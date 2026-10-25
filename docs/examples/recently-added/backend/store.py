"""The plugin's database schema. Each entry brings the schema one version up; new versions are only
ever appended, so every user's database can be brought up to date."""

SCHEMA = [
    # 1
    """
    CREATE TABLE added (
        item_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        added_at TEXT NOT NULL
    );
    CREATE INDEX added_at ON added (added_at);
    """,
]
