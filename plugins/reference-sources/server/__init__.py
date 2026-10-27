"""Scholarly reference providers using only the public plugin API."""

from backend.plugins import Plugin

from .client import Client
from .providers import fetch


def activate(plugin: Plugin) -> None:
    http = Client(plugin.http)
    plugin.on_unload(http.close)

    @plugin.reference_source(
        "scholarly",
        title="Semantic Scholar, OpenAlex, Crossref and arXiv",
        subsources={
            "semanticscholar": "Semantic Scholar",
            "openalex": "OpenAlex",
            "crossref": "Crossref",
            "arxiv-html": "arXiv (HTML version)",
        },
    )
    def references(query: dict) -> dict:
        source, refs = fetch(query["item"], http=http, fresh=query["fresh"])
        return {"source": source, "references": refs}
