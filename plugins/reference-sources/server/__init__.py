"""The bundled scholarly reference providers, preserving their existing fallback policy."""

from backend.online.http import LookupFailed
from backend.plugins import Plugin, PluginError

from .providers import fetch


def activate(plugin: Plugin) -> None:
    @plugin.reference_source("scholarly", title="Semantic Scholar, OpenAlex, Crossref and arXiv")
    def references(query: dict) -> dict:
        settings = plugin.synapse.settings
        try:
            source, refs = fetch(
                query["item"],
                email=query["email"],
                openalex_key=settings.get("openalex_api_key") or "",
                s2_key=settings.get("semantic_scholar_api_key") or "",
                fresh=query["fresh"],
            )
            return {"source": source, "references": refs}
        except LookupFailed as e:
            raise PluginError(str(e)) from e
