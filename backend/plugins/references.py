"""Reference providers offered by running plugins, in deterministic priority order."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..online.http import LookupFailed
from ..references.sources import FetchFailed
from .errors import PluginError


@dataclass
class ReferenceSource:
    id: str
    title: str
    fn: Callable[[dict], dict | None]
    order: int


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
                name = str(result.get("source") or source.id) if core else f"{pid}.{source.id}"
                return name, result["references"]
        except Exception as e:
            errors.append(f"{pid}.{source.id}: {e}")
    if errors:
        raise FetchFailed("Could not get the references (" + "; ".join(errors) + ").")
    return "none", []
