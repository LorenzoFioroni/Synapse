"""SQLite access and schema migrations for the library database."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .fields import DEFAULT_ITEM_TYPE, FIELDS, INTERNAL_COLUMNS


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def session(self):
        """Connection that commits on success and always closes."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ schema
    def migrate(self) -> None:
        with self.session() as c:
            cols = {**INTERNAL_COLUMNS, **{f.key: f.sql for f in FIELDS}}
            c.execute(f"CREATE TABLE IF NOT EXISTS papers ({', '.join(f'{k} {v}' for k, v in cols.items())})")
            existing = {r["name"] for r in c.execute("PRAGMA table_info(papers)")}
            for key, sql in cols.items():
                if key not in existing:
                    c.execute(f"ALTER TABLE papers ADD COLUMN {key} {sql.replace('PRIMARY KEY', '').strip()}")

            c.execute("""
                CREATE TABLE IF NOT EXISTS attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
                    name TEXT,
                    url TEXT,
                    type TEXT
                )""")
            att_cols = {r["name"] for r in c.execute("PRAGMA table_info(attachments)")}
            if "date_added" not in att_cols:
                c.execute("ALTER TABLE attachments ADD COLUMN date_added TEXT")

            # Organisation: folders (a tree; each item in at most one), tags (many per item),
            # smart folders (saved searches).
            c.execute("""
                CREATE TABLE IF NOT EXISTS folders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    parent_id INTEGER REFERENCES folders(id),
                    position INTEGER DEFAULT 0,
                    date_added TEXT
                )""")
            c.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    color TEXT
                )""")
            c.execute("""
                CREATE TABLE IF NOT EXISTS paper_tags (
                    paper_id TEXT NOT NULL,
                    tag_id INTEGER NOT NULL,
                    PRIMARY KEY (paper_id, tag_id)
                )""")
            c.execute("""
                CREATE TABLE IF NOT EXISTS smart_folders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    query TEXT NOT NULL,
                    mode TEXT DEFAULT 'all',
                    position INTEGER DEFAULT 0,
                    date_added TEXT
                )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_papers_folder ON papers(folder_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_paper_tags_tag ON paper_tags(tag_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_title ON papers(title)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_papers_deleted ON papers(deleted_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_att_paper ON attachments(paper_id)")

            # Backfill values older versions never stored.
            now = utcnow()
            c.execute("UPDATE papers SET date_added = ? WHERE date_added IS NULL OR date_added = ''", (now,))
            c.execute("UPDATE papers SET date_modified = date_added WHERE date_modified IS NULL OR date_modified = ''")
            c.execute("UPDATE papers SET item_type = ? WHERE item_type IS NULL OR item_type = ''", (DEFAULT_ITEM_TYPE,))
            c.execute("UPDATE attachments SET date_added = ? WHERE date_added IS NULL", (now,))
            # Orphaned attachments from older versions that deleted papers without cascading.
            c.execute("DELETE FROM attachments WHERE paper_id NOT IN (SELECT id FROM papers)")
            c.execute("DELETE FROM paper_tags WHERE paper_id NOT IN (SELECT id FROM papers)")
            c.execute("UPDATE papers SET folder_id = NULL WHERE folder_id IS NOT NULL "
                      "AND folder_id NOT IN (SELECT id FROM folders)")
