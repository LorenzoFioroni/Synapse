"""How the match compares with the earlier score on the days kept: where the papers the user added to
the library, and those they dismissed, ranked under each, and which papers moved most. Read
through Claude's ``match_report`` tool to check and tune the weights on real days."""

from __future__ import annotations

import json
from collections import defaultdict
from statistics import median


def _ranks(rows: list[dict], column: str) -> dict[str, int]:
    """Rank within each day (1 = top) by ``column``."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)
    out = {}
    for day_rows in by_day.values():
        ordered = sorted(day_rows, key=lambda r: (-(r[column] or 0), r["arxiv"]))
        for i, r in enumerate(ordered, 1):
            out[r["arxiv"]] = i
    return out


def compare(rows: list[dict], in_library: set[str], top: int = 10) -> dict:
    """``rows``: stored papers with ``score`` (the match) and ``score_v1`` (the earlier score)."""
    rows = [r for r in rows if r["score"] is not None and r["score_v1"] is not None]
    if not rows:
        return {"days": 0, "papers": 0, "note": "Nothing scored by both models yet."}
    old, new = _ranks(rows, "score_v1"), _ranks(rows, "score")
    added = [r for r in rows if r["arxiv"] in in_library]
    dismissed = [r for r in rows if r["dismissed"]]

    def side(group: list[dict]) -> dict:
        if not group:
            return {"papers": 0}
        return {
            "papers": len(group),
            "median_rank_earlier": median(old[r["arxiv"]] for r in group),
            "median_rank_match": median(new[r["arxiv"]] for r in group),
            f"in_top_{top}_earlier": sum(old[r["arxiv"]] <= top for r in group),
            f"in_top_{top}_match": sum(new[r["arxiv"]] <= top for r in group),
        }

    def entry(r: dict) -> dict:
        return {
            "arxiv": r["arxiv"],
            "day": r["day"],
            "title": r["title"],
            "rank_earlier": old[r["arxiv"]],
            "rank_match": new[r["arxiv"]],
            "earlier": r["score_v1"],
            "match": r["score"],
            "tier": r["tier"],
            "added": r["arxiv"] in in_library,
            "dismissed": bool(r["dismissed"]),
            "why": [x.get("text") for x in json.loads(r["reasons"] or "[]")],
        }

    moved = sorted(rows, key=lambda r: new[r["arxiv"]] - old[r["arxiv"]])
    tiers: dict[str, int] = defaultdict(int)
    for r in rows:
        tiers[r["tier"] or "unscored"] += 1
    return {
        "days": len({r["day"] for r in rows}),
        "papers": len(rows),
        "tiers": dict(tiers),
        "added": side(added),
        "dismissed": side(dismissed),
        "rose_most": [entry(r) for r in moved[:top]],
        "fell_most": [entry(r) for r in reversed(moved[-top:])],
    }
