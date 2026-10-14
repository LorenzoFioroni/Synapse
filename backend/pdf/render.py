"""Rendering PDF pages to PNG (for the MCP tools ``view_page`` and ``snapshot``).

Uses pypdfium2 (Chrome's PDF engine); it is optional, and ``available()`` says
whether it is installed. PNGs are encoded here with zlib, so Pillow is not needed.

Regions are ``[x, y, w, h]`` as fractions of the page as it is displayed (origin at
the top left, rotation applied), the same convention the PDF viewer uses for
snapshots (``rect=`` in its URL and the ``zc:pdf-region`` PNG chunk).
"""
from __future__ import annotations

import json
from pathlib import Path

from .png import REGION_KEYWORD, png_encode


def available() -> bool:
    try:
        import pypdfium2  # noqa: F401
        return True
    except ImportError:
        return False


MISSING = ("Viewing and snapshotting pages needs the pypdfium2 package. Install it in the app's "
           "environment (pip install pypdfium2, or pip install -r requirements.txt) and restart Claude.")


def open_pdf(path: Path | str):
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
    doc = open_pdf(path)
    try:
        return len(doc)
    finally:
        doc.close()


def render_region(path: Path | str, page_no: int, rect: list[float], *, scale: float = 3.0,
                  max_side: int = 2400) -> tuple[bytes, int, int]:
    """PNG of a region (fractions of the displayed page) with the region recorded in it."""
    doc = open_pdf(path)
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
    doc = open_pdf(path)
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
