"""Reference providers offered by running plugins, in deterministic priority order."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..online.http import LookupFailed
from ..references.sources import FetchFailed
from .errors import PluginError

# These qualified IDs belong to the providers Synapse already stored under bare names.
# Keep both disk and HTTP identities unchanged when their bundled plugin becomes contributed.
LEGACY_SOURCES = {
    "reference-sources.scholarly.semanticscholar": ("semanticscholar", "Semantic Scholar"),
    "reference-sources.scholarly.openalex": ("openalex", "OpenAlex"),
    "reference-sources.scholarly.crossref": ("crossref", "Crossref"),
    "reference-sources.scholarly.arxiv-html": ("arxiv-html", "arXiv (HTML version)"),
}
SUBSOURCE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def clean_subsources(value: dict[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(key, str)
        and SUBSOURCE_ID.fullmatch(key)
        and isinstance(title, str)
        and title.strip()
        and len(title) <= 80
        for key, title in value.items()
    ):
        raise PluginError("Reference sub-sources map lowercase IDs to nonempty titles (at most 80 characters)")
    return {key: title.strip() for key, title in value.items()}


@dataclass
class ReferenceSource:
    id: str
    title: str
    fn: Callable[[dict], dict | None]
    order: int
    subsources: dict[str, str] = field(default_factory=dict)


def resolve(sources: list[tuple[str, bool, ReferenceSource]], run: Callable, query: dict) -> tuple[str, list[dict]]:
    if not sources:
        raise LookupFailed("No reference sources are on: turn on Reference Sources in Settings → Plugins.")
    errors = []
    for pid, core, source in sorted(sources, key=lambda x: (x[2].order, x[0], x[2].id)):
        try:
            result = run(pid, f"Reference source {source.id}", source.fn, {**query, "item": dict(query["item"])})
            if result is None:
                continue
            if not isinstance(result, dict) or not isinstance(result.get("references"), list):
                raise PluginError("A reference source must return an object with a references list, or None")
            if not all(isinstance(ref, dict) for ref in result["references"]):
                raise PluginError("Each reference must be an object")
            if result["references"]:
                if source.subsources:
                    selected = result.get("source")
                    if not isinstance(selected, str) or selected not in source.subsources:
                        raise PluginError("Reference result must name one of its declared sub-sources")
                    name = f"{pid}.{source.id}.{selected}"
                    name = LEGACY_SOURCES.get(name, (name, ""))[0]
                else:
                    name = str(result.get("source") or source.id) if core else f"{pid}.{source.id}"
                return name, result["references"]
        except Exception as e:
            reason = str(e) if isinstance(e, PluginError) else type(e).__name__  # faults are logged
            errors.append(f"{pid}.{source.id}: {reason}")
    if errors:
        raise FetchFailed("Could not get the references (" + "; ".join(errors) + ").")
    return "none", []


def source_title(name: str, sources: list[tuple[str, ReferenceSource]]) -> str:
    """Historical titles work with providers off; other titles come from running declarations."""
    for qualified, (legacy, title) in LEGACY_SOURCES.items():
        if name in (qualified, legacy):
            return title
    if name == "none":
        return "no service"
    for pid, source in sources:
        base = f"{pid}.{source.id}"
        if name == base:
            return source.title
        for key, title in source.subsources.items():
            if name == f"{base}.{key}":
                return title
    return name
