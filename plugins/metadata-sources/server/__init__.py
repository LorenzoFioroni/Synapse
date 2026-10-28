"""Metadata Sources: scholarly providers using only the public plugin API."""

from backend.plugins import Plugin

from .providers import Providers


def activate(plugin: Plugin) -> None:
    providers = Providers(plugin.http)
    plugin.on_unload(providers.close)
    plugin.metadata_source("scholarly", title="Crossref, OpenAlex, arXiv and Open Library", order=100)(
        providers.resolve
    )
