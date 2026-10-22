"""The fetched reference lists, in their own database (``data/citations.db``) so the library database
is never touched. Deleting the file loses the lists, which can be fetched again."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..library.db import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetches (
    item_id TEXT PRIMARY KEY,
    source TEXT,              -- semanticscholar | openalex | crossref | arxiv-html | none
    fetched_at TEXT,
    doi TEXT, arxiv_id TEXT,  -- what the lookup used
    count INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS refs (
    item_id TEXT NOT NULL,
    position INTEGER NOT NULL,   -- order in the reference list
    doi TEXT, arxiv_id TEXT, openalex_id TEXT, s2_id TEXT,
    title TEXT, authors TEXT, year INTEGER, venue TEXT,
    raw TEXT,                    -- the citation as printed, when that is all the source gives
    PRIMARY KEY (item_id, position)
);
CREATE INDEX IF NOT EXISTS idx_refs_doi ON refs(doi);
CREATE INDEX IF NOT EXISTS idx_refs_arxiv ON refs(arxiv_id);
"""
# The fields of a reference (the columns of ``refs`` besides its place in the list).
REF_FIELDS = ("doi", "arxiv_id", "openalex_id", "s2_id", "title", "authors", "year", "venue", "raw")


class CitationStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        with self._session() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _session(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save(self, item_id: str, source: str, refs: list[dict], *, doi=None, arxiv_id=None) -> None:
        with self._session() as c:
            c.execute("DELETE FROM refs WHERE item_id = ?", (item_id,))
            c.executemany(
                f"INSERT INTO refs (item_id, position, {', '.join(REF_FIELDS)}) VALUES (?, ?, {', '.join('?' * len(REF_FIELDS))})",
                [(item_id, i, *(r.get(k) for k in REF_FIELDS)) for i, r in enumerate(refs)],
            )
            c.execute(
                "INSERT OR REPLACE INTO fetches (item_id, source, fetched_at, doi, arxiv_id, count, error) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (item_id, source, utcnow(), doi, arxiv_id, len(refs)),
            )

    def save_error(self, item_id: str, message: str) -> None:
        """Remember a failed fetch without losing references fetched earlier."""
        with self._session() as c:
            if c.execute("SELECT 1 FROM fetches WHERE item_id = ?", (item_id,)).fetchone():
                c.execute("UPDATE fetches SET error = ? WHERE item_id = ?", (message, item_id))
            else:
                c.execute(
                    "INSERT INTO fetches (item_id, source, fetched_at, count, error) VALUES (?, 'none', ?, 0, ?)",
                    (item_id, utcnow(), message),
                )

    def fetch_info(self, item_id: str) -> dict | None:
        with self._session() as c:
            r = c.execute("SELECT * FROM fetches WHERE item_id = ?", (item_id,)).fetchone()
            return dict(r) if r else None

    def fetched_ids(self) -> set[str]:
        with self._session() as c:
            return {r[0] for r in c.execute("SELECT item_id FROM fetches WHERE error IS NULL")}

    def references(self, item_id: str) -> list[dict]:
        with self._session() as c:
            return [dict(r) for r in c.execute("SELECT * FROM refs WHERE item_id = ? ORDER BY position", (item_id,))]

    def all_refs(self) -> list[dict]:
        with self._session() as c:
            return [dict(r) for r in c.execute("SELECT * FROM refs ORDER BY item_id, position")]

    def citing(self, doi: str | None, arxiv_id: str | None) -> set[str]:
        """The items whose references include this DOI or arXiv id (normalised forms)."""
        out: set[str] = set()
        with self._session() as c:
            if doi:
                out |= {r[0] for r in c.execute("SELECT DISTINCT item_id FROM refs WHERE doi = ?", (doi,))}
            if arxiv_id:
                out |= {r[0] for r in c.execute("SELECT DISTINCT item_id FROM refs WHERE arxiv_id = ?", (arxiv_id,))}
        return out

    def remove(self, item_ids) -> None:
        with self._session() as c:
            for i in item_ids:
                c.execute("DELETE FROM refs WHERE item_id = ?", (i,))
                c.execute("DELETE FROM fetches WHERE item_id = ?", (i,))
