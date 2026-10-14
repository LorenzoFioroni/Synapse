"""Converting the annotations of older versions, once.

They stored the raw SVG markup of each page in one blob per item (``papers.annotations``).
``migrate_legacy`` parses those blobs into annotation records (see ``annotations.py``), after a
backup, and leaves the old column untouched. The PDF's page sizes and rotation are needed to
turn SVG coordinates into page fractions, and the words under a highlight are read from the PDF.
"""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser

from .annotations import COLOR_RE, AnnotationError, AnnotationStore
from .db import utcnow

log = logging.getLogger(__name__)


# ============================================================================ legacy SVG
class _Element:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent, self.text = tag, attrs, [], parent, []


class _SvgFragment(HTMLParser):
    """Tolerant parser for the innerHTML of an annotation layer (SVG with HTML inside
    foreignObject, as the browser serialised it)."""

    VOID = {"br", "img", "input", "hr", "wbr", "meta", "link"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        el = _Element(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(el)
        if tag == "br":
            self.cur.text.append("\n")
        if tag not in self.VOID:
            self.cur = el

    def handle_startendtag(self, tag, attrs):
        el = _Element(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(el)
        if tag == "br":
            self.cur.text.append("\n")

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.text.append(data)
        self.cur.children.append(data)


def _text_of(el) -> str:
    """innerText-like: block children (div, p) start a new line."""
    parts = []
    for ch in el.children:
        if isinstance(ch, str):
            parts.append(ch)
        elif ch.tag == "br":
            parts.append("\n")
        else:
            inner = _text_of(ch)
            if ch.tag in ("div", "p") and parts and not "".join(parts).endswith("\n"):
                parts.append("\n")
            parts.append(inner)
    return "".join(parts)


def _f(v, default=0.0) -> float:
    try:
        return float(str(v).replace("px", "").strip())
    except (TypeError, ValueError):
        return default


def _style(s: str) -> dict:
    out = {}
    for part in (s or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _hex(color: str | None, default: str) -> str:
    c = (color or "").strip().lower()
    if COLOR_RE.match(c):
        return c
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", c)
    if m:
        return "#" + "".join(f"{min(255, int(g)):02x}" for g in m.groups())
    if re.fullmatch(r"#[0-9a-f]{3}", c):
        return "#" + "".join(ch * 2 for ch in c[1:])
    named = {"red": "#ff0000", "yellow": "#ffff00", "black": "#000000", "blue": "#0000ff", "green": "#008000"}
    return named.get(c, default)


_NUM = r"-?\d+(?:\.\d+)?(?:e-?\d+)?"


def _path_points(d: str) -> list[tuple[float, float]]:
    nums = [float(n) for n in re.findall(_NUM, d or "", re.I)]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def parse_legacy_page(markup: str, width: float, height: float) -> tuple[list[dict], list[str]]:
    """Annotations (fields for :meth:`AnnotationStore.create`, without page) from one page's old
    SVG markup, whose coordinates are PDF points at 100 % zoom; and what could not be read."""
    p = _SvgFragment()
    p.feed(markup or "")
    p.close()
    W, H = float(width), float(height)
    fx = lambda v: _f(v) / W  # noqa: E731
    fy = lambda v: _f(v) / H  # noqa: E731
    out, skipped = [], []

    def rect_of(el):
        return [fx(el.attrs.get("x")), fy(el.attrs.get("y")), fx(el.attrs.get("width")), fy(el.attrs.get("height"))]

    for el in p.root.children:
        if isinstance(el, str):
            continue
        cls = el.attrs.get("class", "")
        if "selection-box" in cls:
            continue
        try:
            if el.tag == "g":
                kids = [k for k in el.children if not isinstance(k, str)]
                lines = [k for k in kids if k.tag == "line"]
                rects = [k for k in kids if k.tag == "rect"]
                if lines:  # underline: a line per text line plus a transparent hit box
                    boxes = [rect_of(r) for r in rects if r.attrs.get("fill") == "transparent"]
                    if not boxes:  # no hit boxes: rebuild from the lines (1 em tall)
                        boxes = [
                            [
                                fx(min(_f(l.attrs.get("x1")), _f(l.attrs.get("x2")))),
                                fy(_f(l.attrs.get("y1")) - 11),
                                fx(abs(_f(l.attrs.get("x2")) - _f(l.attrs.get("x1")))),
                                fy(11),
                            ]
                            for l in lines
                        ]
                    out.append(
                        {
                            "type": "underline",
                            "rects": boxes,
                            "color": _hex(lines[0].attrs.get("stroke"), "#ff0000"),
                            "size": _f(lines[0].attrs.get("stroke-width"), 2) or 2,
                        }
                    )
                elif rects:
                    fill = next(
                        (r.attrs.get("fill") for r in rects if r.attrs.get("fill") not in ("transparent", "none")), None
                    )
                    out.append(
                        {
                            "type": "highlight",
                            "rects": [rect_of(r) for r in rects if r.attrs.get("fill") != "transparent"],
                            "color": _hex(fill, "#ffff00"),
                        }
                    )
                else:
                    skipped.append("an empty group")
            elif el.tag == "rect":
                out.append(
                    {
                        "type": "box",
                        "rects": [rect_of(el)],
                        "color": _hex(el.attrs.get("stroke"), "#000000"),
                        "size": _f(el.attrs.get("stroke-width"), 2) or 2,
                    }
                )
            elif el.tag == "path":
                pts = _path_points(el.attrs.get("d", ""))
                if not pts:
                    skipped.append("a drawing without points")
                    continue
                out.append(
                    {
                        "type": "ink",
                        "path": [[x / W, y / H] for x, y in pts],
                        "color": _hex(el.attrs.get("stroke"), "#000000"),
                        "size": _f(el.attrs.get("stroke-width"), 2) or 2,
                    }
                )
            elif el.tag == "foreignobject":
                editable = _find(el, lambda e: "contenteditable" in e.attrs)
                text = _text_of(editable or el).strip("\n")
                if not text.strip():
                    continue  # an empty text box is deleted by the viewer anyway
                st = _style((editable or el).attrs.get("style", ""))
                size = _f(st.get("font-size"), 16) or 16
                lines = text.split("\n")
                w = max(len(line) for line in lines) * size * 0.55 + 8
                h = len(lines) * size * 1.2 + 8
                out.append(
                    {
                        "type": "text",
                        "rects": [[fx(el.attrs.get("x")), fy(el.attrs.get("y")), w / W, h / H]],
                        "text": text,
                        "color": _hex(st.get("color"), "#000000"),
                        "size": size,
                    }
                )
            else:
                skipped.append(f"an unknown <{el.tag}> element")
        except Exception as e:  # one bad element never stops the rest
            skipped.append(f"a <{el.tag}> element ({e})")
    return out, skipped


def _find(el, pred):
    for ch in el.children:
        if isinstance(ch, str):
            continue
        if pred(ch):
            return ch
        hit = _find(ch, pred)
        if hit:
            return hit
    return None


# ============================================================================ page geometry
def page_sizes(path) -> list[tuple[float, float, int, tuple]]:
    """(width, height, rotation, cropbox) of each page as displayed (rotation applied), in points."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        out = []
        for i in range(len(doc)):
            pg = doc[i]
            w, h = pg.get_size()
            out.append((w, h, pg.get_rotation() % 360, tuple(pg.get_cropbox())))
        return out
    finally:
        doc.close()


def to_pdf(u: float, v: float, rotation: int, crop: tuple) -> tuple[float, float]:
    """A point of the displayed page (points, origin top left) in PDF user space."""
    x0, y0, x1, y1 = crop
    if rotation == 90:
        return x0 + v, y0 + u
    if rotation == 180:
        return x1 - u, y0 + v
    if rotation == 270:
        return x1 - v, y1 - u
    return x0 + u, y1 - v


def text_under(path, page: int, rects: list[list[float]]) -> str:
    """The words of the PDF inside the rects (fractions of the displayed page), line by line."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        pg = doc[page - 1]
        w, h = pg.get_size()
        rot, crop = pg.get_rotation() % 360, tuple(pg.get_cropbox())
        tp = pg.get_textpage()
        parts = []
        for x, y, rw, rh in rects:
            # a little inside the box vertically, so the lines above and below are not caught
            pad = rh * h * 0.2
            a = to_pdf(x * w, y * h + pad, rot, crop)
            b = to_pdf((x + rw) * w, (y + rh) * h - pad, rot, crop)
            left, right = sorted((a[0], b[0]))
            bottom, top = sorted((a[1], b[1]))
            parts.append(tp.get_text_bounded(left=left, bottom=bottom, right=right, top=top).strip())
        tp.close()
        return " ".join(p for p in parts if p).replace("\r\n", " ").replace("\n", " ").strip()
    finally:
        doc.close()


# ============================================================================ migration
def migrate_legacy(services) -> dict:
    """Convert the old per-item SVG blobs into records, once, after a backup. Returns a summary."""
    store: AnnotationStore = services.annotations
    pending = store.pending_legacy()
    if not pending:
        return {"items": 0, "converted": 0, "skipped": 0}
    try:
        import pypdfium2  # noqa: F401  (page sizes; without it, try again on a later start)
    except ImportError:
        log.warning("Old annotations not converted yet: pypdfium2 is missing (pip install -r requirements.txt)")
        return {"items": 0, "converted": 0, "skipped": 0, "waiting": len(pending)}
    try:
        services.backups.create("before converting annotations")
    except Exception:
        log.exception("Backup before converting annotations failed; converting anyway (the old data stays)")
    total, skipped_total = 0, 0
    for pid in pending:
        converted, skipped, att_id = 0, [], None
        try:
            with services.db.session() as c:
                blob = json.loads(c.execute("SELECT annotations FROM papers WHERE id = ?", (pid,)).fetchone()[0])
                atts = c.execute(
                    "SELECT id, url, type FROM attachments WHERE paper_id = ? ORDER BY id", (pid,)
                ).fetchall()
            pdfs = [
                a
                for a in atts
                if services.papers.attachment_path(dict(a)) is not None and str(a["url"]).lower().endswith(".pdf")
            ] or [a for a in atts if services.papers.attachment_path(dict(a)) is not None]
            if not pdfs:
                skipped.append("the item has no PDF file to put the annotations on")
            else:
                att = dict(pdfs[0])
                att_id = att["id"]
                path = services.papers.attachment_path(att)
                sizes = page_sizes(path)
                with services.db.session() as c:
                    for key, markup in sorted(blob.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
                        if not str(markup or "").strip():
                            continue
                        page = int(key)
                        if not 1 <= page <= len(sizes):
                            skipped.append(f"page {page} does not exist in the PDF")
                            continue
                        w, h, _, _ = sizes[page - 1]
                        items, bad = parse_legacy_page(markup, w, h)
                        skipped += [f"page {page}: {b}" for b in bad]
                        for a in items:
                            if a["type"] in ("highlight", "underline") and not a.get("text"):
                                try:
                                    a["text"] = text_under(path, page, a["rects"])
                                except Exception:
                                    a["text"] = ""
                            try:
                                store.create(att_id, {**a, "page": page}, conn=c)
                                converted += 1
                            except AnnotationError as e:
                                skipped.append(f"page {page}: a {a['type']} ({e})")
        except Exception as e:
            log.exception("Converting the annotations of %s failed", pid)
            skipped.append(f"conversion failed: {e}")
        with services.db.session() as c:
            c.execute(
                "INSERT OR REPLACE INTO annotation_migration VALUES (?, ?, ?, ?, ?)",
                (pid, att_id, converted, json.dumps(skipped), utcnow()),
            )
        if skipped:
            log.warning("Annotations of item %s: %s converted, not converted: %s", pid, converted, "; ".join(skipped))
        total += converted
        skipped_total += len(skipped)
    log.info("Converted %s annotations of %s items (%s parts skipped)", total, len(pending), skipped_total)
    return {"items": len(pending), "converted": total, "skipped": skipped_total}
