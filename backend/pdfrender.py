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
        for x in range(x0, x0 + 8 + 16 * len(str(n))):
            for y in range(y0, y0 + 26):
                _put(img, w, h, x, y, red)
        _text(img, w, h, x0 + 5, y0 + 5, str(n), (255, 255, 255), size=3)
    return png_encode(img, w, h)
