"""Folders, tags and smart folders.

* Folders form a tree. An item can be in any number of folders: ``paper_folders``
  stores only (paper id, folder id) links, so the item's data and PDFs exist once
  no matter how many folders show it; an item does not have to be in any folder.
* Tags are many-to-many (``paper_tags``); names are unique ignoring case.
* Smart folders are saved searches (query + scope), evaluated on demand.
"""

from __future__ import annotations

import builtins
import re
import sqlite3

from .db import Database, utcnow
from .repository import NotFound
from .search import MODES

TAG_COLORS = ["#e5484d", "#f76b15", "#ffc53d", "#30a46c", "#12a594", "#0090ff", "#6e56cf", "#d6409f", "#8d8d86"]


class OrganizeError(ValueError):
    pass


def _clean_name(name: str | None, what: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise OrganizeError(f"{what} name cannot be empty")
    if len(name) > 120:
        raise OrganizeError(f"{what} name is too long")
    return name


def clean_tag_names(names) -> list[str]:
    if isinstance(names, str):
        names = re.split(r"[;,\n]", names)
    out, seen = [], set()
    for n in names or []:
        n = " ".join(str(n).split())
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n[:80])
    return out


# --------------------------------------------------------- tag helpers (conn)
def ensure_tags(c: sqlite3.Connection, names: list[str]) -> list[int]:
    """Return tag ids for ``names``, creating missing tags (existing casing wins)."""
    ids = []
    for n in clean_tag_names(names):
        row = c.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (n,)).fetchone()
        if row:
            ids.append(row[0])
        else:
            ids.append(c.execute("INSERT INTO tags (name) VALUES (?)", (n,)).lastrowid)
    return ids


def add_tags_conn(c: sqlite3.Connection, paper_ids: list[str], names: list[str]) -> None:
    tag_ids = ensure_tags(c, names)
    c.executemany(
        "INSERT OR IGNORE INTO paper_tags (paper_id, tag_id) VALUES (?, ?)",
        [(p, t) for p in paper_ids for t in tag_ids],
    )


def tags_by_paper(c: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in c.execute(
        "SELECT pt.paper_id, t.name FROM paper_tags pt JOIN tags t ON t.id = pt.tag_id ORDER BY t.name COLLATE NOCASE"
    ):
        out.setdefault(r[0], []).append(r[1])
    return out


def folders_by_paper(c: sqlite3.Connection) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for r in c.execute("SELECT paper_id, folder_id FROM paper_folders ORDER BY folder_id"):
        out.setdefault(r[0], []).append(r[1])
    return out


def _touch(c: sqlite3.Connection, paper_ids) -> None:
    now = utcnow()
    c.executemany("UPDATE papers SET date_modified = ? WHERE id = ?", [(now, p) for p in paper_ids])


# =================================================================== folders
class FolderRepository:
    def __init__(self, db: Database):
        self.db = db

    def tree(self) -> builtins.list[dict]:
        """Flat list of folders with parent ids and direct item counts (non-trashed)."""
        with self.db.session() as c:
            counts = {
                r[0]: r[1]
                for r in c.execute(
                    "SELECT pf.folder_id, COUNT(*) FROM paper_folders pf JOIN papers p ON p.id = pf.paper_id "
                    "WHERE p.deleted_at IS NULL GROUP BY pf.folder_id"
                )
            }
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT id, name, parent_id, position FROM folders ORDER BY position, name COLLATE NOCASE"
                )
            ]
        for r in rows:
            r["count"] = counts.get(r["id"], 0)
        return rows

    def get(self, folder_id: int) -> dict:
        with self.db.session() as c:
            row = c.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if not row:
            raise NotFound(f"folder {folder_id}")
        return dict(row)

    def descendants(self, folder_id: int, include_self: bool = True) -> set[int]:
        rows = self.tree()
        children: dict[int | None, list[int]] = {}
        for r in rows:
            children.setdefault(r["parent_id"], []).append(r["id"])
        out, stack = set(), [folder_id]
        while stack:
            f = stack.pop()
            if f in out:
                continue
            out.add(f)
            stack.extend(children.get(f, []))
        if not include_self:
            out.discard(folder_id)
        return out

    def paths(self) -> dict[int, list[str]]:
        """folder id -> names from the root down to that folder."""
        rows = {r["id"]: r for r in self.tree()}
        out = {}
        for fid in rows:
            names, cur, guard = [], fid, 0
            while cur in rows and guard < 100:
                names.append(rows[cur]["name"])
                cur = rows[cur]["parent_id"]
                guard += 1
            out[fid] = list(reversed(names))
        return out

    def _check_sibling_name(self, c, name: str, parent_id, exclude: int | None = None) -> None:
        q = "SELECT id FROM folders WHERE name = ? COLLATE NOCASE AND " + (
            "parent_id IS NULL" if parent_id is None else "parent_id = ?"
        )
        args = [name] + ([] if parent_id is None else [parent_id])
        for r in c.execute(q, args):
            if r[0] != exclude:
                raise OrganizeError(f"A folder named “{name}” already exists here")

    def create(self, name: str, parent_id: int | None = None) -> dict:
        name = _clean_name(name, "Folder")
        with self.db.session() as c:
            if parent_id is not None and not c.execute("SELECT 1 FROM folders WHERE id = ?", (parent_id,)).fetchone():
                raise NotFound(f"folder {parent_id}")
            self._check_sibling_name(c, name, parent_id)
            pos = c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM folders").fetchone()[0]
            fid = c.execute(
                "INSERT INTO folders (name, parent_id, position, date_added) VALUES (?, ?, ?, ?)",
                (name, parent_id, pos, utcnow()),
            ).lastrowid
        return self.get(fid)

    def update(self, folder_id: int, *, name: str | None = None, parent_id="__keep__") -> dict:
        cur = self.get(folder_id)
        new_parent = cur["parent_id"] if parent_id == "__keep__" else parent_id
        new_name = _clean_name(name, "Folder") if name is not None else cur["name"]
        if new_parent is not None:
            if new_parent in self.descendants(folder_id):
                raise OrganizeError("A folder cannot be moved into itself or one of its subfolders")
            self.get(new_parent)
        with self.db.session() as c:
            self._check_sibling_name(c, new_name, new_parent, exclude=folder_id)
            c.execute("UPDATE folders SET name = ?, parent_id = ? WHERE id = ?", (new_name, new_parent, folder_id))
        return self.get(folder_id)

    def delete(self, folder_id: int) -> int:
        """Delete a folder and its subfolders. Their items stay in the library (and in any
        other folders); returns how many items ended up in no folder at all."""
        ids = list(self.descendants(folder_id))
        marks = ",".join("?" * len(ids))
        with self.db.session() as c:
            members = {r[0] for r in c.execute(f"SELECT paper_id FROM paper_folders WHERE folder_id IN ({marks})", ids)}
            c.execute(f"DELETE FROM paper_folders WHERE folder_id IN ({marks})", ids)
            c.execute(f"DELETE FROM folders WHERE id IN ({marks})", ids)
            still = {r[0] for r in c.execute("SELECT DISTINCT paper_id FROM paper_folders")}
        return len(members - still)

    def _check_folders(self, folder_ids) -> builtins.list[int]:
        out = []
        for f in folder_ids or []:
            if f in (None, ""):
                continue
            self.get(int(f))
            out.append(int(f))
        return out

    def change_items(self, paper_ids: builtins.list[str], add=(), remove=()) -> None:
        """Add items to folders and/or take them out of folders. Adding is idempotent."""
        add, remove = self._check_folders(add), [int(f) for f in remove or [] if f not in (None, "")]
        if not paper_ids or not (add or remove):
            return
        with self.db.session() as c:
            c.executemany(
                "DELETE FROM paper_folders WHERE paper_id = ? AND folder_id = ?",
                [(p, f) for p in paper_ids for f in remove],
            )
            c.executemany(
                "INSERT OR IGNORE INTO paper_folders (paper_id, folder_id) VALUES (?, ?)",
                [(p, f) for p in paper_ids for f in add],
            )
            _touch(c, paper_ids)

    def set_item_folders(self, paper_id: str, folder_ids) -> None:
        folder_ids = self._check_folders(folder_ids)
        with self.db.session() as c:
            c.execute("DELETE FROM paper_folders WHERE paper_id = ?", (paper_id,))
            c.executemany(
                "INSERT OR IGNORE INTO paper_folders (paper_id, folder_id) VALUES (?, ?)",
                [(paper_id, f) for f in folder_ids],
            )
            _touch(c, [paper_id])


# ====================================================================== tags
class TagRepository:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> builtins.list[dict]:
        with self.db.session() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT t.id, t.name, t.color, COUNT(p.id) AS count FROM tags t "
                    "LEFT JOIN paper_tags pt ON pt.tag_id = t.id "
                    "LEFT JOIN papers p ON p.id = pt.paper_id AND p.deleted_at IS NULL "
                    "GROUP BY t.id ORDER BY t.name COLLATE NOCASE"
                )
            ]

    def get(self, tag_id: int) -> dict:
        with self.db.session() as c:
            row = c.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if not row:
            raise NotFound(f"tag {tag_id}")
        return dict(row)

    def add(self, paper_ids: builtins.list[str], names) -> None:
        names = clean_tag_names(names)
        if not names or not paper_ids:
            return
        with self.db.session() as c:
            add_tags_conn(c, paper_ids, names)
            _touch(c, paper_ids)

    def remove(self, paper_ids: builtins.list[str], names) -> None:
        names = clean_tag_names(names)
        if not names or not paper_ids:
            return
        with self.db.session() as c:
            for n in names:
                row = c.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (n,)).fetchone()
                if row:
                    c.executemany(
                        "DELETE FROM paper_tags WHERE paper_id = ? AND tag_id = ?", [(p, row[0]) for p in paper_ids]
                    )
            _touch(c, paper_ids)
            self._prune(c)

    def set_for_item(self, paper_id: str, names) -> None:
        names = clean_tag_names(names)
        with self.db.session() as c:
            c.execute("DELETE FROM paper_tags WHERE paper_id = ?", (paper_id,))
            add_tags_conn(c, [paper_id], names)
            _touch(c, [paper_id])
            self._prune(c)

    def update(self, tag_id: int, *, name: str | None = None, color="__keep__") -> dict:
        tag = self.get(tag_id)
        with self.db.session() as c:
            if name is not None:
                name = _clean_name(name, "Tag")
                other = c.execute(
                    "SELECT id FROM tags WHERE name = ? COLLATE NOCASE AND id != ?", (name, tag_id)
                ).fetchone()
                if other:  # renaming onto an existing tag merges the two
                    c.execute(
                        "INSERT OR IGNORE INTO paper_tags (paper_id, tag_id) "
                        "SELECT paper_id, ? FROM paper_tags WHERE tag_id = ?",
                        (other[0], tag_id),
                    )
                    c.execute("DELETE FROM paper_tags WHERE tag_id = ?", (tag_id,))
                    c.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
                    if tag.get("color") and color == "__keep__":
                        c.execute("UPDATE tags SET color = COALESCE(color, ?) WHERE id = ?", (tag["color"], other[0]))
                    tag_id = other[0]
                else:
                    c.execute("UPDATE tags SET name = ? WHERE id = ?", (name, tag_id))
            if color != "__keep__":
                if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                    raise OrganizeError("Colour must look like #RRGGBB")
                c.execute("UPDATE tags SET color = ? WHERE id = ?", (color or None, tag_id))
        return self.get(tag_id)

    def delete(self, tag_id: int) -> None:
        self.get(tag_id)
        with self.db.session() as c:
            c.execute("DELETE FROM paper_tags WHERE tag_id = ?", (tag_id,))
            c.execute("DELETE FROM tags WHERE id = ?", (tag_id,))

    @staticmethod
    def _prune(c) -> None:
        """Drop tags no item uses any more, unless the user gave them a colour."""
        c.execute("DELETE FROM tags WHERE color IS NULL AND id NOT IN (SELECT DISTINCT tag_id FROM paper_tags)")


# ============================================================ smart folders
class SmartFolderRepository:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> builtins.list[dict]:
        with self.db.session() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT id, name, query, mode, position FROM smart_folders ORDER BY position, name COLLATE NOCASE"
                )
            ]

    def get(self, sid: int) -> dict:
        with self.db.session() as c:
            row = c.execute("SELECT id, name, query, mode, position FROM smart_folders WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise NotFound(f"smart folder {sid}")
        return dict(row)

    @staticmethod
    def _validate(query, mode):
        query = (query or "").strip()
        if not query:
            raise OrganizeError("A smart folder needs a search query")
        if mode not in MODES:
            raise OrganizeError(f"Unknown search scope: {mode}")
        return query

    def create(self, name: str, query: str, mode: str = "all") -> dict:
        name = _clean_name(name, "Smart folder")
        query = self._validate(query, mode)
        with self.db.session() as c:
            pos = c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM smart_folders").fetchone()[0]
            sid = c.execute(
                "INSERT INTO smart_folders (name, query, mode, position, date_added) VALUES (?, ?, ?, ?, ?)",
                (name, query, mode, pos, utcnow()),
            ).lastrowid
        return self.get(sid)

    def update(self, sid: int, data: dict) -> dict:
        cur = self.get(sid)
        name = _clean_name(data.get("name", cur["name"]), "Smart folder")
        mode = data.get("mode", cur["mode"])
        query = self._validate(data.get("query", cur["query"]), mode)
        with self.db.session() as c:
            c.execute("UPDATE smart_folders SET name = ?, query = ?, mode = ? WHERE id = ?", (name, query, mode, sid))
        return self.get(sid)

    def delete(self, sid: int) -> None:
        self.get(sid)
        with self.db.session() as c:
            c.execute("DELETE FROM smart_folders WHERE id = ?", (sid,))
