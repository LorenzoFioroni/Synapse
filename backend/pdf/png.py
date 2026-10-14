"""PNG files written without Pillow (zlib only), carrying the PDF region they show.

A snapshot of a PDF region stores where it came from in a ``zc:pdf-region`` text chunk, so a
note's image can be traced back to its page (``read_region``).
"""

from __future__ import annotations

import json
import struct
import zlib

REGION_KEYWORD = "zc:pdf-region"  # same as frontend/js/views/notes/citeTarget.js


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_encode(rgb: bytes | bytearray, width: int, height: int, text: dict[str, str] | None = None) -> bytes:
    """An RGB PNG; ``text`` adds tEXt chunks (latin-1) just before IEND."""
    row = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgb[y * row : (y + 1) * row]
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
        n = struct.unpack(">I", png[i : i + 4])[0]
        kind, data = png[i + 4 : i + 8], png[i + 8 : i + 8 + n]
        if kind == b"tEXt" and data.startswith(REGION_KEYWORD.encode() + b"\0"):
            try:
                return json.loads(data.split(b"\0", 1)[1].decode("latin-1"))
            except ValueError:
                return None
        i += 12 + n
    return None
