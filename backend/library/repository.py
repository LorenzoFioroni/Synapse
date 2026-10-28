"""Data access for papers and attachments.

All SQL that touches the library lives here; the HTTP layer only calls these
methods and never builds queries itself.
"""

from __future__ import annotations

import builtins
import logging
import os
import sqlite3
import uuid
from pathlib import Path

from ..settings import SettingsStore
from . import citekeys
from .db import Database, utcnow
from .fields import DEFAULT_ITEM_TYPE, EDITABLE_KEYS, ITEM_TYPE_LABELS

log = logging.getLogger(__name__)


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

    def __init__(self, papers: builtins.list[dict]):
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
        self._citekey_listeners: list = []
        self._item_listeners: list = []

    # ------------------------------------------------------------------ reads
    def list(self, view: str = "library") -> builtins.list[dict]:
        """Items for a view: library, trash or duplicates (folder views are filtered in the API)."""
        if view == "duplicates":
            papers = self.list()
            group_of = {pid: n for n, g in enumerate(self.duplicate_groups(papers)) for pid in g}
            out = [p for p in papers if p["id"] in group_of]
            for p in out:
                p["dup_group"] = group_of[p["id"]]
            return out
        where = "deleted_at IS NOT NULL" if view == "trash" else "deleted_at IS NULL"
        with self.db.session() as c:
            papers = [dict(r) for r in c.execute(f"SELECT * FROM papers WHERE {where}")]
            atts: dict[str, list] = {}
            for r in c.execute("SELECT * FROM attachments ORDER BY id"):
                atts.setdefault(r["paper_id"], []).append(dict(r))
            from .organize import folders_by_paper, tags_by_paper

            tags = tags_by_paper(c)
            folders = folders_by_paper(c)
        for p in papers:
            p.pop("annotations", None)  # large and only needed by the viewer
            p.pop("folder_id", None)  # legacy column
            p["attachments"] = atts.get(p["id"], [])
            p["tags"] = tags.get(p["id"], [])
            p["folder_ids"] = folders.get(p["id"], [])
        return papers

    def get(self, paper_id: str) -> dict:
        with self.db.session() as c:
            row = c.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if not row:
                raise NotFound(paper_id)
            p = dict(row)
            p.pop("annotations", None)
            p["attachments"] = [
                dict(r) for r in c.execute("SELECT * FROM attachments WHERE paper_id = ? ORDER BY id", (paper_id,))
            ]
            p["tags"] = [
                r[0]
                for r in c.execute(
                    "SELECT t.name FROM paper_tags pt JOIN tags t ON t.id = pt.tag_id WHERE pt.paper_id = ? "
                    "ORDER BY t.name COLLATE NOCASE",
                    (paper_id,),
                )
            ]
            p["folder_ids"] = [
                r[0]
                for r in c.execute(
                    "SELECT folder_id FROM paper_folders WHERE paper_id = ? ORDER BY folder_id", (paper_id,)
                )
            ]
            p.pop("folder_id", None)
        return p

    def counts(self, library: builtins.list[dict] | None = None) -> dict:
        with self.db.session() as c:
            lib = c.execute("SELECT COUNT(*) FROM papers WHERE deleted_at IS NULL").fetchone()[0]
            trash = c.execute("SELECT COUNT(*) FROM papers WHERE deleted_at IS NOT NULL").fetchone()[0]
        dups = sum(len(g) for g in self.duplicate_groups(library))
        return {"library": lib, "trash": trash, "duplicates": dups}

    def duplicate_groups(self, papers: builtins.list[dict] | None = None) -> builtins.list[builtins.list[str]]:
        """Groups of item ids that look like the same work (same DOI, or same title and year)."""
        from .search import normalize

        papers = papers if papers is not None else self.list()
        parent = {p["id"]: p["id"] for p in papers}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)

        by_doi: dict[str, str] = {}  # DOI -> first item with it
        by_title: dict[str, builtins.list[tuple[str, str]]] = {}  # title -> (item id, year)
        for p in papers:
            doi = (p.get("doi") or "").strip().lower()
            if doi:
                if doi in by_doi:
                    union(p["id"], by_doi[doi])
                else:
                    by_doi[doi] = p["id"]
            title = normalize(p.get("title") or "")
            if len(title) > 10:
                for other_id, other_year in by_title.get(title, []):
                    y = str(p.get("year") or "")
                    if not y or not other_year or y == other_year:
                        union(p["id"], other_id)
                by_title.setdefault(title, []).append((p["id"], str(p.get("year") or "")))
        groups: dict[str, list[str]] = {}
        for p in papers:
            groups.setdefault(find(p["id"]), []).append(p["id"])
        return [g for g in groups.values() if len(g) > 1]

    def all_citation_keys(self, exclude_id: str | None = None) -> set[str]:
        with self.db.session() as c:
            rows = c.execute(
                "SELECT id, citation_key FROM papers WHERE citation_key IS NOT NULL AND citation_key != ''"
            )
            return {r["citation_key"] for r in rows if r["id"] != exclude_id}

    def _check_citation_key(self, c: sqlite3.Connection, key: str | None, paper_id: str = "") -> None:
        citekeys.validate(key)
        if not key:
            return
        # Case-insensitive: keys differing only in case would share a paper-note file on macOS.
        other = c.execute(
            "SELECT id, title FROM papers WHERE citation_key = ? COLLATE NOCASE AND id != ? LIMIT 1",
            (key, paper_id),
        ).fetchone()
        if other:
            raise citekeys.CitationKeyError(
                f'Citation key "{key}" is already used by "{other["title"] or "Untitled"}" ({other["id"]})'
            )

    def find_duplicate(self, entry: dict, papers: builtins.list[dict] | None = None) -> str | None:
        """Return the id of an existing (non-trashed) item matching by DOI or title+year."""
        return DuplicateFinder(papers if papers is not None else self.list()).find(entry)

    # ----------------------------------------------------------------- writes
    def create(self, data: dict | None = None, *, auto_citekey: bool | None = None) -> dict:
        return self.get(self.create_many([data or {}], auto_citekey=auto_citekey)[0])

    def create_many(self, entries: builtins.list[dict], *, auto_citekey: bool | None = None) -> builtins.list[str]:
        """Insert several items in one transaction. Returns the new ids. A citation key that is
        malformed or already taken rejects the whole batch (CitationKeyError).

        Besides metadata fields, an entry may carry ``folder_id`` / ``folder_ids`` and ``tags``.
        """
        return self._create_many(entries, auto_citekey=auto_citekey, fix_keys=False)[0]

    def import_many(self, entries: builtins.list[dict]) -> tuple[builtins.list[str], builtins.list[dict]]:
        """Like create_many for imported files, which often carry keys made elsewhere: a key that
        is malformed or already taken is replaced instead of failing the import. Returns the new
        ids and the replaced keys as ``{"id", "title", "from", "to"}``."""
        return self._create_many(entries, auto_citekey=None, fix_keys=True)

    def _create_many(
        self, entries: builtins.list[dict], *, auto_citekey: bool | None, fix_keys: bool
    ) -> tuple[builtins.list[str], builtins.list[dict]]:
        from .organize import add_tags_conn

        if auto_citekey is None:
            auto_citekey = self.settings.get("citekey_auto")
        pattern = self.settings.get("citekey_pattern")
        now = utcnow()
        ids: builtins.list[str] = []
        changes: builtins.list[dict] = []
        with self.db.session() as c:
            # Serialize key selection with other writers, including other Synapse processes.
            c.execute("BEGIN IMMEDIATE")
            taken = {r[0] for r in c.execute("SELECT citation_key FROM papers WHERE citation_key != ''")}
            # Explicit keys are reserved first, so a generated key never takes one a later entry asks for.
            keys: builtins.list[str | None] = []
            replace: dict[int, str] = {}  # entry index -> the key it asked for, when that cannot be kept
            explicit: set[str] = set()
            for i, data in enumerate(entries):
                key = _clean_value("citation_key", data.get("citation_key"))
                if key and fix_keys:
                    clean = citekeys.sanitize(key)
                    if not clean or clean.casefold() in explicit:
                        replace[i] = key
                        key = None
                    else:
                        try:
                            self._check_citation_key(c, clean)
                        except citekeys.CitationKeyError:
                            replace[i] = key
                            key = None
                        else:
                            if clean != key:
                                replace[i] = key
                            key = clean
                else:
                    self._check_citation_key(c, key)
                    if key and key.casefold() in explicit:
                        raise citekeys.CitationKeyError(
                            f'Citation key "{key}" is already used by another imported item'
                        )
                if key:
                    explicit.add(key.casefold())
                keys.append(key)
            taken.update(k for k in keys if k)
            for i, data in enumerate(entries):
                values = {k: _clean_value(k, data.get(k)) for k in EDITABLE_KEYS if k in data}
                values.setdefault("item_type", DEFAULT_ITEM_TYPE)
                values.setdefault("title", "")
                if keys[i]:
                    values["citation_key"] = keys[i]
                elif i in replace:
                    values.pop("citation_key", None)
                if i in replace and not keys[i]:
                    # The file named a key that cannot be used as it is: the nearest free one, or one
                    # from the pattern when nothing of it survives (even with automatic keys off, so
                    # the item keeps a key as it had in the file).
                    clean = citekeys.sanitize(replace[i])
                    if clean:
                        values["citation_key"] = citekeys.make_unique(clean, taken)
                    elif values.get("authors") or values.get("title"):
                        values["citation_key"] = citekeys.generate(pattern, values, taken)
                elif auto_citekey and not values.get("citation_key") and (values.get("authors") or values.get("title")):
                    values["citation_key"] = citekeys.generate(pattern, values, taken)
                if values.get("citation_key"):
                    taken.add(values["citation_key"])
                new_id = uuid.uuid4().hex
                values.update(id=new_id, date_added=now, date_modified=now)
                c.execute(
                    f"INSERT INTO papers ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                    list(values.values()),
                )
                folders = list(data.get("folder_ids") or []) + [data.get("folder_id")]
                for f in {int(f) for f in folders if f not in (None, "")}:
                    if c.execute("SELECT 1 FROM folders WHERE id = ?", (f,)).fetchone():
                        c.execute(
                            "INSERT OR IGNORE INTO paper_folders (paper_id, folder_id) VALUES (?, ?)", (new_id, f)
                        )
                if data.get("tags"):
                    add_tags_conn(c, [new_id], data["tags"])
                ids.append(new_id)
                if i in replace:
                    changes.append(
                        {
                            "id": new_id,
                            "title": values.get("title") or "",
                            "from": replace[i],
                            "to": values.get("citation_key") or "",
                        }
                    )
        self._notify_items("item.added", ids)
        return ids, changes

    def update(self, paper_id: str, data: dict) -> dict:
        values = {k: _clean_value(k, v) for k, v in data.items() if k in EDITABLE_KEYS}
        changed_key = None
        if values:
            with self.db.session() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
                if row is None:
                    raise NotFound(paper_id)
                old = row["citation_key"] or ""
                if (
                    "citation_key" not in values
                    and not old
                    and self.settings.get("citekey_auto")
                    and {"authors", "title", "year"} & values.keys()
                ):
                    # Items created empty get a key as soon as they have enough metadata.
                    merged = {**dict(row), **values}
                    pattern = self.settings.get("citekey_pattern")
                    if all(merged.get(f) for f in citekeys.required_fields(pattern) or {"authors", "title"}):
                        taken = {r[0] for r in c.execute("SELECT citation_key FROM papers WHERE citation_key != ''")}
                        values["citation_key"] = citekeys.generate(pattern, merged, taken)
                if "citation_key" in values and (values["citation_key"] or "") != old:
                    self._check_citation_key(c, values["citation_key"], paper_id)
                    if old:
                        changed_key = (old, values["citation_key"] or "")
                values["date_modified"] = utcnow()
                sets = ", ".join(f"{k} = ?" for k in values)
                c.execute(f"UPDATE papers SET {sets} WHERE id = ?", [*values.values(), paper_id])
        if changed_key:
            self._notify_citekey_change(paper_id, *changed_key)
        if values:
            self._notify_items("item.changed", [paper_id], fields=sorted(set(values) - {"date_modified"}))
        return self.get(paper_id)

    # Citation keys are stable: nothing in the app changes an existing key on its own
    # (new keys are only generated for items that have none). A key changes only through an
    # explicit user action, and every such change passes through update() and is reported
    # here, so notes citing the old key can be rewritten.
    def on_citekey_change(self, callback) -> None:
        """Register ``callback(paper_id, old_key, new_key)``."""
        self._citekey_listeners.append(callback)

    def _notify_citekey_change(self, paper_id: str, old: str, new: str) -> None:
        for cb in list(self._citekey_listeners):
            try:
                cb(paper_id, old, new)
            except Exception:  # the key is already saved; a listener must not undo that
                log.exception("citekey listener failed for %s -> %s", old, new)

    def on_items(self, callback) -> None:
        """Register ``callback(event, ids, **details)``, called after items were added
        (``item.added``), had fields set (``item.changed``, with ``fields``), went to the Trash
        (``item.trashed``), came back (``item.restored``) or were deleted for good (``item.deleted``)."""
        self._item_listeners.append(callback)

    def _notify_items(self, event: str, ids: builtins.list[str], **details) -> None:
        if not ids:
            return
        for cb in list(self._item_listeners):
            try:
                cb(event, list(ids), **details)
            except Exception:  # the change is saved; a listener must not undo that
                log.exception("item listener failed for %s", event)

    def regenerate_citekeys(self, ids: builtins.list[str]) -> dict[str, str]:
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

    def trash(self, ids: builtins.list[str]) -> None:
        with self.db.session() as c:
            c.executemany("UPDATE papers SET deleted_at = ? WHERE id = ?", [(utcnow(), i) for i in ids])
        self._notify_items("item.trashed", ids)

    def restore(self, ids: builtins.list[str]) -> None:
        with self.db.session() as c:
            c.executemany("UPDATE papers SET deleted_at = NULL WHERE id = ?", [(i,) for i in ids])
        self._notify_items("item.restored", ids)

    def delete_permanently(self, ids: builtins.list[str]) -> builtins.list[int]:
        """Delete items, their attachment rows and files. Returns removed attachment ids."""
        removed = []
        with self.db.session() as c:
            for pid in ids:
                for r in c.execute("SELECT id, url FROM attachments WHERE paper_id = ?", (pid,)).fetchall():
                    self._remove_file(r["url"])
                    removed.append(r["id"])
                c.execute("DELETE FROM attachments WHERE paper_id = ?", (pid,))
                c.execute("DELETE FROM paper_tags WHERE paper_id = ?", (pid,))
                c.execute("DELETE FROM paper_folders WHERE paper_id = ?", (pid,))
                c.execute("DELETE FROM papers WHERE id = ?", (pid,))
            c.execute("DELETE FROM tags WHERE color IS NULL AND id NOT IN (SELECT DISTINCT tag_id FROM paper_tags)")
        self._notify_items("item.deleted", ids)
        return removed

    def trashed_ids(self) -> builtins.list[str]:
        with self.db.session() as c:
            return [r[0] for r in c.execute("SELECT id FROM papers WHERE deleted_at IS NOT NULL")]

    def add_attachment(self, paper_id: str, name: str, filename: str, type_: str = "pdf") -> dict:
        with self.db.session() as c:
            if not c.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone():
                raise NotFound(paper_id)
            cur = c.execute(
                "INSERT INTO attachments (paper_id, name, url, type, date_added) VALUES (?, ?, ?, ?, ?)",
                (paper_id, name, filename, type_, utcnow()),
            )
            att_id = cur.lastrowid
        return self.get_attachment(att_id)

    def get_attachment(self, att_id: int) -> dict:
        with self.db.session() as c:
            row = c.execute("SELECT * FROM attachments WHERE id = ?", (att_id,)).fetchone()
        if not row:
            raise NotFound(att_id)
        return dict(row)

    def all_attachments(self) -> builtins.list[dict]:
        with self.db.session() as c:
            return [dict(r) for r in c.execute("SELECT * FROM attachments")]

    def rename_attachment(self, att_id: int, name: str) -> dict:
        with self.db.session() as c:
            c.execute("UPDATE attachments SET name = ? WHERE id = ?", (name, att_id))
            # the name is part of the item (searched in "all" mode), so the item changed
            c.execute(
                "UPDATE papers SET date_modified = ? WHERE id = (SELECT paper_id FROM attachments WHERE id = ?)",
                (utcnow(), att_id),
            )
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
        p = self.pdf_dir / os.path.basename(url)
        return p if p.exists() else None

    def _remove_file(self, url: str | None) -> None:
        if not url or url.startswith(("http://", "https://")):
            return
        p = self.pdf_dir / os.path.basename(url)
        if p.exists():
            p.unlink()
