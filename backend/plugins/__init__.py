"""Plugins: code outside Synapse that adds features through a small, versioned API.

This module is the public API for plugins' Python code: a plugin imports only from
``backend.plugins`` (``Plugin``, ``PluginError``, ``HttpError``, ``Offline``, ``EVENTS``,
``API_VERSION``, and the reference writers as ``formats``). Everything else in ``backend`` may
change between versions. See docs/plugins.md.

Inside Synapse: ``host.py`` finds, starts and stops plugins, ``manifest.py`` reads their
``manifest.json``, ``context.py`` is the ``Plugin`` object they get, ``storage.py`` what they store,
``library.py`` what they may read, ``runtime.py`` their log, worker thread and failure count,
``http.py`` how they reach the web, ``jobs.py`` and ``schedule.py`` their background work,
``deps.py`` their Python packages, ``build.py`` TypeScript frontends, ``scaffold.py`` new plugins,
``formats.py`` the reference writers plugins may use.
"""

from . import formats
from .context import EVENTS, Plugin, ToolSpec
from .errors import PluginError
from .host import PluginHost
from .http import HttpError, Offline, Response
from .library import LibraryReader
from .manifest import API_VERSION, Manifest, ManifestError
from .storage import JsonStore, SqliteStore, StorageError

__all__ = [
    "API_VERSION",
    "EVENTS",
    "formats",
    "HttpError",
    "Offline",
    "Response",
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
