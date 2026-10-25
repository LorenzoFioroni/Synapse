"""Metadata Sources: the scholarly providers used by Synapse's metadata lookups."""

from backend.online.http import LookupFailed
from backend.plugins import Plugin, PluginError

from .providers import pool, resolve


def activate(plugin: Plugin) -> None:
    plugin.on_unload(lambda: pool.shutdown(wait=False))

    @plugin.metadata_source("scholarly", title="Crossref, OpenAlex, arXiv and Open Library", order=100)
    def lookup(query: dict) -> dict:
        try:
            return resolve(query)
        except LookupFailed as e:
            raise PluginError(str(e)) from e
