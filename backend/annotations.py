"""PDF annotations as records (issue #20).

Each highlight, underline, box, drawing or text box is one row of the ``annotations`` table in
``library.db``, tied to the **attachment** it was made on (not to the item, so two PDFs of one
item keep separate annotations) and to a page. Positions are fractions of the page as it is
displayed (0–1, origin top left, rotation applied) — the same convention as snapshot regions
(``rect=`` in viewer URLs, ``zc:pdf-region`` in PNGs) — so they do not depend on zoom.

=========  ===================================================================  =============
type       geometry                                                             ``size``
=========  ===================================================================  =============
highlight  ``rects``: one ``[x, y, w, h]`` per line of text                       —
underline  ``rects``: the text lines; the line is drawn along each bottom edge    stroke width
box        ``rects``: ``[[x, y, w, h]]``                                         stroke width
ink        ``path``: ``[[x, y], …]``                                             stroke width
text       ``rects``: ``[[x, y, w, h]]`` (top left; w, h as last drawn)           font size
=========  ===================================================================  =============

Sizes are in PDF points at 100 % zoom. ``text`` is the highlighted or underlined words, or the
content of a text box; ``comment`` is free text.

Older versions stored the raw SVG markup of each page in one blob per item
(``papers.annotations``). :func:`migrate_legacy` converts those blobs once, after a backup,
and leaves the old column untouched.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from html.parser import HTMLParser

from .db import utcnow

log = logging.getLogger(__name__)

TYPES = ("highlight", "underline", "box", "ink", "text")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FIELDS = ("page", "type", "rects", "path", "text", "color", "size", "comment")
MAX_TEXT = 100_000


class AnnotationError(ValueError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    attachment_id INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    type TEXT NOT NULL,
    rects TEXT,
    path TEXT,
    text TEXT,
    color TEXT,
    size REAL,
    comment TEXT,
    created TEXT,
    modified TEXT
);
CREATE INDEX IF NOT EXISTS idx_annotations_attachment ON annotations(attachment_id);
CREATE TABLE IF NOT EXISTS annotation_migration (
    paper_id TEXT PRIMARY KEY,
    attachment_id INTEGER,
    converted INTEGER,
    skipped TEXT,
    at TEXT
);
"""


# ============================================================================ validation
def _num(v, lo=-0.5, hi=1.5) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise AnnotationError("Coordinates must be numbers.")
    if not (lo <= f <= hi) or f != f:
        raise AnnotationError("Coordinates are fractions of the page (0 to 1).")
    return round(f, 6)


def _rects(v) -> list[list[float]]:
    if not isinstance(v, list) or not v or len(v) > 500:
        raise AnnotationError("rects must be a list of [x, y, w, h].")
    out = []
    for r in v:
        if not isinstance(r, (list, tuple)) or len(r) != 4:
            raise AnnotationError("Each rect is [x, y, w, h].")
        x, y, w, h = (_num(c) for c in r)
        if w < 0 or h < 0:
            raise AnnotationError("Width and height cannot be negative.")
        out.append([x, y, w, h])
    return out


def _path(v) -> list[list[float]]:
    if not isinstance(v, list) or len(v) < 1 or len(v) > 20_000:
        raise AnnotationError("path must be a list of [x, y] points.")
    out = []
    for p in v:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise AnnotationError("Each point is [x, y].")
        out.append([_num(p[0]), _num(p[1])])
    return out


def clean(data: dict, partial: bool = False) -> dict:
    """Validated fields of an annotation (all required ones unless ``partial``)."""
    out = {}
    if "type" in data or not partial:
        if data.get("type") not in TYPES:
            raise AnnotationError(f"type must be one of {', '.join(TYPES)}.")
        out["type"] = data["type"]
    if "page" in data or not partial:
        try:
            page = int(data.get("page"))
        except (TypeError, ValueError):
            raise AnnotationError("page must be a page number (1, 2, …).")
        if page < 1:
            raise AnnotationError("page must be a page number (1, 2, …).")
        out["page"] = page
    if data.get("rects") is not None:
        out["rects"] = _rects(data["rects"])
    if data.get("path") is not None:
        out["path"] = _path(data["path"])
    for key in ("text", "comment"):
        if key in data:
            v = "" if data[key] is None else str(data[key])
            if len(v) > MAX_TEXT:
                raise AnnotationError(f"{key} is too long.")
            out[key] = v
    if "color" in data:
        if data["color"] is not None and not COLOR_RE.match(str(data["color"])):
            raise AnnotationError("color must look like #ffcc00.")
        out["color"] = data["color"]
    if "size" in data:
        try:
            size = float(data["size"]) if data["size"] is not None else None
        except (TypeError, ValueError):
            raise AnnotationError("size must be a number.")
        if size is not None and not 0 < size <= 400:
            raise AnnotationError("size must be between 0 and 400.")
        out["size"] = size
    kind = out.get("type")
    if kind and not partial:
        if kind == "ink" and "path" not in out:
            raise AnnotationError("A drawing needs a path.")
        if kind != "ink" and "rects" not in out:
            raise AnnotationError(f"A {kind} needs rects.")
    return out


def _row(r) -> dict:
    d = dict(r)
    for k in ("rects", "path"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


# ============================================================================ store
class AnnotationStore:
    def __init__(self, db):
        self.db = db

    def ensure_schema(self) -> None:
        with self.db.session() as c:
            c.executescript(SCHEMA)

    def for_attachment(self, attachment_id: int) -> list[dict]:
        with self.db.session() as c:
            rows = c.execute("SELECT * FROM annotations WHERE attachment_id = ? ORDER BY page, created, id",
                             (attachment_id,)).fetchall()
        return [_row(r) for r in rows]

    def for_item(self, paper_id: str) -> list[dict]:
        with self.db.session() as c:
            rows = c.execute("SELECT a.* FROM annotations a JOIN attachments t ON t.id = a.attachment_id "
                             "WHERE t.paper_id = ? ORDER BY a.attachment_id, a.page, a.created",
                             (paper_id,)).fetchall()
        return [_row(r) for r in rows]

    def get(self, ann_id: str) -> dict | None:
        with self.db.session() as c:
            r = c.execute("SELECT * FROM annotations WHERE id = ?", (ann_id,)).fetchone()
        return _row(r) if r else None

    def create(self, attachment_id: int, data: dict, conn=None) -> dict:
        fields = clean(data)
        ann_id = str(data.get("id") or uuid.uuid4().hex)
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", ann_id):
            raise AnnotationError("id must be 8–64 letters, digits, - or _.")
        now = utcnow()
        row = {"id": ann_id, "attachment_id": attachment_id, "created": data.get("created") or now, "modified": now,
               **{k: fields.get(k) for k in FIELDS}}
        row["rects"] = json.dumps(row["rects"]) if row["rects"] is not None else None
        row["path"] = json.dumps(row["path"]) if row["path"] is not None else None
        cols = ", ".join(row)
        sql = f"INSERT INTO annotations ({cols}) VALUES ({', '.join('?' for _ in row)})"
        if conn is not None:
            conn.execute(sql, tuple(row.values()))
            return {**row, "rects": fields.get("rects"), "path": fields.get("path")}
        with self.db.session() as c:
            if c.execute("SELECT 1 FROM annotations WHERE id = ?", (ann_id,)).fetchone():
                raise AnnotationError("An annotation with this id already exists.")
            c.execute(sql, tuple(row.values()))
        return self.get(ann_id)

    def update(self, ann_id: str, data: dict) -> dict:
        fields = clean(data, partial=True)
        fields.pop("type", None)   # an annotation keeps its kind
        if not fields:
            raise AnnotationError("Nothing to change.")
        sets = {k: (json.dumps(v) if k in ("rects", "path") and v is not None else v) for k, v in fields.items()}
        sets["modified"] = utcnow()
        with self.db.session() as c:
            cur = c.execute(f"UPDATE annotations SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                            (*sets.values(), ann_id))
            if not cur.rowcount:
                raise KeyError(ann_id)
        return self.get(ann_id)

    def delete(self, ann_id: str) -> bool:
        with self.db.session() as c:
            return bool(c.execute("DELETE FROM annotations WHERE id = ?", (ann_id,)).rowcount)

    # ------------------------------------------------------------------ migration
    def pending_legacy(self) -> list[str]:
        """Items with an old SVG blob that has not been converted yet."""
        with self.db.session() as c:
            rows = c.execute("SELECT id, annotations FROM papers WHERE annotations IS NOT NULL AND annotations != '' "
                             "AND id NOT IN (SELECT paper_id FROM annotation_migration)").fetchall()
        out = []
        for r in rows:
            try:
                blob = json.loads(r["annotations"])
            except ValueError:
                blob = None
            if isinstance(blob, dict) and any(str(v or "").strip() for v in blob.values()):
                out.append(r["id"])
            else:
                with self.db.session() as c:   # empty blob: nothing to convert, never look again
                    c.execute("INSERT OR REPLACE INTO annotation_migration VALUES (?, NULL, 0, '[]', ?)",
                              (r["id"], utcnow()))
        return out

    def migration_report(self) -> list[dict]:
        with self.db.session() as c:
            rows = c.execute("SELECT m.*, p.title FROM annotation_migration m LEFT JOIN papers p ON p.id = m.paper_id "
                             "ORDER BY m.at").fetchall()
        return [{**dict(r), "skipped": json.loads(r["skipped"] or "[]")} for r in rows]


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
                if lines:   # underline: a line per text line plus a transparent hit box
                    boxes = [rect_of(r) for r in rects if r.attrs.get("fill") == "transparent"]
                    if not boxes:   # no hit boxes: rebuild from the lines (1 em tall)
                        boxes = [[fx(min(_f(l.attrs.get("x1")), _f(l.attrs.get("x2")))), fy(_f(l.attrs.get("y1")) - 11),
                                  fx(abs(_f(l.attrs.get("x2")) - _f(l.attrs.get("x1")))), fy(11)] for l in lines]
                    out.append({"type": "underline", "rects": boxes, "color": _hex(lines[0].attrs.get("stroke"), "#ff0000"),
                                "size": _f(lines[0].attrs.get("stroke-width"), 2) or 2})
                elif rects:
                    fill = next((r.attrs.get("fill") for r in rects if r.attrs.get("fill") not in ("transparent", "none")), None)
                    out.append({"type": "highlight", "rects": [rect_of(r) for r in rects if r.attrs.get("fill") != "transparent"],
                                "color": _hex(fill, "#ffff00")})
                else:
                    skipped.append("an empty group")
            elif el.tag == "rect":
                out.append({"type": "box", "rects": [rect_of(el)], "color": _hex(el.attrs.get("stroke"), "#000000"),
                            "size": _f(el.attrs.get("stroke-width"), 2) or 2})
            elif el.tag == "path":
                pts = _path_points(el.attrs.get("d", ""))
                if not pts:
                    skipped.append("a drawing without points")
                    continue
                out.append({"type": "ink", "path": [[x / W, y / H] for x, y in pts],
                            "color": _hex(el.attrs.get("stroke"), "#000000"), "size": _f(el.attrs.get("stroke-width"), 2) or 2})
            elif el.tag == "foreignobject":
                editable = _find(el, lambda e: "contenteditable" in e.attrs)
                text = _text_of(editable or el).strip("\n")
                if not text.strip():
                    continue   # an empty text box is deleted by the viewer anyway
                st = _style((editable or el).attrs.get("style", ""))
                size = _f(st.get("font-size"), 16) or 16
                lines = text.split("\n")
                w = max(len(line) for line in lines) * size * 0.55 + 8
                h = len(lines) * size * 1.2 + 8
                out.append({"type": "text", "rects": [[fx(el.attrs.get("x")), fy(el.attrs.get("y")), w / W, h / H]],
                            "text": text, "color": _hex(st.get("color"), "#000000"), "size": size})
            else:
                skipped.append(f"an unknown <{el.tag}> element")
        except Exception as e:   # one bad element never stops the rest
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
                atts = c.execute("SELECT id, url, type FROM attachments WHERE paper_id = ? ORDER BY id", (pid,)).fetchall()
            pdfs = [a for a in atts if services.papers.attachment_path(dict(a)) is not None
                    and str(a["url"]).lower().endswith(".pdf")] or \
                   [a for a in atts if services.papers.attachment_path(dict(a)) is not None]
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
            c.execute("INSERT OR REPLACE INTO annotation_migration VALUES (?, ?, ?, ?, ?)",
                      (pid, att_id, converted, json.dumps(skipped), utcnow()))
        if skipped:
            log.warning("Annotations of item %s: %s converted, not converted: %s", pid, converted, "; ".join(skipped))
        total += converted
        skipped_total += len(skipped)
    log.info("Converted %s annotations of %s items (%s parts skipped)", total, len(pending), skipped_total)
    return {"items": len(pending), "converted": total, "skipped": skipped_total}
