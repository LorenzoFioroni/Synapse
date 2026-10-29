"""Pack selected earlier note text and nearby text into the existing model prefix budget."""

from __future__ import annotations

CONTEXT_CHARS = 1200
_GAP = "\n\n<!-- Earlier sections omitted. -->\n\n"


def tail(text: str, size: int) -> str:
    if len(text) <= size:
        return text
    cut = text.find("\n", len(text) - size)
    return text[cut + 1 :] if 0 <= cut < len(text) - 1 else text[-size:]


def _strings(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    # Ignore oversized snippets instead of cutting through an equation or macro definition.
    return [v.strip() for v in value[:6] if isinstance(v, str) and 0 < len(v.strip()) <= limit]


def prefix_with_context(prefix: str, context: object, budget: int) -> str:
    nearby = tail(prefix, budget)
    if not isinstance(context, dict):
        return nearby
    title = context.get("title")
    titles = ["# " + title.strip()] if isinstance(title, str) and 0 < len(title.strip()) <= 160 else []
    snippets = titles + _strings(context.get("excerpts"), 420) + _strings(context.get("headings"), 180)
    # This tail is guaranteed to remain after adding the bounded header. Avoid duplicating
    # snippets already in it, without losing a title when the final tail becomes shorter.
    guaranteed = prefix[-max(1, budget - CONTEXT_CHARS) :]
    selected: list[str] = []
    used = len(_GAP)
    for snippet in snippets:
        if snippet in guaranteed or snippet in selected:
            continue
        size = len(snippet) + (2 if selected else 0)
        if used + size > min(CONTEXT_CHARS, budget):
            continue
        selected.append(snippet)
        used += size
    if not selected or all(s in nearby for s in selected):
        return nearby
    header = "\n\n".join(selected) + _GAP
    return header + tail(prefix, budget - len(header))
