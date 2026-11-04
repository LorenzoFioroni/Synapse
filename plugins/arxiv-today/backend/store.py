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
    # the match (scoring.py): what announced papers cite and who writes them, kept longer than the
    # listings (BACKGROUND_DAYS), and the score of the earlier model, shown for comparison
    """
    ALTER TABLE papers ADD COLUMN score_v1 REAL;
    ALTER TABLE papers ADD COLUMN tier TEXT;
    UPDATE papers SET score_v1 = score;
    CREATE TABLE bg_papers (
        arxiv TEXT PRIMARY KEY,
        day TEXT NOT NULL,
        refs INTEGER NOT NULL DEFAULT -1     -- references read; -1: not yet
    );
    CREATE INDEX bg_papers_day ON bg_papers (day);
    CREATE TABLE bg_refs (
        arxiv TEXT NOT NULL,
        key TEXT NOT NULL,               -- doi:… | arxiv:… | t:<title words>
        PRIMARY KEY (arxiv, key)
    );
    CREATE INDEX bg_refs_key ON bg_refs (key);
    CREATE TABLE bg_authors (
        arxiv TEXT NOT NULL,
        key TEXT NOT NULL,               -- family|initial and family|first name
        PRIMARY KEY (arxiv, key)
    );
    CREATE INDEX bg_authors_key ON bg_authors (key);
    """,
    # learning (learning.py): what the user did with each paper, the features it was scored on,
    # and the labels taken from them, kept a year (longer than the listings)
    """
    ALTER TABLE papers ADD COLUMN opened_at TEXT;
    ALTER TABLE papers ADD COLUMN passed INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE papers ADD COLUMN features TEXT;
    CREATE TABLE labels (
        arxiv TEXT PRIMARY KEY,
        day TEXT NOT NULL,
        y REAL NOT NULL,                 -- 1: wanted, 0: not wanted
        strength REAL NOT NULL,
        features TEXT NOT NULL           -- JSON, as scoring.score gives them
    );
    CREATE INDEX labels_day ON labels (day);
    """,
]
