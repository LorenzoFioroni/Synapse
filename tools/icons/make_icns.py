"""Draw the macOS icon (frontend/assets/Synapse.icns) used by the desktop shortcut.

    pip install pillow && python tools/icons/make_icns.py

It is the icon of frontend/assets/favicon.svg, drawn the way macOS lays icons out: the rounded
square at 824 of 1024 pixels, centred, with a soft shadow below it, so it sits in the Dock and in
Finder at the size of the other apps. The .icns file holds PNGs (one per size macOS asks for).
Run it again after changing favicon.svg.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "assets" / "Synapse.icns"
RED, WHITE = (204, 41, 54, 255), (255, 255, 255, 255)
SS = 4  # drawn this many times larger, then scaled down (smooth edges)

# .icns entry type → pixel size (the @2x types hold twice their point size)
TYPES = {
    "icp4": 16,
    "icp5": 32,
    "ic11": 32,
    "ic12": 64,
    "ic07": 128,
    "ic13": 256,
    "ic08": 256,
    "ic14": 512,
    "ic09": 512,
    "ic10": 1024,
}


def draw(size: int = 1024) -> Image.Image:
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    box = 824 / 1024 * big  # the rounded square, centred (Apple's icon grid)
    x0 = y0 = (big - box) / 2
    u = box / 100  # one unit of favicon.svg's 100×100 box

    shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x0, y0 + 10 * SS, x0 + box, y0 + box + 10 * SS), 20 * u, fill=(0, 0, 0, 90)
    )
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14 * SS)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0, y0, x0 + box, y0 + box), 20 * u, fill=RED)

    def p(x: float, y: float) -> tuple[float, float]:
        return (x0 + x * u, y0 + y * u)

    # the page: rounded corners but the bottom right one
    d.rounded_rectangle((*p(20, 20), *p(80, 80)), 10 * u, fill=WHITE)
    d.rectangle((*p(70, 70), *p(80, 80)), fill=WHITE)
    for y, x1 in ((35, 75), (50, 75), (65, 55)):  # the lines, 6 units thick with round ends
        d.rounded_rectangle((*p(22, y - 3), *p(x1 + 3, y + 3)), 3 * u, fill=RED)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def icns(master: Image.Image) -> bytes:
    chunks = b""
    for kind, px in TYPES.items():
        buf = io.BytesIO()
        master.resize((px, px), Image.Resampling.LANCZOS).save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        chunks += kind.encode() + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(chunks) + 8) + chunks


if __name__ == "__main__":
    OUT.write_bytes(icns(draw()))
    print(f"wrote {OUT.relative_to(ROOT)}")
