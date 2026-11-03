"""Bounded, plain-text badges shared by source papers and retained-day navigation."""


def highlights(value) -> list[dict]:
    if not isinstance(value, list | tuple):
        return []
    out = []
    for badge in value[:8]:
        if not isinstance(badge, dict) or not badge.get("label"):
            continue
        out.append(
            {
                "label": str(badge["label"])[:40],
                "text": str(badge.get("text") or badge["label"])[:300],
                "tone": badge.get("tone") if badge.get("tone") in ("gold", "teal") else "gold",
                "icon": badge.get("icon") if badge.get("icon") in ("star", "link") else "star",
            }
        )
    return out
