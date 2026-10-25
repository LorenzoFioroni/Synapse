"""Reference fetching entry point; providers are supplied by the composition layer.

Citation storage and matching remain available when no provider plugin is running.
"""

from __future__ import annotations

from collections.abc import Callable

from ..library.identifiers import identifiers
from ..online.http import LookupFailed


class FetchFailed(LookupFailed):
    """Providers failed; a later attempt may succeed."""


def fetch(
    item: dict, *, email: str = "", fresh: bool = False, sources: Callable | None = None
) -> tuple[str, list[dict]]:
    if not any(identifiers(item)):
        raise LookupFailed("This item has no DOI or arXiv ID to look up its references with.")
    if sources is None:
        raise LookupFailed("No reference sources are on: turn on Reference Sources in Settings → Plugins.")
    return sources({"item": dict(item), "email": email, "fresh": fresh})
