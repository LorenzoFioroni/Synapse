"""Rendering PDF pages to PNG (for the MCP tools ``view_page`` and ``snapshot``).

Uses pypdfium2 (Chrome's PDF engine); it is optional, and ``available()`` says
whether it is installed. PNGs are encoded here with zlib, so Pillow is not needed.

Regions are ``[x, y, w, h]`` as fractions of the page as it is displayed (origin at
the top left, rotation applied), the same convention the PDF viewer uses for
snapshots (``rect=`` in its URL and the ``zc:pdf-region`` PNG chunk).
"""
from __future__ import annotations

import json
import re
import struct
import unicodedata
import zlib
from pathlib import Path

REGION_KEYWORD = "zc:pdf-region"   # same as frontend/js/views/notes/citeTarget.js


def available() -> bool:
    try:
        import pypdfium2  # noqa: F401
        return True
    except ImportError:
        return False


MISSING = ("Viewing and snapshotting pages needs the pypdfium2 package. Install it in the app's "
           "environment (pip install pypdfium2, or pip install -r requirements.txt) and restart Claude.")


# ============================================================================ PNG
def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_encode(rgb: bytes | bytearray, width: int, height: int, text: dict[str, str] | None = None) -> bytes:
    """An RGB PNG; ``text`` adds tEXt chunks (latin-1) just before IEND."""
    row = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgb[y * row:(y + 1) * row]
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    out += _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    for k, v in (text or {}).items():
        out += _chunk(b"tEXt", k.encode("latin-1") + b"\0" + v.encode("latin-1"))
    return out + _chunk(b"IEND", b"")


def read_region(png: bytes) -> dict | None:
    """The region stored in a PNG by ``snapshot`` (or by the viewer), or None."""
    if not png.startswith(b"\x89PNG"):
        return None
    i = 8
    while i + 12 <= len(png):
        n = struct.unpack(">I", png[i:i + 4])[0]
        kind, data = png[i + 4:i + 8], png[i + 8:i + 8 + n]
        if kind == b"tEXt" and data.startswith(REGION_KEYWORD.encode() + b"\0"):
            try:
                return json.loads(data.split(b"\0", 1)[1].decode("latin-1"))
            except ValueError:
                return None
        i += 12 + n
    return None


# ============================================================================ rendering
def _open(path: Path | str):
    import pypdfium2 as pdfium
    return pdfium.PdfDocument(str(path))


def _render_rgb(page, scale: float, crop=(0, 0, 0, 0)) -> tuple[bytearray, int, int]:
    import pypdfium2.raw as raw
    bm = page.render(scale=scale, crop=crop, rev_byteorder=True, force_bitmap_format=raw.FPDFBitmap_BGR,
                     fill_color=(255, 255, 255, 255))
    w, h, stride = bm.width, bm.height, bm.stride
    buf = bytes(bm.buffer)
    if stride == w * 3:
        return bytearray(buf[:w * h * 3]), w, h
    out = bytearray()
    for y in range(h):
        out += buf[y * stride:y * stride + w * 3]
    return out, w, h


def page_count(path: Path | str) -> int:
    doc = _open(path)
    try:
        return len(doc)
    finally:
        doc.close()


def render_region(path: Path | str, page_no: int, rect: list[float], *, scale: float = 3.0,
                  max_side: int = 2400) -> tuple[bytes, int, int]:
    """PNG of a region (fractions of the displayed page) with the region recorded in it."""
    doc = _open(path)
    try:
        page = doc[page_no - 1]
        W, H = page.get_size()
        x, y, w, h = rect
        scale = min(scale, max_side / max(1.0, w * W), max_side / max(1.0, h * H))
        crop = (x * W, (1 - y - h) * H, (1 - x - w) * W, y * H)
        rgb, pw, ph = _render_rgb(page, scale, crop)
    finally:
        doc.close()
    region = {"page": page_no, "rect": [round(v, 4) for v in rect]}
    return png_encode(rgb, pw, ph, {REGION_KEYWORD: json.dumps(region, separators=(",", ":"))}), pw, ph


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
    doc = _open(path)
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
        for lo, hi, s, n in spans:
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
    doc = _open(path)
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


# ============================================================================ page view
_GLYPHS = {   # 3×5 pixel digits for the grid labels
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
    "8": "111101111101111", "9": "111101111001111", ".": "000000000000010",
}


def _put(img, w, h, x, y, color):
    if 0 <= x < w and 0 <= y < h:
        i = (y * w + x) * 3
        img[i:i + 3] = bytes(color)


def _blend(img, w, h, x, y, color, a):
    if 0 <= x < w and 0 <= y < h:
        i = (y * w + x) * 3
        for k in range(3):
            img[i + k] = int(img[i + k] * (1 - a) + color[k] * a)


def _text(img, w, h, x, y, s, color, size=3):
    for ch in s:
        g = _GLYPHS.get(ch)
        if g:
            for n, bit in enumerate(g):
                if bit == "1":
                    gx, gy = n % 3, n // 3
                    for dx in range(size):
                        for dy in range(size):
                            _put(img, w, h, x + gx * size + dx, y + gy * size + dy, color)
        x += 4 * size


def page_view(path: Path | str, page_no: int, regions: list[dict] | None = None, *, long_side: int = 1400) -> bytes:
    """PNG of a whole page with a 0–1 grid (every 0.1, labelled in the margins) and the given
    regions outlined in red with their number."""
    doc = _open(path)
    try:
        page = doc[page_no - 1]
        W, H = page.get_size()
        scale = long_side / max(W, H)
        rgb, pw, ph = _render_rgb(page, scale)
    finally:
        doc.close()
    m = 40   # margin for the labels
    w, h = pw + m + 14, ph + m + 14   # room for the last labels too
    img = bytearray(b"\xff" * (w * h * 3))
    for y in range(ph):
        img[((y + m) * w + m) * 3:((y + m) * w + m + pw) * 3] = rgb[y * pw * 3:(y + 1) * pw * 3]
    grid, ink = (0, 120, 255), (60, 60, 60)
    for k in range(11):
        gx, gy = m + min(pw - 1, round(k / 10 * pw)), m + min(ph - 1, round(k / 10 * ph))
        a = 0.45 if k in (0, 5, 10) else 0.25
        for y in range(m, h):
            _blend(img, w, h, gx, y, grid, a)
        for x in range(m, w):
            _blend(img, w, h, x, gy, grid, a)
        label = "0" if k == 0 else "1" if k == 10 else f".{k}"
        _text(img, w, h, gx - (len(label) * 16) // 2, 8, label, ink, size=4)
        _text(img, w, h, 3, gy - 10, label, ink, size=4)
    red = (220, 30, 30)
    for n, r in enumerate(regions or [], 1):
        x0, y0 = m + round(r["rect"][0] * pw), m + round(r["rect"][1] * ph)
        x1, y1 = m + round((r["rect"][0] + r["rect"][2]) * pw), m + round((r["rect"][1] + r["rect"][3]) * ph)
        for t in range(3):
            for x in range(x0, x1 + 1):
                _put(img, w, h, x, y0 + t, red)
                _put(img, w, h, x, y1 - t, red)
            for y in range(y0, y1 + 1):
                _put(img, w, h, x0 + t, y, red)
                _put(img, w, h, x1 - t, y, red)
        ly = y0 - 22 if y0 - 22 >= m else y0   # the number above the box, so it hides nothing
        for x in range(x0, x0 + 8 + 16 * len(str(n))):
            for y in range(ly, ly + 22):
                _put(img, w, h, x, y, red)
        _text(img, w, h, x0 + 5, ly + 4, str(n), (255, 255, 255), size=3)
    return png_encode(img, w, h)
