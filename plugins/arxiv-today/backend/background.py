"""The background the match measures rarity against (scoring.Background): which works the papers
announced in the user's categories cite, and who writes them, over BACKGROUND_DAYS (longer than
the listings are kept, so the counts outlive forgotten days)."""

from __future__ import annotations

import json
from collections.abc import Iterable

from . import scoring

BACKGROUND_DAYS = 180
CHUNK = 500  # keys per query (SQLite allows 999 parameters)


def author_keys(authors: Iterable[str]) -> set[str]:
    keys = set()
    for a in authors:
        n = scoring.name(a)
        if n:
            keys.add(n.short)
            if n.full:
                keys.add(n.full)
    return keys


def record_paper(c, arxiv: str, day: str, authors: list[str]) -> None:
    """A listed paper, its authors counted (``c``: an open session)."""
    c.execute("INSERT OR IGNORE INTO bg_papers (arxiv, day) VALUES (?, ?)", (arxiv, day))
    c.executemany(
        "INSERT OR IGNORE INTO bg_authors (arxiv, key) VALUES (?, ?)", [(arxiv, k) for k in author_keys(authors)]
    )


def record_refs(c, arxiv: str, refs: list[dict]) -> None:
    keys = set().union(*(scoring.ref_keys(r) for r in refs)) if refs else set()
    c.execute("UPDATE bg_papers SET refs = ? WHERE arxiv = ?", (len(refs), arxiv))
    c.execute("DELETE FROM bg_refs WHERE arxiv = ?", (arxiv,))
    c.executemany("INSERT OR IGNORE INTO bg_refs (arxiv, key) VALUES (?, ?)", [(arxiv, k) for k in keys])


def backfill(db) -> int:
    """Fill the background once from the listings already kept (after the upgrade that added it)."""
    if db.query("SELECT 1 FROM bg_papers LIMIT 1"):
        return 0
    rows = db.query("SELECT arxiv, day, authors, refs FROM papers")
    with db.session() as c:
        for r in rows:
            record_paper(c, r["arxiv"], r["day"], json.loads(r["authors"]))
            if r["refs"] is not None:
                record_refs(c, r["arxiv"], json.loads(r["refs"]))
    return len(rows)


def forget(db, before: str) -> None:
    with db.session() as c:
        old = [r[0] for r in c.execute("SELECT arxiv FROM bg_papers WHERE day < ?", (before,))]
        for i in range(0, len(old), CHUNK):
            part = old[i : i + CHUNK]
            marks = ",".join("?" * len(part))
            for table in ("bg_refs", "bg_authors", "bg_papers"):
                c.execute(f"DELETE FROM {table} WHERE arxiv IN ({marks})", part)


def _counts(db, table: str, keys: Iterable[str]) -> dict[str, int]:
    keys = sorted(set(keys))
    out: dict[str, int] = {}
    for i in range(0, len(keys), CHUNK):
        part = keys[i : i + CHUNK]
        marks = ",".join("?" * len(part))
        for r in db.query(f"SELECT key, COUNT(*) AS n FROM {table} WHERE key IN ({marks}) GROUP BY key", part):
            out[r["key"]] = r["n"]
    return out


def load(db, ref_keys: Iterable[str], names: Iterable[str]) -> scoring.Background:
    """Counts for the keys a scoring run needs: those of the library's papers and their references
    (``ref_keys``) and the authors of the papers being scored (``names``)."""
    papers = db.query("SELECT COUNT(*) AS n FROM bg_papers WHERE refs > 0")[0]["n"]
    authored = db.query("SELECT COUNT(*) AS n FROM bg_papers")[0]["n"]
    return scoring.Background(
        papers=papers,
        cites=_counts(db, "bg_refs", ref_keys),
        authored=authored,
        authors=_counts(db, "bg_authors", author_keys(names)),
    )
