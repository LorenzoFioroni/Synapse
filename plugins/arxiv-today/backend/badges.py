"""Feed badges and their matched-author explanations, computed from current settings."""

import json
from datetime import date

from .following import followed_names


def paper_badges(authors: list[str], reasons: list, followed: list[str]) -> list[dict]:
    badges = []
    matches = followed_names(authors, followed)
    if matches:
        badges.append(
            {"label": "Following", "text": "Following: " + "; ".join(matches), "tone": "gold", "icon": "star"}
        )
    if any(isinstance(reason, dict) and reason.get("kind") == "cites_own" for reason in reasons):
        badges.append(
            {
                "label": "Cites you",
                "text": "Cites a paper you authored in your library",
                "tone": "red",
                "icon": "megaphone",
            }
        )
    return badges


def day_variants(rows: list[dict], followed: list[str]) -> list[dict]:
    """Aggregate non-dismissed matches while keeping unread counts independent."""
    days: dict[str, dict] = {}
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        day = row["day"]
        entry = days.setdefault(day, {"id": day, "title": date.fromisoformat(day).strftime("%d %b %Y"), "badge": 0})
        per_label = counts.setdefault(day, {})
        if row["dismissed"]:
            continue
        entry["badge"] += not row["seen"]
        for badge in paper_badges(json.loads(row["authors"]), json.loads(row["reasons"]), followed):
            label = badge["label"]
            per_label[label] = per_label.get(label, 0) + 1
    for day, entry in days.items():
        badges = []
        for label, count in counts[day].items():
            following = label == "Following"
            text = f"{count} paper{'s' if count != 1 else ''} " + (
                "by authors you follow" if following else "citing you"
            )
            badges.append(
                {
                    "label": label,
                    "text": text,
                    "tone": "gold" if following else "red",
                    "icon": "star" if following else "megaphone",
                }
            )
        entry["highlights"] = sorted(badges, key=lambda b: b["label"] != "Following")
    return list(days.values())
