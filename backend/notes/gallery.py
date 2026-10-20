"""Galleries: a fenced block with the info string ``gallery`` holds images and their captions.

    ```gallery|600                      optional width in pixels
    ![](attachments/a.png)              an image starts a panel
    *Clean system.*                     the lines right below it (or the rest of its line) are its caption
                                        a blank line ends a panel's caption
    Photon number versus $N$.           lines not attached to an image: the gallery's caption
    ```

The app shows a gallery as a carousel and exports it as one figure with panels (a), (b), ….
This module reads the blocks for the MCP tools; the frontend's ``gallery.ts`` reads them the same
way for display. The parser (``parser.mask``) decides where a gallery block is.
"""

from __future__ import annotations

import re

from . import frontmatter, parser

_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_WIDTH = re.compile(r"^gallery[\s|]+(?:width\s*=\s*)?(\d+)(?:px)?\b", re.I)
_WRAPPED = re.compile(r"^([*_])(?!\1)([\s\S]*[^\s\\])\1$")


def panel_label(i: int) -> str:
    """ "a", "b", … "z", then "27", "28", …: the label of the i-th panel (0-based)."""
    return chr(97 + i) if i < 26 else str(i + 1)


def _unwrap(caption: str) -> str:
    c = caption.strip()
    m = _WRAPPED.match(c)
    return m.group(2) if m and m.group(1) not in m.group(2) else c


def parse_block(lines: list[str], info: str = "gallery") -> dict:
    """The content lines of one block → {width, caption, panels: [{src, alt, caption, index}]},
    with each panel's 0-based ``index`` into ``lines`` as ``offset``."""
    panels: list[dict] = []
    caption: list[str] = []
    current: list[str] | None = None

    def close() -> None:
        nonlocal current
        if current is not None:
            panels[-1]["caption"] = _unwrap("\n".join(current))
        current = None

    for n, raw in enumerate(lines):
        rest = raw.strip()
        if not rest:
            close()
            continue
        if _IMAGE.match(rest):
            while (m := _IMAGE.match(rest)) is not None:
                close()
                panels.append({"src": m.group(2), "alt": m.group(1).split("|")[0].strip(), "caption": "", "offset": n})
                current = []
                rest = rest[m.end() :].strip()
            if rest and current is not None:
                current.append(rest)
            continue
        if current is not None:
            current.append(rest)
        else:
            caption.append(rest)
    close()
    w = _WIDTH.match(info.strip())
    return {
        "width": int(w.group(1)) if w and int(w.group(1)) > 0 else None,
        "caption": _unwrap("\n".join(caption)),
        "panels": panels,
    }


def galleries(text: str) -> list[dict]:
    """Every gallery of a note: {line (1-based, of the opening fence), width, caption, panels},
    each panel with its 1-based ``line`` and its ``label`` ("a", "b", …)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    _, body, offset = frontmatter.split(text)
    found: list[tuple[int, int | None, str]] = []
    parser.mask(body, found)
    lines = body.split("\n")
    out = []
    for start, end, info in found:
        g = parse_block(lines[start + 1 : end if end is not None else len(lines)], info)
        for i, p in enumerate(g["panels"]):
            p["line"] = start + 2 + p.pop("offset") + offset
            p["label"] = panel_label(i)
        out.append({"line": start + 1 + offset, **g})
    return out
