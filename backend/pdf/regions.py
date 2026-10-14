"""Finding figures, tables and numbered equations on a PDF page, as regions ``[x, y, w, h]``.

Regions are fractions of the page as displayed (origin top left, rotation applied); see render.py.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .render import open_pdf

# ============================================================================ figures
CAPTION = re.compile(r"^\s*(fig(?:ure)?\.?|table)\s*([A-Z]?\d+[a-z]?|[IVX]+)\b\s*[.:|]?", re.I)


def _captions(page) -> list[dict]:
    """Text lines that start a figure or table caption: {"kind", "label", "text", "box"} (PDF units)."""
    tp = page.get_textpage()
    try:
        out = []
        for i in range(tp.count_rects()):
            l, b, r, t = tp.get_rect(i)
            text = tp.get_text_bounded(l, b, r, t).strip()
            m = CAPTION.match(text)
            if not m:
                continue
            # "Fig. 3 shows…" in running text is not a caption: those continue with a lowercase verb
            rest = text[m.end():].lstrip()
            if rest[:1].islower() and not m.group(0).rstrip().endswith((":", ".", "|")):
                continue
            kind = "table" if m.group(1).lower().startswith("tab") else "figure"
            out.append({"kind": kind, "label": f"{'Table' if kind == 'table' else 'Figure'} {m.group(2)}",
                        "text": text[:160], "box": (l, b, r, t)})
        return out
    finally:
        tp.close()


def _objects(page) -> tuple[list[tuple], list[tuple]]:
    """(graphics boxes, text boxes) of the page in PDF units (l, b, r, t)."""
    import pypdfium2.raw as raw
    graphics, texts = [], []
    W, H = page.get_size()
    for obj in page.get_objects(max_depth=2):
        try:
            l, b, r, t = obj.get_bounds()
        except Exception:
            continue
        if r - l <= 0 and t - b <= 0:
            continue
        if obj.type == raw.FPDF_PAGEOBJ_TEXT:
            texts.append((l, b, r, t))
        elif obj.type in (raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_SHADING, raw.FPDF_PAGEOBJ_FORM):
            if (r - l) > 0.9 * W and (t - b) > 0.9 * H:
                continue   # a page background
            graphics.append((l, b, r, t))
    return graphics, texts


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _overlap_x(a, b) -> float:
    return min(a[2], b[2]) - max(a[0], b[0])


def figure_regions(path: Path | str, page_no: int) -> list[dict]:
    """Figure and table captions on a page, each with a proposed region (without the caption)
    when one can be found from what the page draws. Best effort: check it on the page image."""
    doc = open_pdf(path)
    try:
        page = doc[page_no - 1]
        if page.get_rotation() % 360:
            return [{"label": c["label"], "caption": c["text"]} for c in _captions(page)]
        W, H = page.get_size()
        cl, cb, _, ct = page.get_cropbox()
        caps = _captions(page)
        graphics, texts = _objects(page)
    finally:
        doc.close()

    def frac(box) -> list[float]:
        l, b, r, t = box
        l, r = max(l, cl), min(r, cl + W)
        b, t = max(b, cb), min(t, cb + H)
        pad = 3
        x0, x1 = max(0.0, (l - pad - cl) / W), min(1.0, (r + pad - cl) / W)
        y0, y1 = max(0.0, (cb + H - t - pad) / H), min(1.0, (cb + H - b + pad) / H)
        return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]

    out = []
    tops = sorted(c["box"][3] for c in caps)
    for c in caps:
        cl_, cb_, cr_, ct_ = c["box"]
        entry = {"label": c["label"], "caption": c["text"]}
        if c["kind"] == "figure":
            # graphics above the caption, closest first, while they stay close together
            limit = min([t for t in tops if t > ct_ + 1] + [cb + H])   # next caption above, if any
            cand = sorted((g for g in graphics if g[1] >= cb_ - 2 and g[3] <= limit + 2 and g[1] < limit),
                          key=lambda g: g[1])
            box = None
            for g in cand:
                gap = g[1] - (box[3] if box else ct_)
                if gap > 60:
                    break
                if _overlap_x(g, box or c["box"]) < -10:
                    continue   # beside it, e.g. in the other column
                box = g if box is None else _union([box, g])
            if box and (box[2] - box[0]) > 20 and (box[3] - box[1]) > 20:
                # labels and tick numbers drawn as text around the plot
                grow = (box[0] - 15, box[1] - 15, box[2] + 15, box[3] + 15)
                small = [t for t in texts if _overlap_x(t, grow) > 0 and t[1] < grow[3] and t[3] > grow[1]
                         and (t[2] - t[0]) < 0.6 * (box[2] - box[0]) and t[1] >= ct_ - 1]
                if small:
                    box = _union([box] + small)
                entry["rect"] = frac(box)
        else:
            # a table below its caption: down to the last horizontal rule close to the one before
            rules = sorted((g for g in graphics if g[3] <= cb_ + 2 and (g[3] - g[1]) < 3 and (g[2] - g[0]) > 30
                            and _overlap_x(g, c["box"]) > 0), key=lambda g: -g[3])
            last, box = cb_, None
            for g in rules:
                if last - g[3] > 200:
                    break
                box = g if box is None else _union([box, g])
                last = g[1]
            if box:
                body = [t for t in texts if t[3] <= cb_ and t[1] >= box[1] - 1 and _overlap_x(t, box) > 0]
                box = _union([box] + body)
                entry["rect"] = frac(box)
        out.append(entry)
    return out


# ============================================================================ equations
# An equation number: "(3)", "(2.5)", "(A1)", "(S2)", "(3b)", "(4-2)".
EQ_NUMBER = re.compile(r"\(([A-Z]{0,2}\d{1,3}(?:[.\-–]\d{1,3})*[a-z]?)\)")
# Fonts that set mathematics (TeX's Computer Modern / AMS / STIX / Cambria Math / Symbol …).
MATH_FONT = re.compile(r"cmmi|cmsy|cmex|cmbsy|cmmib|msam|msbm|eufm|eusm|rsfs|math|stix|symbol|esint|wasy|"
                       r"bbm|dsrom|(?:tx|px|lm|mt|ntx|zx)(?:mi|sy|ex)|\+?(?:mi|sy|ex)\d|euler", re.I)


def _chars(page) -> list[tuple]:
    """The page's visible characters in reading order: (char, l, b, r, t, line, math), PDF units, tight boxes."""
    import ctypes

    import pypdfium2.raw as raw
    tp = page.get_textpage()
    try:
        text = tp.get_text_range()
        buf, flags = ctypes.create_string_buffer(128), ctypes.c_int()
        fonts: dict[bytes, bool] = {}
        out, line = [], 0
        for i in range(min(len(text), tp.count_chars())):
            ch = text[i]
            if ch == "\n":
                line += 1
            if ch.isspace() or ch == "￾":
                continue
            l, b, r, t = tp.get_charbox(i)
            if r - l <= 0 and t - b <= 0:
                continue
            n = raw.FPDFText_GetFontInfo(tp.raw, i, buf, 128, ctypes.byref(flags))
            name = buf.value if n else b""
            if name not in fonts:
                fonts[name] = bool(MATH_FONT.search(name.decode("latin-1")))
            out.append((ch, l, b, r, t, line, fonts[name]))
        return out
    finally:
        tp.close()


def _box(chars) -> tuple:
    return (min(c[1] for c in chars), min(c[2] for c in chars), max(c[3] for c in chars), max(c[4] for c in chars))


def _bands(chars: list[tuple]) -> list[list[tuple]]:
    """Group characters into horizontal bands, top first: characters whose boxes overlap vertically
    (an equation with a fraction or big brackets forms one band, sub/superscripts join their line)."""
    bands: list[list] = []   # [bottom, chars]
    for c in sorted(chars, key=lambda c: -c[4]):
        if bands and c[4] > bands[-1][0] + 0.3:
            bands[-1][0] = min(bands[-1][0], c[2])
            bands[-1][1].append(c)
        else:
            bands.append([c[2], [c]])
    return [b[1] for b in bands]


def _mathiness(band) -> float:
    """Share of the letters and symbols of a band that are set as mathematics."""
    math = sum(1 for c in band if c[6] or unicodedata.category(c[0]) == "Sm" or "Ͱ" <= c[0] <= "Ͽ")
    letters = sum(1 for c in band if c[0].isalpha() and not c[6])
    return math / max(1, math + letters)


def _equation_numbers(chars: list[tuple]) -> list[dict]:
    """Equation numbers: "(N)" set apart at the end of a line, with nothing after it in its column.
    "see Eq. (3)." in running text has only a word space before it, or more text after it."""
    out = []
    s = "".join(c[0] for c in chars)
    for m in EQ_NUMBER.finditer(s):
        num = chars[m.start():m.end()]
        if any(c[6] for c in num):
            continue   # "(x)" inside a formula
        mine = {id(c) for c in num}
        l, b, r, t = _box(num)
        h = max(t - b, 1.0)
        em = max(6.0, h / 0.75)   # parentheses span ~0.75 em
        row = [c for c in chars if id(c) not in mine and min(c[4], t) - max(c[2], b) > 0.3 * min(h, (c[4] - c[2]) or h)]
        gap_left = l - max((c[3] for c in row if c[3] <= l + 0.5), default=-1e9)
        gap_right = min((c[1] for c in row if c[1] >= r - 0.5), default=1e9) - r
        if gap_left >= 0.8 * em and gap_right >= 1.0 * em:
            out.append({"number": m.group(1), "box": (l, b, r, t), "em": em, "chars": num})
    return out


def _column_lefts(chars: list[tuple]) -> list[float]:
    """Where the columns' lines start: left edges shared by at least three text lines (paragraph
    indents fold into their column's edge)."""
    starts: dict[int, float] = {}
    count: dict[int, int] = {}
    for c in chars:
        starts[c[5]] = min(starts.get(c[5], 1e9), c[1])
        count[c[5]] = count.get(c[5], 0) + 1
    xs = sorted(x for k, x in starts.items() if count[k] >= 12)
    groups: list[list[float]] = []
    for x in xs:
        if groups and x - groups[-1][-1] <= 2:
            groups[-1].append(x)
        else:
            groups.append([x])
    edges = sorted(g[0] for g in groups if len(g) >= 3)
    merged: list[float] = []
    for x in edges:
        if not merged or x - merged[-1] > 30:
            merged.append(x)
    return merged


def _prose_lines(chars: list[tuple]) -> dict[int, list[tuple]]:
    """pdfium text lines that look like running text: at least 12 characters, mostly not math."""
    lines: dict[int, list[tuple]] = {}
    for c in chars:
        lines.setdefault(c[5], []).append(c)
    return {k: v for k, v in lines.items() if len(v) >= 12 and _mathiness(v) < 0.3}


def _column(prose: dict[int, list[tuple]], lefts: list[float], num: dict) -> tuple[float, float]:
    """Left and right edge of the column holding an equation number. A gutter is an empty strip
    (8 pt or more) in the running text near the number with running text on both sides; without
    one, the column starts at the nearest common line start (or the text) left of the number."""
    l, b, r, t = num["box"]
    near = [c for v in prose.values() for c in v if c[2] < t + 250 and c[4] > b - 250 and c[3] <= l - 2]
    spans: list[list] = []   # [from, to, chars]
    for c in sorted(near, key=lambda c: c[1]):
        if spans and c[1] <= spans[-1][1] + 8:
            spans[-1][1] = max(spans[-1][1], c[3])
            spans[-1][2] += 1
        else:
            spans.append([c[1], c[3], 1])
    for k in range(len(spans) - 1, 0, -1):
        if sum(sp[2] for sp in spans[k:]) >= 10 and sum(sp[2] for sp in spans[:k]) >= 10:
            return spans[k][0], r
    below = [x for x in lefts if x < l - 20]
    if below:
        return below[-1], r
    return (spans[0][0] if spans else l), r


def _frac(page, box) -> list[float]:
    W, H = page.get_size()
    cl, cb, _, _ = page.get_cropbox()
    l, b, r, t = box
    pad = 3
    x0, x1 = max(0.0, (l - pad - cl) / W), min(1.0, (r + pad - cl) / W)
    y0, y1 = max(0.0, (cb + H - t - pad) / H), min(1.0, (cb + H - b + pad) / H)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def _page_equations(page) -> list[dict]:
    """Each numbered display equation: its number's band, grown up and down through the lines of
    displayed math next to it (indented from both sides, or mostly set in math fonts), stopping at
    prose, at a wide gap and at the lines of another number. Equations of one column that would
    share lines are split at the widest gap between them."""
    if page.get_rotation() % 360:
        return []
    chars = _chars(page)
    nums = _equation_numbers(chars)
    if not nums:
        return []
    graphics, _ = _objects(page)
    lefts = _column_lefts(chars)
    prose = _prose_lines(chars)
    columns: dict[tuple, list[dict]] = {}
    for n in nums:
        cl, cr = _column(prose, lefts, n)
        n["col"] = (cl, cr)
        key = next((k for k in columns if abs(k[0] - cl) < 3 and abs(k[1] - cr) < 15), (cl, cr))
        columns.setdefault(key, []).append(n)
    out = []
    for group in columns.values():
        col_l = min(n["col"][0] for n in group)
        col_r = max(n["col"][1] for n in group)
        bands = _bands([c for c in chars if c[1] >= col_l - 1 and c[3] <= col_r + 1])
        boxes = [_box(b) for b in bands]
        where = {id(c): i for i, b in enumerate(bands) for c in b}
        starts = {}
        for n in group:
            starts.setdefault(where[id(n["chars"][0])], n)
        numbered = set(starts)

        def gap(i: int) -> float:   # between band i and band i + 1 below it
            return boxes[i][1] - boxes[i + 1][3]

        def display(i: int, em: float) -> bool:
            if i in numbered:
                return False
            bl, _, br, _ = boxes[i]
            return (bl - col_l >= 1.5 * em and col_r - br >= 1.5 * em) or _mathiness(bands[i]) >= 0.35

        spans = []
        for s, n in sorted(starts.items()):
            em, lo, hi = n["em"], s, s
            while lo > 0 and gap(lo - 1) <= max(14.0, 1.4 * em) and display(lo - 1, em):
                lo -= 1
            while hi + 1 < len(bands) and gap(hi) <= max(14.0, 1.4 * em) and display(hi + 1, em):
                hi += 1
            spans.append([lo, hi, s, n])
        for a, b in zip(spans, spans[1:]):
            if a[1] >= b[0]:   # they meet: cut at the widest gap between the two numbers
                cut = max(range(a[2], b[2]), key=gap)
                a[1], b[0] = cut, cut + 1
        for lo, hi, _s, n in spans:
            box = _union(boxes[lo:hi + 1])
            for g in graphics:   # fraction bars, root signs and rules drawn as paths
                if g[0] >= col_l - 1 and g[2] <= col_r + 1 and g[1] >= box[1] - 2 and g[3] <= box[3] + 2:
                    box = _union([box, g])
            out.append({"label": f"Equation ({n['number']})", "number": n["number"], "rect": _frac(page, box),
                        "_y": -box[3]})
    seen, result = set(), []
    for e in sorted(out, key=lambda e: e.pop("_y")):
        if e["number"] not in seen:
            seen.add(e["number"])
            result.append(e)
    return result


def equation_regions(path: Path | str, page_no: int) -> list[dict]:
    """Numbered display equations on a page: {"label": "Equation (3)", "number": "3", "rect"}, the
    rect covering the equation's lines and its number. Best effort: check it on the page image."""
    doc = open_pdf(path)
    try:
        return _page_equations(doc[page_no - 1])
    finally:
        doc.close()


def parse_label(label: str) -> tuple[str, str] | None:
    """("equation", "3") for "Eq. (3)", "equation 3", "(3)"; ("figure", "2") / ("table", "1") for
    "Fig. 2", "Figure 2", "Table I". None when it is none of these."""
    s = " ".join(str(label or "").split())
    m = re.fullmatch(r"(?:eqs?\.?|equations?)?\s*\(?\s*([A-Z]{0,2}\d{1,3}(?:[.\-–]\d{1,3})*[a-z]?)\s*\)?", s, re.I)
    if m and (re.match(r"eq", s, re.I) or s.startswith("(")):
        return "equation", m.group(1)
    m = re.fullmatch(r"(fig(?:ure)?s?\.?|tab(?:le)?s?\.?)\s*([A-Z]?\d+[a-z]?|[IVX]+)\.?", s, re.I)
    if m:
        return ("table" if m.group(1).lower().startswith("tab") else "figure"), m.group(2)
    return None


def page_regions(path: Path | str, page_no: int) -> list[dict]:
    """Figures, tables and numbered equations of a page (see figure_regions, equation_regions)."""
    return figure_regions(path, page_no) + equation_regions(path, page_no)
