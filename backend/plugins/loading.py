"""Loading a plugin's code and finding its files: its Python module (under a fresh name each time,
so a restart picks up changed code), the frontend files the app may fetch, and when its files last
changed (dev plugins restart then)."""

from __future__ import annotations

import importlib.util
import itertools
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from werkzeug.routing import Map

from . import build, deps
from .context import Plugin
from .errors import PluginError
from .jobs import Runner
from .manifest import Manifest
from .runtime import Faults, Worker


@dataclass
class Entry:
    """A plugin found on disk."""

    id: str
    folder: Path
    source: str  # "bundled" | "user"
    manifest: Manifest | None = None
    problem: str | None = None  # why the manifest cannot be used
    replaces_bundled: bool = False


@dataclass
class Active:
    """A plugin that is running."""

    entry: Entry
    plugin: Plugin
    worker: Worker
    faults: Faults = field(default_factory=Faults)
    module: ModuleType | None = None
    modname: str | None = None
    handler: logging.Handler | None = None
    urls: Map | None = None
    started: str = ""  # when it started: the app loads its frontend again when this changes
    runner: Runner | None = None  # its jobs and schedules (web app only)
    pypath: str | None = None  # where its Python packages are, on sys.path while it runs
    bundle: Path | None = None  # its TypeScript frontend, built
    stamp: float = 0.0  # newest change in its folder (dev plugins: reloaded when it changes)


_LOADS = itertools.count(1)  # each load gets its own module name, also with two hosts in one process


def load_module(pid: str, path: Path) -> tuple[ModuleType, str]:
    """Import the plugin's backend (a package folder or a .py file); returns it and its name."""
    modname = f"_synapse_plugin_{pid.replace('-', '_')}_{next(_LOADS)}"
    if path.is_dir():
        spec = importlib.util.spec_from_file_location(
            modname, path / "__init__.py", submodule_search_locations=[str(path)]
        )
    else:
        spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise PluginError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        forget_modules(modname)
        raise
    return module, modname


def forget_modules(modname: str) -> None:
    for name in [n for n in sys.modules if n == modname or n.startswith(modname + ".")]:
        del sys.modules[name]


def asset_path(man: Manifest, bundle: Path | None, rel: str) -> Path | None:
    """The file of a plugin's frontend the app asks for: its built bundle, its styles file, or a
    file in the folder of its frontend module (so the module can import its own modules). The
    backend's code and the manifest are never served."""
    if bundle and rel == build.BUNDLED:
        return bundle
    folder = man.folder.resolve()
    wanted = (folder / rel).resolve()
    allowed = [((folder / man.frontend).resolve().parent, True)] if man.frontend else []
    if man.styles:
        allowed.append(((folder / man.styles).resolve(), False))
    for root, is_dir in allowed:
        inside = wanted == root or (is_dir and root in wanted.parents)
        if inside and wanted.is_file() and not (is_dir and _is_backend(man, wanted)):
            return wanted
    return None


def _is_backend(man: Manifest, path: Path) -> bool:
    """Whether ``path`` is the plugin's Python code or its manifest (a frontend module kept at the
    top of the plugin's folder must not make them readable)."""
    if path.name == "manifest.json" or path.suffix == ".py":
        return True
    backend = man.backend_path()
    return bool(backend and (path == backend or backend in path.parents))


def newest(folder: Path) -> float:
    """The newest modification time of the plugin's own files (not built or installed ones)."""
    latest = 0.0
    skip = {"__pycache__", "node_modules", build.BUILD_DIR, deps.DIR, ".git"}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            try:
                latest = max(latest, os.stat(os.path.join(root, f)).st_mtime)
            except OSError:
                continue
    return latest
