"""SQLite index of the notes vault (``data/notes_index.db``).

The index is derived data only: deleting the file and calling ``rebuild()`` restores the
exact same state. Each note is re-parsed only when its content hash changes.

Notes are identified by their path relative to the vault (``papers/smith2021.md``).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from . import parser
from .vault import NoteMissing, Vault, content_hash

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notes (
    path TEXT PRIMARY KEY,
    stem TEXT NOT NULL,           -- lower-case file name without .md (for [[link]] lookup)
    id TEXT, title TEXT, type TEXT, paper TEXT, author TEXT,
    created TEXT, updated TEXT,
    hash TEXT NOT NULL, mtime REAL, size INTEGER
);
CREATE INDEX IF NOT EXISTS idx_notes_stem ON notes(stem);
CREATE INDEX IF NOT EXISTS idx_notes_paper ON notes(paper);
CREATE TABLE IF NOT EXISTS links (
    path TEXT NOT NULL,           -- the note containing the link
    kind TEXT NOT NULL,           -- note | embed | cite | image
    target TEXT NOT NULL,
    target_norm TEXT NOT NULL,    -- lower-case target for case-insensitive lookup
    heading TEXT, alias TEXT, locator TEXT, page INTEGER, prefix TEXT,
    suppress_author INTEGER, grp INTEGER, line INTEGER, context TEXT
);
CREATE INDEX IF NOT EXISTS idx_links_path ON links(path);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(kind, target_norm);
CREATE TABLE IF NOT EXISTS tags (path TEXT NOT NULL, tag TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tags_path ON tags(path);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
CREATE TABLE IF NOT EXISTS headings (path TEXT NOT NULL, level INTEGER, text TEXT, line INTEGER);
CREATE INDEX IF NOT EXISTS idx_headings_path ON headings(path);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(path UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2');
"""

TABLES = ("notes", "links", "tags", "headings", "notes_fts")


def stem_of(path: str) -> str:
    return PurePosixPath(path).stem.lower()


def norm_target(target: str) -> str:
    t = target.strip().replace("\\", "/").lower()
    return t[:-3] if t.endswith(".md") else t


class NotesIndex:
    def __init__(self, db_path: Path | str, vault: Vault):
        self.db_path = str(db_path)
        self.vault = vault
        self._lock = threading.RLock()
        self._init_schema()

    # ------------------------------------------------------------------ plumbing
    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    @contextmanager
    def _session(self):
        c = self._connect()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._session() as c:
            row = None
            try:
                row = c.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
            except sqlite3.OperationalError:
                pass
            if row is not None and int(row["value"]) != SCHEMA_VERSION:
                # Derived data: an old layout is simply dropped and rebuilt.
                for t in TABLES + ("meta",):
                    c.execute(f"DROP TABLE IF EXISTS {t}")
            c.executescript(SCHEMA)
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))

    # ------------------------------------------------------------------ writing
    def _delete_rows(self, c, path: str) -> None:
        for t in TABLES:
            c.execute(f"DELETE FROM {t} WHERE path = ?", (path,))

    def _insert(self, c, path: str, text: str, h: str, mtime: float | None, size: int | None) -> None:
        p = parser.parse(text, fallback_title=PurePosixPath(path).stem)
        m = p.meta
        paper = str(m.get("paper") or "").strip() or None
        c.execute("INSERT INTO notes (path, stem, id, title, type, paper, author, created, updated, hash, mtime, size) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (path, stem_of(path), str(m.get("id") or "") or None, p.title,
                   str(m.get("type") or ("paper" if paper else "note")), paper,
                   str(m.get("author") or "human"), str(m.get("created") or "") or None,
                   str(m.get("updated") or "") or None, h, mtime, size))
        c.executemany(
            "INSERT INTO links (path, kind, target, target_norm, heading, alias, locator, page, prefix, "
            "suppress_author, grp, line, context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(path, l.kind, l.target, l.target if l.kind == "cite" else norm_target(l.target),
              l.heading, l.alias, l.locator, l.page, l.prefix, int(l.suppress_author), l.group, l.line, l.context)
             for l in p.links])
        c.executemany("INSERT INTO tags (path, tag) VALUES (?, ?)", [(path, t) for t in p.tags])
        c.executemany("INSERT INTO headings (path, level, text, line) VALUES (?,?,?,?)",
                      [(path, h_.level, h_.text, h_.line) for h_ in p.headings])
        c.execute("INSERT INTO notes_fts (path, title, body) VALUES (?,?,?)", (path, p.title, p.body))

    def update(self, path: str) -> bool:
        """(Re)index one note from disk; removes it if the file is gone. Returns True if changed."""
        path = self.vault.clean_rel(path)
        with self._lock:
            try:
                text, h = self.vault.read(path)
            except NoteMissing:
                return self.remove(path)
            st = self.vault.abs(path).stat()
            with self._session() as c:
                row = c.execute("SELECT hash FROM notes WHERE path = ?", (path,)).fetchone()
                if row and row["hash"] == h:
                    c.execute("UPDATE notes SET mtime = ?, size = ? WHERE path = ?", (st.st_mtime, st.st_size, path))
                    return False
                self._delete_rows(c, path)
                self._insert(c, path, text, h, st.st_mtime, st.st_size)
            return True

    def remove(self, path: str) -> bool:
        with self._lock, self._session() as c:
            existed = c.execute("SELECT 1 FROM notes WHERE path = ?", (path,)).fetchone() is not None
            self._delete_rows(c, path)
            return existed

    def sync(self) -> dict:
        """Bring the index in line with the files (cheap: only changed files are parsed)."""
        added = changed = removed = 0
        with self._lock:
            with self._session() as c:
                known = {r["path"]: (r["mtime"], r["size"]) for r in c.execute("SELECT path, mtime, size FROM notes")}
            seen = set()
            for rel, st in self.vault.iter_notes():
                seen.add(rel)
                if known.get(rel) == (st.st_mtime, st.st_size):
                    continue
                try:
                    if self.update(rel):
                        if rel in known:
                            changed += 1
                        else:
                            added += 1
                except Exception:
                    log.exception("Could not index note %s", rel)
            for rel in set(known) - seen:
                self.remove(rel)
                removed += 1
        return {"added": added, "changed": changed, "removed": removed}

    def rebuild(self) -> dict:
        with self._lock:
            with self._session() as c:
                for t in TABLES:
                    c.execute(f"DELETE FROM {t}")
            return self.sync()

    # ------------------------------------------------------------------ reading
    def note(self, path: str) -> dict | None:
        with self._session() as c:
            row = c.execute("SELECT * FROM notes WHERE path = ?", (path,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["tags"] = [r["tag"] for r in c.execute("SELECT tag FROM tags WHERE path = ? ORDER BY rowid", (path,))]
            d["headings"] = [dict(r) for r in c.execute(
                "SELECT level, text, line FROM headings WHERE path = ? ORDER BY line", (path,))]
            return d

    def list(self) -> list[dict]:
        with self._session() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT path, id, title, type, paper, author, created, updated, mtime FROM notes ORDER BY path")]
            tags: dict[str, list[str]] = {}
            for r in c.execute("SELECT path, tag FROM tags ORDER BY rowid"):
                tags.setdefault(r["path"], []).append(r["tag"])
        for r in rows:
            r["tags"] = tags.get(r["path"], [])
        return rows

    def resolve(self, target: str, *, from_path: str = "") -> list[str]:
        """Paths a [[target]] may refer to (best first). Empty target = the note itself."""
        t = norm_target(target)
        if not t:
            return [from_path] if from_path else []
        with self._session() as c:
            if "/" in t:
                rows = c.execute("SELECT path FROM notes WHERE lower(path) = ? OR lower(path) LIKE ? ESCAPE '\\'",
                                 (t + ".md", "%/" + _like_escape(t) + ".md")).fetchall()
            else:
                rows = c.execute("SELECT path FROM notes WHERE stem = ?", (t,)).fetchall()
        paths = [r["path"] for r in rows]
        # Prefer the shortest path, then alphabetical: deterministic and predictable.
        return sorted(paths, key=lambda p: (p.count("/"), len(p), p))

    def outgoing(self, path: str) -> list[dict]:
        with self._session() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM links WHERE path = ? ORDER BY line, rowid", (path,))]
        for r in rows:
            r["suppress_author"] = bool(r["suppress_author"])
            if r["kind"] in ("note", "embed"):
                matches = self.resolve(r["target"], from_path=path)
                r["resolved"] = matches[0] if matches else None
                r["ambiguous"] = len(matches) > 1
        return rows

    def backlinks(self, path: str) -> list[dict]:
        """Links from other notes to ``path`` (by stem or by folder/stem)."""
        stem = stem_of(path)
        noext = norm_target(path)
        candidates = {stem, noext}
        # every suffix of the folder path also works: [[b/c]] links to a/b/c.md
        parts = noext.split("/")
        for i in range(1, len(parts)):
            candidates.add("/".join(parts[i:]))
        with self._session() as c:
            q = ",".join("?" * len(candidates))
            rows = [dict(r) for r in c.execute(
                f"SELECT l.*, n.title AS source_title FROM links l JOIN notes n ON n.path = l.path "
                f"WHERE l.kind IN ('note','embed') AND l.target_norm IN ({q}) AND l.path != ? "
                f"ORDER BY l.path, l.line", (*candidates, path))]
        # Keep only links that actually resolve here (a same-named note elsewhere may win).
        return [r for r in rows if (self.resolve(r["target"], from_path=r["path"]) or [None])[0] == path]

    def citing(self, key: str) -> list[dict]:
        """Every citation of ``key`` with its note and context."""
        with self._session() as c:
            return [dict(r) for r in c.execute(
                "SELECT l.path, l.line, l.context, l.locator, l.page, n.title AS source_title, n.type, n.author "
                "FROM links l JOIN notes n ON n.path = l.path WHERE l.kind = 'cite' AND l.target = ? "
                "ORDER BY n.path, l.line", (key,))]

    def paper_notes(self, key: str) -> list[dict]:
        with self._session() as c:
            return [dict(r) for r in c.execute(
                "SELECT path, title, type, author, updated FROM notes WHERE paper = ? ORDER BY path", (key,))]

    def paper_note_map(self) -> dict[str, str]:
        """citation key -> path of its paper note (the shortest path if there are several)."""
        with self._session() as c:
            rows = c.execute("SELECT paper, path FROM notes WHERE paper IS NOT NULL ORDER BY length(path) DESC, path DESC")
            return {r["paper"]: r["path"] for r in rows}

    def cited_keys(self, path: str | None = None) -> list[str]:
        with self._session() as c:
            if path:
                rows = c.execute("SELECT DISTINCT target FROM links WHERE kind = 'cite' AND path = ? ORDER BY target",
                                 (path,))
            else:
                rows = c.execute("SELECT DISTINCT target FROM links WHERE kind = 'cite' ORDER BY target")
            return [r["target"] for r in rows]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        q = _fts_query(query)
        if not q:
            return []
        with self._session() as c:
            try:
                return [dict(r) for r in c.execute(
                    "SELECT path, title, snippet(notes_fts, 2, '<mark>', '</mark>', '…', 12) AS snippet "
                    "FROM notes_fts WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?", (q, limit))]
            except sqlite3.OperationalError:
                return []

    def snapshot(self) -> dict:
        """Canonical dump of all derived data (used by tests: incremental == rebuild)."""
        with self._session() as c:
            out = {}
            for t, cols in (("notes", "path, stem, id, title, type, paper, author, created, updated, hash"),
                            ("links", "path, kind, target, target_norm, heading, alias, locator, page, prefix, "
                                      "suppress_author, grp, line, context"),
                            ("tags", "path, tag"), ("headings", "path, level, text, line"),
                            ("notes_fts", "path, title, body")):
                out[t] = sorted(tuple(r) for r in c.execute(f"SELECT {cols} FROM {t}"))
            return out

    def stats(self) -> dict:
        with self._session() as c:
            return {"notes": c.execute("SELECT count(*) FROM notes").fetchone()[0],
                    "links": c.execute("SELECT count(*) FROM links").fetchone()[0]}


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fts_query(query: str) -> str:
    words = re.findall(r"\w+", query or "", re.UNICODE)
    return " ".join(f'"{w}"*' for w in words)


__all__ = ["NotesIndex", "content_hash"]
