"""The plugin's database (plugin-data/arxiv-today/feed.sqlite). Each entry brings the schema one
version up; only ever append."""

SCHEMA = [
    """
    CREATE TABLE papers (
        arxiv TEXT PRIMARY KEY,
        day TEXT NOT NULL,               -- announcement day, YYYY-MM-DD
        kind TEXT NOT NULL,              -- new | cross | replace | replace-cross
        title TEXT NOT NULL DEFAULT '',
        authors TEXT NOT NULL DEFAULT '[]',     -- JSON list of names
        abstract TEXT NOT NULL DEFAULT '',
        categories TEXT NOT NULL DEFAULT '[]',  -- JSON list
        refs TEXT,                       -- JSON list of references once read; NULL: not yet
        score REAL,
        reasons TEXT NOT NULL DEFAULT '[]',     -- JSON, as plugin.services.connections gives them
        seen INTEGER NOT NULL DEFAULT 0,
        dismissed INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX papers_day ON papers (day);
    CREATE TABLE days (
        day TEXT PRIMARY KEY,
        fetched_at TEXT NOT NULL,
        categories TEXT NOT NULL,        -- what was asked for, comma-separated
        notified INTEGER NOT NULL DEFAULT 0
    );
    """,
]
