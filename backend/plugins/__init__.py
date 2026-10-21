"""Plugins: code outside Synapse that adds features through a small, versioned API.

This module is the public API for plugins' Python code: a plugin imports only from
``backend.plugins`` (``Plugin``, ``PluginError``, ``EVENTS``, ``API_VERSION``). Everything else in
``backend`` may change between versions. See docs/plugins.md.

Inside Synapse: ``host.py`` finds, starts and stops plugins, ``manifest.py`` reads their
``manifest.json``, ``context.py`` is the ``Plugin`` object they get, ``storage.py`` what they store,
``library.py`` what they may read, ``runtime.py`` their log, worker thread and failure count.
"""

from .context import EVENTS, Plugin, PluginError, ToolSpec
from .host import PluginHost
from .library import LibraryReader
from .manifest import API_VERSION, Manifest, ManifestError
from .storage import JsonStore, SqliteStore, StorageError

__all__ = [
    "API_VERSION",
    "EVENTS",
    "JsonStore",
    "LibraryReader",
    "Manifest",
    "ManifestError",
    "Plugin",
    "PluginError",
    "PluginHost",
    "SqliteStore",
    "StorageError",
    "ToolSpec",
]
