"""Periodic metadata audit, including journal versions announced by arXiv."""

from __future__ import annotations

import re
import unicodedata

from backend.plugins import Offline, Plugin, PluginError

CHECK_FIELDS = ("title", "authors", "year", "journal", "volume", "issue", "pages", "publisher")


def _plain(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)).lower())


def _real_doi(value) -> str:
    doi = str(value or "").lower().removeprefix("https://doi.org/")
    return "" if doi.startswith("10.48550/arxiv.") else doi


def activate(plugin: Plugin) -> None:
    def audit(papers: list[dict]) -> dict:
        out = {}
        for paper in papers:
            current = plugin.library.get(paper["item_id"]) or {}
            identifier = f"arXiv:{paper['arxiv']}" if paper.get("arxiv") else paper.get("doi")
            if not identifier:
                out[paper["key"]] = "Needs a DOI or arXiv ID"
                continue
            try:
                found = plugin.services.lookup(identifier)
            except Offline as error:
                plugin.log.info("Could not check %s: %s", identifier, error)
                out[paper["key"]] = plugin.fields.LATER
                continue
            except PluginError as error:
                out[paper["key"]] = f"Could not verify: {error}"
                continue
            published = _real_doi(found.get("doi"))
            current_doi = _real_doi(current.get("doi"))
            if paper.get("arxiv") and published and published != current_doi:
                out[paper["key"]] = (f"Published version: {published}", f"https://doi.org/{published}")
                continue
            changed = [
                name for name in CHECK_FIELDS if found.get(name) and _plain(found[name]) != _plain(current.get(name))
            ]
            out[paper["key"]] = f"Review: {', '.join(changed)}" if changed else "Up to date"
        return out

    plugin.fields.declare(
        "metadata_check",
        type="text",
        label="Metadata Check",
        description="Current scholarly metadata compared with this library record",
        source="Metadata Sources",
        refresh="3d",
        refresher=audit,
    )
