"""Full-text index of attachment PDFs.

The index lives in its own SQLite file (``data/fulltext.db``) so the library
database stays small; it can be deleted at any time and rebuilt from the PDFs.
SQLite FTS5 is used when available (fast, accent-insensitive, ranked
snippets); otherwise a plain table with LIKE matching is used as a fallback.
"""

from __future__ import annotations

import html
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..library.db import utcnow
from .text import clean_text, extract_text

log = logging.getLogger(__name__)

MARK_OPEN, MARK_CLOSE = "\u0002", "\u0003"


def _fts_quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _like_escape(term: str) -> str:
    """``term`` for ``LIKE … ESCAPE '\\'``: its ``%`` and ``_`` are plain characters."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FulltextIndex:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.fts = True
        self._write_lock = threading.Lock()
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _session(self):
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init(self) -> None:
        with self._session() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS documents (
                attachment_id INTEGER PRIMARY KEY,
                paper_id TEXT,
                file TEXT,
                mtime REAL,
                size INTEGER,
                pages INTEGER,
                chars INTEGER,
                indexed_at TEXT
            )""")
            c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            try:
                self._create_fts(c)
            except sqlite3.OperationalError:
                log.warning("SQLite FTS5 not available; full-text search falls back to LIKE")
                self.fts = False
                c.execute("""CREATE TABLE IF NOT EXISTS content_plain (
                    attachment_id INTEGER PRIMARY KEY, paper_id TEXT, text TEXT)""")
            self._check_content_table(c)

    @staticmethod
    def _create_fts(c: sqlite3.Connection) -> None:
        c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS content USING fts5(
            text, attachment_id UNINDEXED, paper_id UNINDEXED,
            tokenize = "unicode61 remove_diacritics 2")""")

    def _check_content_table(self, c: sqlite3.Connection) -> None:
        """Start over if ``documents`` describes text kept in the other table.

        Whether FTS5 is there is decided at each start (another Python, another SQLite), but
        ``documents`` is shared: its rows would count every file as indexed while the table now
        in use is empty. ``meta`` records which table the rows belong to.
        """
        row = c.execute("SELECT value FROM meta WHERE key = 'content'").fetchone()
        if row is None:  # an index from before this was recorded: look for rows without text
            stale = (
                c.execute(
                    f"SELECT 1 FROM documents WHERE attachment_id NOT IN (SELECT attachment_id FROM {self._table}) "
                    "LIMIT 1"
                ).fetchone()
                is not None
            )
        else:
            stale = row["value"] != self._table
        if stale:
            log.warning(
                "The full-text index was made %s FTS5; the PDFs will be indexed again",
                "without" if self.fts else "with",
            )
            c.execute("DELETE FROM documents")
            for table in ("content", "content_plain"):
                try:
                    c.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:  # not there, or FTS5 is what is missing
                    pass
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('content', ?)", (self._table,))

    @property
    def _table(self) -> str:
        return "content" if self.fts else "content_plain"

    # ----------------------------------------------------------------- writes
    def needs_indexing(self, att_id: int, path: Path) -> bool:
        st = path.stat()
        with self._session() as c:
            row = c.execute("SELECT mtime, size FROM documents WHERE attachment_id = ?", (att_id,)).fetchone()
        return not row or row["mtime"] != st.st_mtime or row["size"] != st.st_size

    def index_file(self, att_id: int, paper_id: str, path: Path) -> int:
        text, pages = extract_text(path)
        text = clean_text(text)
        st = path.stat()
        with self._write_lock, self._session() as c:
            c.execute(f"DELETE FROM {self._table} WHERE attachment_id = ?", (att_id,))
            c.execute(
                f"INSERT INTO {self._table} (text, attachment_id, paper_id) VALUES (?, ?, ?)", (text, att_id, paper_id)
            )
            c.execute(
                "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (att_id, paper_id, path.name, st.st_mtime, st.st_size, pages, len(text), utcnow()),
            )
        return len(text)

    def remove(self, att_ids) -> None:
        with self._write_lock, self._session() as c:
            for a in att_ids:
                c.execute(f"DELETE FROM {self._table} WHERE attachment_id = ?", (a,))
                c.execute("DELETE FROM documents WHERE attachment_id = ?", (a,))

    def indexed_ids(self) -> set[int]:
        with self._session() as c:
            return {r[0] for r in c.execute("SELECT attachment_id FROM documents")}

    def clear(self) -> None:
        with self._write_lock, self._session() as c:
            c.execute(f"DELETE FROM {self._table}")
            c.execute("DELETE FROM documents")
        with self._session() as c:
            c.execute("VACUUM")

    # ------------------------------------------------------------------ reads
    def match(self, term: str, limit: int = 2000) -> dict[str, str]:
        """Map paper_id -> HTML snippet (with <mark>) for documents containing ``term``."""
        term = term.strip()
        if not term:
            return {}
        out: dict[str, str] = {}
        with self._session() as c:
            if self.fts:
                words = term.split()
                q = _fts_quote(term) + ("*" if len(words) == 1 else "")
                try:
                    rows = c.execute(
                        "SELECT paper_id, snippet(content, 0, ?, ?, '…', 14) AS snip FROM content "
                        "WHERE content MATCH ? ORDER BY rank LIMIT ?",
                        (MARK_OPEN, MARK_CLOSE, q, limit),
                    ).fetchall()
                except sqlite3.OperationalError as e:
                    log.info("FTS query failed for %r: %s", term, e)
                    rows = []
                for r in rows:
                    out.setdefault(r["paper_id"], self._to_html(r["snip"]))
            else:
                for r in c.execute(
                    "SELECT paper_id, text FROM content_plain WHERE text LIKE ? ESCAPE '\\' LIMIT ?",
                    (f"%{_like_escape(term)}%", limit),
                ):
                    t = r["text"]
                    i = t.lower().find(term.lower())
                    s = max(0, i - 60)
                    snip = (
                        t[s:i] + MARK_OPEN + t[i : i + len(term)] + MARK_CLOSE + t[i + len(term) : i + len(term) + 60]
                    )
                    out.setdefault(r["paper_id"], self._to_html(("…" if s else "") + snip + "…"))
        return out

    @staticmethod
    def _to_html(snip: str) -> str:
        snip = " ".join((snip or "").split())
        return html.escape(snip).replace(MARK_OPEN, "<mark>").replace(MARK_CLOSE, "</mark>")

    def stats(self) -> dict:
        with self._session() as c:
            row = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(chars), 0), COALESCE(SUM(pages), 0) FROM documents"
            ).fetchone()
        size = Path(self.path).stat().st_size if Path(self.path).exists() else 0
        return {"documents": row[0], "chars": row[1], "pages": row[2], "bytes": size, "fts5": self.fts}


class Indexer:
    """Keeps the full-text index in sync with the attachments, in a background thread."""

    def __init__(self, repo, index: FulltextIndex):
        self.repo = repo
        self.index = index
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._again = False
        self.status: dict[str, Any] = {
            "running": False,
            "total": 0,
            "done": 0,
            "current": "",
            "last_run": None,
            "errors": 0,
        }

    def sync(self, background: bool = True) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._again = True
                return
            if not background:
                self._run()
                return
            self._thread = threading.Thread(target=self._loop, name="fulltext-indexer", daemon=True)
            self._thread.start()

    def rebuild(self) -> None:
        self.index.clear()
        self.sync()

    def wait(self, timeout: float = 30) -> None:
        t = self._thread
        if t:
            t.join(timeout)

    def _loop(self) -> None:
        while True:
            self._run()
            with self._lock:
                if not self._again:
                    return
                self._again = False

    def _run(self) -> None:
        atts = self.repo.all_attachments()
        valid = {a["id"] for a in atts}
        stale = self.index.indexed_ids() - valid
        if stale:
            self.index.remove(stale)
        todo = []
        for a in atts:
            path = self.repo.attachment_path(a)
            if path and path.suffix.lower() == ".pdf" and self.index.needs_indexing(a["id"], path):
                todo.append((a, path))
        self.status.update(running=bool(todo), total=len(todo), done=0, current="", errors=0)  # this run's
        for a, path in todo:
            self.status["current"] = a.get("name") or path.name
            try:
                self.index.index_file(a["id"], a["paper_id"], path)
            except Exception as e:  # keep going on bad files
                log.warning("Indexing %s failed: %s", path, e)
                self.status["errors"] += 1
            self.status["done"] += 1
        self.status.update(running=False, current="", last_run=utcnow())
