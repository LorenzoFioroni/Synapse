"""PDF annotations as records.

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
(``papers.annotations``). ``annotations_legacy.migrate_legacy`` converts those blobs once, after a backup,
and leaves the old column untouched.
"""
from __future__ import annotations

import json
import re
import uuid

from .db import utcnow

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
    except (TypeError, ValueError) as e:
        raise AnnotationError("Coordinates must be numbers.") from e
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
        except (TypeError, ValueError) as e:
            raise AnnotationError("page must be a page number (1, 2, …).") from e
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
        except (TypeError, ValueError) as e:
            raise AnnotationError("size must be a number.") from e
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
