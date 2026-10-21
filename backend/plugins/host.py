"""The plugin host: finds plugins, keeps which are on (``data/plugins.json``), starts and stops their
code, and hands them what happens in the library.

Plugins are folders with a ``manifest.json`` in ``data/plugins/`` (installed by the user) and in
the repository's ``plugins/`` (they come with Synapse, off until turned on); one in ``data/plugins``
replaces a bundled one with the same id. Both the web app and the MCP server run a host: the
web app with ``role="app"``, the MCP server Claude starts with ``role="mcp"``. Only the web app
changes which plugins are on; the MCP server follows ``plugins.json`` (``refresh``).

A plugin that fails while starting, or fails ``FAULTS`` times within ``FAULT_WINDOW_S`` (an event
listener, a route or a tool raising anything but ``PluginError``), is turned off and its reason
shown in Settings → Plugins. ``--safe-mode`` starts none.
"""

from __future__ import annotations

import importlib.util
import itertools
import logging
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType

from werkzeug.exceptions import HTTPException, MethodNotAllowed, NotFound
from werkzeug.routing import Map, Rule

from .context import Plugin, PluginError, ToolSpec
from .manifest import Manifest, ManifestError, read
from .runtime import Faults, Worker, make_logger, tail
from .state import StateFile

log = logging.getLogger(__name__)
_LOADS = itertools.count(1)  # each load gets its own module name, also with two hosts in one process


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


class PluginHost:
    def __init__(
        self,
        config,
        *,
        library=None,
        before_backup: Callable[[str], object] | None = None,
        changed: Callable[[], object] | None = None,
    ):
        self.config = config
        self.safe_mode = bool(getattr(config, "safe_mode", False))
        self.library = library  # what plugin.library reads through (LibraryReader)
        self.before_backup = before_backup  # a backup before a plugin migrates its stored data
        self.changed = changed  # tells open pages that the plugins changed (change feed)
        self.role: str | None = None
        self._entries: dict[str, Entry] = {}
        self._active: dict[str, Active] = {}
        self._lock = threading.RLock()
        self.state = StateFile(config.plugins_state_path)
        self._state_seen: tuple | None = None

    # ------------------------------------------------------------------ discovery
    def roots(self) -> list[tuple[Path, str]]:
        return [(self.config.bundled_plugins_dir, "bundled"), (self.config.plugins_dir, "user")]

    def scan(self) -> dict[str, Entry]:
        """Look at the plugin folders again (new plugins are listed, never turned on by this)."""
        found: dict[str, Entry] = {}
        for root, source in self.roots():
            if not root.is_dir():
                continue
            for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
                entry = Entry(folder.name, folder, source)
                try:
                    entry.manifest = read(folder)
                except ManifestError as e:
                    entry.problem = str(e)
                if source == "user" and folder.name in found:
                    entry.replaces_bundled = True
                found[folder.name] = entry
        with self._lock:
            # a running plugin keeps the manifest it was started with until it is restarted
            for pid, act in self._active.items():
                if pid in found and found[pid].folder == act.entry.folder:
                    found[pid] = act.entry
            self._entries = found
        return found

    # ------------------------------------------------------------------ settings
    def _state_of(self, pid: str) -> dict:
        return self.state.of(pid)

    def _update_state(self, pid: str, **changes) -> dict:
        cur = self.state.update(pid, **changes)
        self._state_seen = self.state.stamp()
        return cur

    def values(self, pid: str) -> dict:
        """The plugin's settings: what the user chose, else the manifest's defaults."""
        entry = self._entries.get(pid)
        if not entry or not entry.manifest:
            return {}
        stored = self._state_of(pid).get("settings")
        stored = stored if isinstance(stored, dict) else {}
        out = {}
        for s in entry.manifest.settings:
            try:
                out[s.key] = s.clean(stored[s.key]) if s.key in stored else s.default
            except ManifestError:
                out[s.key] = s.default  # the manifest changed and the stored value no longer fits
        return out

    # ------------------------------------------------------------------ lifecycle
    def start(self, role: str = "app", *, announce: bool = True) -> None:
        """Start every plugin that is on (none in safe mode). ``announce``: send ``app.started``
        (not when they start again after a backup was restored)."""
        self.role = role
        if role == "app":
            try:
                self.config.plugins_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        self.scan()
        self._state_seen = self.state.stamp()
        if self.safe_mode:
            log.warning("Safe mode: plugins are off")
            return
        self._sync()
        if role == "app" and announce:
            self.emit("app.started")

    def stop(self) -> None:
        for pid in list(self._active):
            self._deactivate(pid)

    def refresh(self) -> None:
        """Follow plugins.json when another process changed it (the MCP server picks up plugins
        turned on or off in the app; the app sees one the MCP server had to turn off)."""
        if self.role is None or self.state.stamp() == self._state_seen:
            return
        with self._lock:
            self._state_seen = self.state.stamp()
            self.scan()
            if not self.safe_mode:
                self._sync()

    def _sync(self) -> None:
        on = {pid for pid, st in self.state.read().items() if st.get("enabled")}
        for pid in list(self._active):
            if pid not in on:
                self._deactivate(pid)
        for pid in sorted(on - set(self._active)):
            entry = self._entries.get(pid)
            if entry and entry.manifest and not entry.manifest.compatible():
                self._activate(entry)

    def _activate(self, entry: Entry) -> bool:
        man = entry.manifest
        assert man is not None
        pid = man.id
        data_dir = self.config.plugin_data_dir / pid
        plog, handler = make_logger(pid, data_dir)
        backup = self.before_backup

        def before_upgrade(name: str) -> None:
            if backup:
                backup(f"updating {man.name}'s {name}")

        plugin = Plugin(
            man,
            role=self.role or "app",
            data_dir=data_dir,
            log=plog,
            settings=lambda: self.values(pid),
            library=self.library,
            before_upgrade=before_upgrade,
        )
        act = Active(entry, plugin, Worker(pid, plog, lambda what, e: self.fault(pid, what, e)), handler=handler)
        try:
            path = man.backend_path()
            if path:
                act.module, act.modname = self._load(pid, path)
                start = getattr(act.module, "activate", None)
                if not callable(start):
                    raise PluginError("its backend has no activate(plugin) function")
                start(plugin)
            act.urls = Map([Rule(r.rule, methods=list(r.methods), endpoint=i) for i, r in enumerate(plugin.routes)])
        except BaseException as e:
            plog.error("Could not start:\n%s", traceback.format_exc())
            self._teardown(act, run_unload=True)
            self._failed(pid, f"Could not start: {_describe(e)}")
            return False
        with self._lock:
            self._active[pid] = act
        plog.info("Started (%s, version %s)", self.role, man.version)
        return True

    def _load(self, pid: str, path: Path) -> tuple[ModuleType, str]:
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
            _forget_modules(modname)
            raise
        return module, modname

    def _deactivate(self, pid: str) -> None:
        with self._lock:
            act = self._active.pop(pid, None)
        if act:
            self._teardown(act, run_unload=True)
            act.plugin.log.info("Stopped")

    def _teardown(self, act: Active, *, run_unload: bool) -> None:
        act.worker.stop()
        if run_unload:
            stop = getattr(act.module, "deactivate", None) if act.module else None
            for fn in ([lambda: stop(act.plugin)] if callable(stop) else []) + list(reversed(act.plugin.unload)):
                try:
                    fn()
                except Exception:
                    act.plugin.log.error("Stopping failed:\n%s", traceback.format_exc())
        act.plugin.routes.clear()
        act.plugin.tools.clear()
        act.plugin.listeners.clear()
        if act.modname:
            _forget_modules(act.modname)
        if act.handler:
            act.plugin.log.removeHandler(act.handler)
            act.handler.close()

    def _failed(self, pid: str, reason: str) -> None:
        self._update_state(pid, enabled=False, failed=reason, failed_at=_now())
        log.warning("Plugin %s turned off: %s", pid, reason)
        if self.changed:
            self.changed()

    def fault(self, pid: str, what: str, exc: BaseException) -> None:
        """A plugin's code failed. Logged; too many failures in a short time turn it off."""
        act = self._active.get(pid)
        if not act:
            return
        act.plugin.log.error("%s failed:\n%s", what, "".join(traceback.format_exception(exc)).rstrip())
        if act.faults.add():

            def turn_off() -> None:
                self._deactivate(pid)
                self._failed(pid, f"Turned off after failing repeatedly; last: {what}: {_describe(exc)}")

            # not on the plugin's own worker thread, which turning it off waits for
            threading.Thread(target=turn_off, name=f"plugin-off-{pid}", daemon=True).start()

    # ------------------------------------------------------------------ what the user does
    def _entry(self, pid: str) -> Entry:
        entry = self.scan().get(pid)
        if not entry:
            raise PluginError(f"No plugin {pid!r}")
        return entry

    def enable(self, pid: str) -> dict:
        entry = self._entry(pid)
        if entry.problem or not entry.manifest:
            raise PluginError(f"{pid} cannot be turned on: {entry.problem}")
        why = entry.manifest.compatible()
        if why:
            raise PluginError(f"{entry.manifest.name} cannot be turned on: {why}")
        with self._lock:
            self._update_state(pid, enabled=True, failed=None, failed_at=None)
            if not self.safe_mode and self.role and pid not in self._active:
                self._activate(entry)
        if self.changed:
            self.changed()
        return self.info(pid)

    def disable(self, pid: str) -> dict:
        self._entry(pid)
        with self._lock:
            self._update_state(pid, enabled=False)
            self._deactivate(pid)
        if self.changed:
            self.changed()
        return self.info(pid)

    def restart(self, pid: str) -> dict:
        """Stop the plugin and start it again with its current code and manifest."""
        with self._lock:
            self._deactivate(pid)
            entry = self._entry(pid)
            if (
                self._state_of(pid).get("enabled")
                and entry.manifest
                and not entry.problem
                and not entry.manifest.compatible()
                and not self.safe_mode
                and self.role
            ):
                self._activate(entry)
        if self.changed:
            self.changed()
        return self.info(pid)

    def set_settings(self, pid: str, changes: dict) -> dict:
        entry = self._entry(pid)
        if not entry.manifest:
            raise PluginError(f"{pid} cannot be set up: {entry.problem}")
        if not isinstance(changes, dict):
            raise PluginError("Settings must be an object")
        clean = {}
        for k, v in changes.items():
            s = entry.manifest.setting(k)
            if not s:
                raise PluginError(f"{entry.manifest.name} has no setting {k!r}")
            try:
                clean[k] = s.clean(v)
            except ManifestError as e:
                raise PluginError(str(e)) from e
        before = self.values(pid)
        stored = self._state_of(pid).get("settings")
        stored = dict(stored) if isinstance(stored, dict) else {}
        stored.update(clean)
        self._update_state(pid, settings=stored)
        after = self.values(pid)
        diff = {k: v for k, v in after.items() if before.get(k) != v}
        act = self._active.get(pid)
        if act and diff:
            for fn in list(act.plugin.settings._listeners):
                act.worker.submit("settings listener", fn, dict(diff))
        if self.changed:
            self.changed()
        return after

    def set_remote_mcp(self, pid: str, on: bool) -> dict:
        """Whether the plugin's tools are also offered to AI apps on other computers (/mcp)."""
        entry = self._entry(pid)
        if not entry.manifest or not entry.manifest.mcp:
            raise PluginError(f"{pid} offers no tools to Claude")
        self._update_state(pid, remote_mcp=bool(on))
        if self.changed:
            self.changed()
        return self.info(pid)

    def log_lines(self, pid: str, lines: int = 500) -> list[str]:
        self._entry(pid)
        return tail(self.config.plugin_data_dir / pid, lines)

    # ------------------------------------------------------------------ what the app shows
    def info(self, pid: str) -> dict:
        entry = self._entries.get(pid) or self._entry(pid)
        st = self._state_of(pid)
        man = entry.manifest
        act = self._active.get(pid)
        enabled = bool(st.get("enabled"))
        if entry.problem or not man:
            status, problem = "invalid", entry.problem
        elif man.compatible():
            status, problem = "incompatible", man.compatible()
        elif act:
            status, problem = "on", None
        elif st.get("failed"):
            status, problem = "failed", st["failed"]
        elif enabled and self.safe_mode:
            status, problem = "safe-mode", "Synapse was started in safe mode"
        else:
            status, problem = ("on" if enabled else "off"), None
        out: dict = {
            "id": pid,
            "name": man.name if man else pid,
            "version": man.version if man else "",
            "description": man.description if man else "",
            "author": man.author if man else "",
            "source": entry.source,
            "folder": str(entry.folder),
            "replaces_bundled": entry.replaces_bundled,
            "enabled": enabled,
            "running": bool(act),
            "status": status,
            "problem": problem,
            "failed_at": st.get("failed_at") if status == "failed" else None,
            "remote_mcp": bool(st.get("remote_mcp")),
        }
        if man:
            out.update(
                api=f"{man.api[0]}.{man.api[1]}",
                permissions=man.permissions(),
                parts=[p for p in ("backend", "frontend", "styles") if getattr(man, p)],
                settings=[s.describe() for s in man.settings],
                values=self.values(pid),
            )
        if act:
            out["tools"] = [t.name for t in act.plugin.tools]
            out["routes"] = [{"rule": r.rule, "methods": list(r.methods)} for r in act.plugin.routes]
            out["events"] = sorted(e for e, fns in act.plugin.listeners.items() if fns)
        return out

    def listing(self) -> list[dict]:
        self.refresh()
        entries = self.scan()
        return sorted((self.info(pid) for pid in entries), key=lambda p: p["name"].lower())

    def overview(self) -> dict:
        from .manifest import API_VERSION

        return {
            "plugins": self.listing(),
            "safe_mode": self.safe_mode,
            "dir": str(self.config.plugins_dir),
            "api": f"{API_VERSION[0]}.{API_VERSION[1]}",
        }

    # ------------------------------------------------------------------ what plugins hear and serve
    def emit(self, event: str, **payload) -> None:
        """Tell the running plugins that ``event`` happened. Returns at once: listeners run on each
        plugin's worker thread."""
        with self._lock:
            targets = [(a, list(a.plugin.listeners.get(event, ()))) for a in self._active.values()]
        for act, fns in targets:
            for fn in fns:
                act.worker.submit(f"{event} listener", fn, dict(payload, event=event))

    def drain(self, pid: str | None = None, timeout: float = 5.0) -> bool:
        """Wait until the listeners called so far have run (for tests)."""
        acts = [self._active[pid]] if pid else list(self._active.values())
        return all(a.worker.drain(timeout) for a in acts)

    def route(self, pid: str, path: str, method: str) -> tuple[Callable, Mapping]:
        """The function serving ``path`` of plugin ``pid``, and its arguments from the path.
        Raises werkzeug's NotFound / MethodNotAllowed."""
        act = self._active.get(pid)
        if not act or not act.urls:
            raise NotFound(f"No plugin {pid!r} is running")
        adapter = act.urls.bind("localhost")
        endpoint, args = adapter.match(path, method=method)
        return act.plugin.routes[int(endpoint)].fn, args

    def run(self, pid: str, what: str, fn: Callable, *args, **kwargs):
        """Run plugin code for a request or a tool: ``PluginError`` and HTTP errors pass through as
        they are, anything else counts as a failure of the plugin."""
        try:
            return fn(*args, **kwargs)
        except (PluginError, HTTPException):
            raise
        except Exception as e:
            self.fault(pid, what, e)
            raise

    def mcp_tools(self, *, remote: bool = False) -> list[ToolSpec]:
        """The tools running plugins offer to Claude. ``remote``: for /mcp (another computer), where
        only plugins the user allowed for it take part."""
        self.refresh()
        out = []
        with self._lock:
            acts = list(self._active.items())
        for pid, act in acts:
            if remote and not self._state_of(pid).get("remote_mcp"):
                continue
            for t in act.plugin.tools:
                out.append(ToolSpec(t.name, t.title, t.description, t.schema, _guarded(self, pid, t), t.read_only))
        return out


def _guarded(host: PluginHost, pid: str, tool: ToolSpec) -> Callable:
    def call(**kwargs):
        return host.run(pid, f"tool {tool.name}", tool.fn, **kwargs)

    return call


def _forget_modules(modname: str) -> None:
    for name in [n for n in sys.modules if n == modname or n.startswith(modname + ".")]:
        del sys.modules[name]


def _describe(e: BaseException) -> str:
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


__all__ = ["PluginHost", "Entry", "MethodNotAllowed", "NotFound"]
