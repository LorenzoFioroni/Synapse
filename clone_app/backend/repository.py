"""Data access for papers and attachments.

All SQL that touches the library lives here; the HTTP layer only calls these
methods and never builds queries itself.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from . import citekeys
from .db import Database, utcnow
from .fields import DEFAULT_ITEM_TYPE, EDITABLE_KEYS, ITEM_TYPE_LABELS
from .settings import SettingsStore


class NotFound(LookupError):
    pass


def _clean_value(key: str, value):
    if value is None:
        return None
    if key == "year":
        s = str(value).strip()
        if not s:
            return None
        return int(s) if s.isdigit() else s
    if key == "item_type":
        return value if value in ITEM_TYPE_LABELS else DEFAULT_ITEM_TYPE
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value)
    return str(value).strip()


class DuplicateFinder:
    """Fast duplicate lookup by DOI, or by normalized title (+ year when both have one)."""

    def __init__(self, papers: list[dict]):
        self.by_doi: dict[str, str] = {}
        self.by_title: dict[str, list[tuple[str, str]]] = {}
        for p in papers:
            self.add(p, p.get("id"))

    @staticmethod
    def _keys(entry: dict) -> tuple[str, str]:
        from .search import normalize
        return (entry.get("doi") or "").strip().lower(), normalize(entry.get("title") or "")

    def add(self, entry: dict, item_id) -> None:
        doi, title = self._keys(entry)
        if doi:
            self.by_doi.setdefault(doi, item_id)
        if title:
            self.by_title.setdefault(title, []).append((item_id, str(entry.get("year") or "")))

    def find(self, entry: dict) -> str | None:
        doi, title = self._keys(entry)
        if doi and doi in self.by_doi:
            return self.by_doi[doi]
        year = str(entry.get("year") or "")
        for item_id, y in self.by_title.get(title, []) if title else []:
            if not year or not y or year == y:
                return item_id
        return None


class PaperRepository:
    def __init__(self, db: Database, settings: SettingsStore, pdf_dir: Path):
        self.db = db
        self.settings = settings
        self.pdf_dir = Path(pdf_dir)

    # ------------------------------------------------------------------ reads
    def list(self, view: str = "library") -> list[dict]:
        where = "deleted_at IS NOT NULL" if view == "trash" else "deleted_at IS NULL"
        with self.db.session() as c:
            papers = [dict(r) for r in c.execute(f"SELECT * FROM papers WHERE {where}")]
            atts: dict[str, list] = {}
            for r in c.execute("SELECT * FROM attachments ORDER BY id"):
                atts.setdefault(r["paper_id"], []).append(dict(r))
        for p in papers:
            p.pop("annotations", None)  # large and only needed by the viewer
            p["attachments"] = atts.get(p["id"], [])
        return papers

    def get(self, paper_id: str) -> dict:
        with self.db.session() as c:
            row = c.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if not row:
                raise NotFound(paper_id)
            p = dict(row)
            p.pop("annotations", None)
            p["attachments"] = [dict(r) for r in c.execute(
                "SELECT * FROM attachments WHERE paper_id = ? ORDER BY id", (paper_id,))]
        return p

    def counts(self) -> dict:
        with self.db.session() as c:
            lib = c.execute("SELECT COUNT(*) FROM papers WHERE deleted_at IS NULL").fetchone()[0]
            trash = c.execute("SELECT COUNT(*) FROM papers WHERE deleted_at IS NOT NULL").fetchone()[0]
        return {"library": lib, "trash": trash}

    def all_citation_keys(self, exclude_id: str | None = None) -> set[str]:
        with self.db.session() as c:
            rows = c.execute("SELECT id, citation_key FROM papers WHERE citation_key IS NOT NULL AND citation_key != ''")
            return {r["citation_key"] for r in rows if r["id"] != exclude_id}

    def find_duplicate(self, entry: dict, papers: list[dict] | None = None) -> str | None:
        """Return the id of an existing (non-trashed) item matching by DOI or title+year."""
        return DuplicateFinder(papers if papers is not None else self.list()).find(entry)

    # ----------------------------------------------------------------- writes
    def create(self, data: dict | None = None, *, auto_citekey: bool | None = None) -> dict:
        return self.get(self.create_many([data or {}], auto_citekey=auto_citekey)[0])

    def create_many(self, entries: list[dict], *, auto_citekey: bool | None = None) -> list[str]:
        """Insert several items in one transaction (used by imports). Returns the new ids."""
        if auto_citekey is None:
            auto_citekey = self.settings.get("citekey_auto")
        pattern = self.settings.get("citekey_pattern")
        taken = self.all_citation_keys() if auto_citekey else set()
        now = utcnow()
        ids = []
        with self.db.session() as c:
            for data in entries:
                values = {k: _clean_value(k, data.get(k)) for k in EDITABLE_KEYS if k in data}
                values.setdefault("item_type", DEFAULT_ITEM_TYPE)
                values.setdefault("title", "")
                if auto_citekey and not values.get("citation_key") and (values.get("authors") or values.get("title")):
                    values["citation_key"] = citekeys.generate(pattern, values, taken)
                if values.get("citation_key"):
                    taken.add(values["citation_key"])
                new_id = uuid.uuid4().hex
                values.update(id=new_id, date_added=now, date_modified=now)
                c.execute(f"INSERT INTO papers ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                          list(values.values()))
                ids.append(new_id)
        return ids

    def update(self, paper_id: str, data: dict) -> dict:
        values = {k: _clean_value(k, v) for k, v in data.items() if k in EDITABLE_KEYS}
        if values and "citation_key" not in values and self.settings.get("citekey_auto") \
                and {"authors", "title", "year"} & values.keys():
            # Items created empty get a key as soon as they have enough metadata.
            current = self.get(paper_id)
            if not current.get("citation_key"):
                merged = {**current, **values}
                pattern = self.settings.get("citekey_pattern")
                if all(merged.get(f) for f in citekeys.required_fields(pattern) or {"authors", "title"}):
                    values["citation_key"] = citekeys.generate(
                        pattern, merged, self.all_citation_keys(exclude_id=paper_id))
        if values:
            values["date_modified"] = utcnow()
            sets = ", ".join(f"{k} = ?" for k in values)
            with self.db.session() as c:
                cur = c.execute(f"UPDATE papers SET {sets} WHERE id = ?", [*values.values(), paper_id])
                if cur.rowcount == 0:
                    raise NotFound(paper_id)
        return self.get(paper_id)

    def regenerate_citekeys(self, ids: list[str]) -> dict[str, str]:
        pattern = self.settings.get("citekey_pattern")
        taken = self.all_citation_keys()
        out = {}
        for pid in ids:
            p = self.get(pid)
            taken.discard(p.get("citation_key") or "")
            key = citekeys.generate(pattern, p, taken)
            taken.add(key)
            self.update(pid, {"citation_key": key})
            out[pid] = key
        return out

    def trash(self, ids: list[str]) -> None:
        with self.db.session() as c:
            c.executemany("UPDATE papers SET deleted_at = ? WHERE id = ?", [(utcnow(), i) for i in ids])

    def restore(self, ids: list[str]) -> None:
        with self.db.session() as c:
            c.executemany("UPDATE papers SET deleted_at = NULL WHERE id = ?", [(i,) for i in ids])

    def delete_permanently(self, ids: list[str]) -> list[int]:
        """Delete items, their attachment rows and files. Returns removed attachment ids."""
        removed = []
        with self.db.session() as c:
            for pid in ids:
                for r in c.execute("SELECT id, url FROM attachments WHERE paper_id = ?", (pid,)).fetchall():
                    self._remove_file(r["url"])
                    removed.append(r["id"])
                c.execute("DELETE FROM attachments WHERE paper_id = ?", (pid,))
                c.execute("DELETE FROM papers WHERE id = ?", (pid,))
        return removed

    def trashed_ids(self) -> list[str]:
        with self.db.session() as c:
            return [r[0] for r in c.execute("SELECT id FROM papers WHERE deleted_at IS NOT NULL")]

    # ------------------------------------------------------------ annotations
    def get_annotations(self, paper_id: str):
        import json
        with self.db.session() as c:
            row = c.execute("SELECT annotations FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else {}

    def save_annotations(self, paper_id: str, annotations) -> None:
        import json
        with self.db.session() as c:
            c.execute("UPDATE papers SET annotations = ? WHERE id = ?", (json.dumps(annotations), paper_id))

    # ------------------------------------------------------------ attachments
    def add_attachment(self, paper_id: str, name: str, filename: str, type_: str = "pdf") -> dict:
        with self.db.session() as c:
            if not c.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone():
                raise NotFound(paper_id)
            cur = c.execute(
                "INSERT INTO attachments (paper_id, name, url, type, date_added) VALUES (?, ?, ?, ?, ?)",
                (paper_id, name, filename, type_, utcnow()))
            att_id = cur.lastrowid
        return self.get_attachment(att_id)

    def get_attachment(self, att_id: int) -> dict:
        with self.db.session() as c:
            row = c.execute("SELECT * FROM attachments WHERE id = ?", (att_id,)).fetchone()
        if not row:
            raise NotFound(att_id)
        return dict(row)

    def all_attachments(self) -> list[dict]:
        with self.db.session() as c:
            return [dict(r) for r in c.execute("SELECT * FROM attachments")]

    def rename_attachment(self, att_id: int, name: str) -> dict:
        with self.db.session() as c:
            c.execute("UPDATE attachments SET name = ? WHERE id = ?", (name, att_id))
        return self.get_attachment(att_id)

    def delete_attachment(self, att_id: int) -> None:
        att = self.get_attachment(att_id)
        self._remove_file(att["url"])
        with self.db.session() as c:
            c.execute("DELETE FROM attachments WHERE id = ?", (att_id,))

    def attachment_path(self, att: dict) -> Path | None:
        url = att.get("url") or ""
        if url.startswith(("http://", "https://")):
            return None
        p = (self.pdf_dir / os.path.basename(url))
        return p if p.exists() else None

    def _remove_file(self, url: str | None) -> None:
        if not url or url.startswith(("http://", "https://")):
            return
        p = self.pdf_dir / os.path.basename(url)
        if p.exists():
            p.unlink()
