"""Metadata sources registered by plugins, in deterministic priority order."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..online.http import LookupFailed
from .errors import PluginError

KINDS = frozenset({"doi", "arxiv", "isbn", "title", "pmid"})


@dataclass
class MetadataSource:
    id: str
    title: str
    fn: Callable[[dict], dict | None]
    kinds: frozenset[str]
    order: int = 100


def metadata_source(sources: dict, id: str, *, title: str, kinds, order: int):
    from .context import TOOL_NAME_RE

    if not TOOL_NAME_RE.fullmatch(id or ""):
        raise PluginError(f"Metadata source id {id!r}: lowercase letters, digits and _")
    selected = frozenset(kinds)
    if not selected or not selected <= KINDS:
        raise PluginError("Metadata source kinds must be doi, arxiv, isbn, title or pmid")
    if id in sources:
        raise PluginError(f"Metadata source {id!r} is registered twice")

    def register(fn: Callable[[dict], dict | None]):
        sources[id] = MetadataSource(id, str(title)[:80], fn, selected, int(order))
        return fn

    return register


def lookup(sources: list[tuple[str, MetadataSource]], run: Callable, query: dict) -> dict:
    matching = [(source.order, pid, source) for pid, source in sources if query["kind"] in source.kinds]
    if not matching:
        raise LookupFailed(
            "No metadata sources are on for this identifier: turn on Metadata Sources in Settings → Plugins."
        )
    errors = []
    for _, pid, source in sorted(matching, key=lambda x: (x[0], x[1], x[2].id)):
        try:
            result = run(pid, f"Metadata source {source.id}", source.fn, dict(query))
            if result is None or result == {}:
                continue
            if not isinstance(result, dict):
                raise PluginError("A metadata source must return an item object or None")
            return dict(result)
        except Exception as e:
            errors.append(str(e))
    raise LookupFailed(errors[-1] if errors else "No metadata source found a match.")
