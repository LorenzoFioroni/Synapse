import sqlite3

columns = [
    'citation_key', 'pmid', 'pmcid', 'issn', 'archive', 'archive_id',
    'short_title', 'language', 'library_catalog', 'call_number', 'license',
    'extra', 'series', 'edition', 'place'
]

conn = sqlite3.connect('papers.db')
c = conn.cursor()

# Get existing columns
c.execute("PRAGMA table_info(papers)")
existing = [row[1] for row in c.fetchall()]

for col in columns:
    if col not in existing:
        print(f"Adding {col}")
        c.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT")

conn.commit()
conn.close()
