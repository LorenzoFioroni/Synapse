"""The plugin host: finds plugins, keeps which are on (``data/plugins.json``), starts and stops their
code, and hands them what happens in the library.

Plugins are folders with a ``manifest.json`` in ``data/plugins/`` (installed by the user) and in
the repository's ``plugins/`` (core plugins start with their declared defaults); one in ``data/plugins``
replaces a bundled one with the same id. Both the web app and the MCP server run a host: the
web app with ``role="app"``, the MCP server Claude starts with ``role="mcp"``. Only the web app
changes which plugins are on; the MCP server follows ``plugins.json`` (``refresh``).

A plugin that fails while starting, or fails ``FAULTS`` times within ``FAULT_WINDOW_S`` (an event
listener, a route or a tool raising anything but ``PluginError``), is turned off and its reason
shown in Settings → Plugins. ``--safe-mode`` runs only bundled core plugins.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from werkzeug.exceptions import HTTPException, MethodNotAllowed, NotFound
from werkzeug.routing import Map, Rule

from . import build, deps
from .catalog import discover, initialize_core
from .context import Plugin, PluginError, ToolSpec
from .http import PluginHttp, service_credentials
from .itemfields import FieldIndex, PluginFields
from .jobs import Runner
from .loading import Active, Entry, asset_path, forget_modules, load_module, newest
from .manifest import ManifestError
from .report import describe
from .runtime import Worker, make_logger, tail
from .services import PluginActions, PluginServices
from .sources import SourceList
from .state import StateFile

log = logging.getLogger(__name__)


class PluginHost:
    def __init__(
        self,
        config,
        *,
        library=None,
        before_backup: Callable[[str], object] | None = None,
        changed: Callable[[], object] | None = None,
        settings=None,
        fields_changed: Callable[[], object] | None = None,
        services=None,
        sources_changed: Callable[[], object] | None = None,
    ):
        self.config = config
        self.safe_mode = bool(getattr(config, "safe_mode", False))
        self.library = library  # what plugin.library reads through (LibraryReader)
        self.before_backup = before_backup  # a backup before a plugin migrates its stored data
        self.changed = changed  # tells open pages that the plugins changed (change feed)
        self.role: str | None = None
        self.settings = settings  # the app's SettingsStore (the contact address in the User-Agent)
        self.services = services  # the app's Services: what plugin.services and plugin.actions use
        self.private_network = False  # tests: let plugin.http reach a server on this computer
        self.installer = deps.pip_install  # tests replace it
        self._watcher: threading.Thread | None = None
        self._watch_stop = threading.Event()
        # lists of outside papers the running plugins offer (sources.py)
        self.sources = SourceList(self)
        self.sources_changed = sources_changed  # tells open pages to show a source's list again
        # per-item fields of the running plugins (itemfields.py), stored in data/fields.db
        self.fields = FieldIndex(
            config.fields_db_path,
            lambda: [d for a in list(self._active.values()) for d in a.plugin.fields.defs.values()],
            fields_changed,
        )
        self._entries: dict[str, Entry] = {}
        self._active: dict[str, Active] = {}
        self._lock = threading.RLock()
        self.state = StateFile(config.plugins_state_path)
        self._state_seen: tuple | None = None

    # ------------------------------------------------------------------ discovery
    def scan(self) -> dict[str, Entry]:
        """Discover folders, retaining running manifests until their plugin restarts."""
        found = discover(self.config)
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
        """Start every enabled plugin (only bundled core plugins in safe mode). ``announce``: send ``app.started``
        (not when they start again after a backup was restored)."""
        self.role = role
        self._watch_stop.clear()
        if role == "app":
            try:
                self.config.plugins_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        self.scan()
        self._turn_on_core()
        self._state_seen = self.state.stamp()
        if self.safe_mode:
            log.warning("Safe mode: plugins are off (except the core plugins, which are part of Synapse)")
        self._sync()
        if role == "app" and announce:
            self.emit("app.started")
        if role == "app" and getattr(self.config, "dev_plugins_dir", None) and not self._watcher:
            self._watch_stop.clear()
            self._watcher = threading.Thread(target=self._watch, name="plugins-dev", daemon=True)
            self._watcher.start()

    def check_changes(self) -> list[str]:
        """Restart the running dev plugins (--dev-plugins) whose files changed; returns their ids."""
        restarted = []
        for pid, act in list(self._active.items()):
            if act.entry.source != "dev":
                continue
            if newest(act.entry.folder) > act.stamp:
                act.plugin.log.info("Files changed: restarting")
                self.restart(pid)
                restarted.append(pid)
        return restarted

    def _watch(self) -> None:
        while not self._watch_stop.wait(1.0):
            try:
                with self._lock:
                    if not self._watch_stop.is_set():
                        self.check_changes()
            except Exception:
                log.exception("Watching the dev plugins failed")

    def stop(self) -> None:
        self._watch_stop.set()
        watcher = self._watcher
        if watcher and watcher is not threading.current_thread():
            watcher.join()
        self._watcher = None
        with self._lock:
            for pid in list(self._active):
                self._deactivate(pid)

    def refresh(self) -> None:
        """Follow plugins.json when another process changed it (the MCP server picks up plugins
        turned on or off in the app; the app sees one the MCP server had to turn off)."""
        if self.role is None or self._watch_stop.is_set() or self.state.stamp() == self._state_seen:
            return
        with self._lock:
            self._state_seen = self.state.stamp()
            self.scan()
            self._sync()

    def is_core(self, pid: str) -> bool:
        """A core plugin: part of Synapse (its plugins/ folder) and marked so in its manifest."""
        entry = self._entries.get(pid)
        return bool(entry and entry.source == "bundled" and entry.manifest and entry.manifest.core)

    def _turn_on_core(self) -> None:
        initialize_core(self._entries, self.state, self.settings)

    def _sync(self) -> None:
        on = {pid for pid, st in self.state.read().items() if st.get("enabled")}
        if self.safe_mode:
            on = {pid for pid in on if self.is_core(pid)}
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
            http=PluginHttp(
                pid,
                man.network,
                lambda: self._user_agent(pid),
                private=self.private_network,
                credentials=lambda host: service_credentials(host, self.settings),
            ),
            services=PluginServices(self.services) if self.services else None,
            actions=PluginActions(self.services, man) if self.services else None,
            fields=PluginFields(
                pid,
                man.name,
                lambda: self.fields.store,
                self.library.get if self.library else None,
                self.fields.changed,
            ),
            sources_changed=lambda: self.sources_changed() if self.sources_changed else None,
            tool_prefix=not self.is_core(pid),
            synapse=self.services if self.is_core(pid) else None,
        )
        act = Active(entry, plugin, Worker(pid, plog, lambda what, e: self.fault(pid, what, e)), handler=handler)
        act.started = datetime.now().isoformat(timespec="microseconds")
        try:
            act.stamp = newest(man.folder)
            target = deps.ensure(man.python, data_dir, self.installer)
            if target:
                act.pypath = str(target)
                sys.path.insert(0, act.pypath)
            if build.needs_build(man.frontend):
                source = (man.folder / (man.frontend or "")).resolve()
                act.bundle = build.bundle(source, data_dir / build.BUILD_DIR, self.config.frontend_dir)
            path = man.backend_path()
            if path:
                act.module, act.modname = load_module(pid, path)
                start = getattr(act.module, "activate", None)
                if not callable(start):
                    raise PluginError("its backend has no activate(plugin) function")
                start(plugin)
            act.urls = Map([Rule(r.rule, methods=list(r.methods), endpoint=i) for i, r in enumerate(plugin.routes)])
            refreshed = [d for d in plugin.fields.defs.values() if d.refresher]
            if self.role == "app" and (plugin.schedules or plugin.handlers or refreshed):
                act.runner = Runner(
                    pid, data_dir, plog, plugin.handlers, plugin.schedules, lambda what, e: self.fault(pid, what, e)
                )
                if refreshed:
                    act.runner.refresher = lambda: self._refresh_fields(refreshed, plog)
                plugin.wake = act.runner.wake
                plugin.fields.allow_retry = lambda name, keys: self.fields.allow_retry(pid, name, keys)
                plugin.fields.refresh_soon = act.runner.refresh_soon
                act.runner.start()
        except BaseException as e:
            plog.error("Could not start:\n%s", traceback.format_exc())
            self._teardown(act, run_unload=True)
            self._failed(pid, f"Could not start: {_describe(e)}")
            return False
        with self._lock:
            self._active[pid] = act
        plog.info("Started (%s, version %s)", self.role, man.version)
        return True

    def asset(self, pid: str, rel: str) -> Path:
        """A file of a running plugin's frontend (``loading.asset_path``); NotFound otherwise."""
        act = self._active.get(pid)
        if not act or not act.entry.manifest:
            raise NotFound()
        found = asset_path(act.entry.manifest, act.bundle, rel)
        if not found:
            raise NotFound()
        return found

    def _deactivate(self, pid: str) -> None:
        with self._lock:
            act = self._active.pop(pid, None)
        if act:
            self._teardown(act, run_unload=True)
            act.plugin.log.info("Stopped")

    def _teardown(self, act: Active, *, run_unload: bool) -> None:
        act.worker.stop()
        if act.runner:
            act.runner.stop()
            act.plugin.wake = lambda: None
            act.plugin.fields.refresh_soon = lambda: None
            act.plugin.fields.allow_retry = lambda name, keys: None
        if run_unload:
            stop = getattr(act.module, "deactivate", None) if act.module else None
            for fn in ([lambda: stop(act.plugin)] if callable(stop) else []) + list(reversed(act.plugin.unload)):
                try:
                    fn()
                except Exception:
                    act.plugin.log.error("Stopping failed:\n%s", traceback.format_exc())
        act.plugin.routes.clear()
        act.plugin.tools.clear()
        act.plugin.metadata_sources.clear()
        act.plugin.reference_sources.clear()
        act.plugin.export_formats.clear()
        act.plugin.listeners.clear()
        if act.modname:
            forget_modules(act.modname)
        if act.pypath and act.pypath in sys.path:
            sys.path.remove(act.pypath)
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
            if (not self.safe_mode or self.is_core(pid)) and self.role and pid not in self._active:
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
                and (not self.safe_mode or self.is_core(pid))
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
        """What Settings → Plugins shows about one plugin (report.describe)."""
        entry = self._entries.get(pid) or self._entry(pid)
        return describe(
            entry, self._state_of(pid), self._active.get(pid), self.values(pid), self.safe_mode, self.is_core(pid)
        )

    def listing(self) -> list[dict]:
        self.refresh()
        entries = self.scan()
        return sorted((self.info(pid) for pid in entries), key=lambda p: p["name"].lower())

    def _user_agent(self, pid: str) -> str:
        email = (self.settings.get("contact_email") if self.settings else "") or "synapse@example.com"
        return f"Synapse/2.0 (plugin {pid}; https://github.com/LorenzoFioroni/Synapse; mailto:{email})"

    def overview(self) -> dict:
        from .manifest import API_VERSION

        return {
            "plugins": self.listing(),
            "safe_mode": self.safe_mode,
            "dir": str(self.config.plugins_dir),
            "api": f"{API_VERSION[0]}.{API_VERSION[1]}",
        }

    # ------------------------------------------------------------------ what plugins hear and serve
    def _refresh_fields(self, defs: list, plog) -> int:
        """One batch of each field's due values (on the plugin's runner thread)."""
        if not self.library:
            return 0
        items = self.library.items()
        return sum(self.fields.refresh(d, items, plog) for d in defs)

    def emit(self, event: str, **payload) -> None:
        """Tell the running plugins that ``event`` happened. Returns at once: listeners run on each
        plugin's worker thread."""
        with self._lock:
            targets = [(a, list(a.plugin.listeners.get(event, ()))) for a in self._active.values()]
        if event in ("item.added", "item.changed"):
            for act, _ in targets:
                if act.runner:
                    act.runner.refresh_soon()  # a new DOI may have new values to fetch
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

    def pdf_candidates(self, paper: dict, *, by_title: bool) -> list[str]:
        """Candidate PDF URLs for ``paper`` ({title, doi, arxiv}) from the running plugins' PDF
        sources (those searching by title, or those going by identifier), best first, each URL
        once. A source that fails is left out (and counts against its plugin unless it is a
        PluginError, such as no network)."""
        sources = []
        with self._lock:
            acts = list(self._active.items())
        for pid, act in acts:
            sources += [(s.order, pid, s) for s in act.plugin.pdf_sources.values() if s.by_title == by_title]
        out: list[str] = []
        for _, pid, s in sorted(sources, key=lambda x: (x[0], x[1], x[2].id)):
            try:
                got = self.run(pid, f"PDF source {s.id}", s.fn, dict(paper)) or []
            except Exception as e:
                log.info("PDF source %s.%s failed: %s", pid, s.id, e)
                continue
            for u in got if isinstance(got, list | tuple) else []:
                if isinstance(u, str) and u.startswith(("https://", "http://")) and u not in out:
                    out.append(u)
        return out

    def has_pdf_sources(self) -> bool:
        with self._lock:
            return any(act.plugin.pdf_sources for act in self._active.values())

    def _export_formats(self) -> dict:
        with self._lock:
            return {
                (f.id if self.is_core(pid) else f"{pid}.{f.id}"): (pid, f)
                for pid, act in self._active.items()
                for f in act.plugin.export_formats.values()
            }

    def export_formats(self) -> list[dict]:
        return [
            {"key": key, "label": f.title, "extension": f.extension} for key, (_, f) in self._export_formats().items()
        ]

    def export_items(self, key: str, items: list[dict], options: dict) -> tuple:
        entry = self._export_formats().get(key)
        if entry is None:
            raise PluginError(
                "This export format is not available: turn on Export Formats or its provider in Settings → Plugins."
            )
        pid, fmt = entry
        text = self.run(pid, f"Export {key}", fmt.fn, items, options)
        if not isinstance(text, str):
            raise PluginError("An export writer must return text")
        return fmt, text

    def fetch_references(self, query: dict) -> tuple[str, list[dict]]:
        from .references import resolve

        with self._lock:
            sources = [
                (pid, self.is_core(pid), source)
                for pid, act in self._active.items()
                for source in act.plugin.reference_sources.values()
            ]
        return resolve(sources, self.run, query)

    def lookup_metadata(self, query: dict) -> dict:
        """Ask the metadata sources of running plugins for the normalized query."""
        from .resolvers import lookup

        with self._lock:
            sources = [
                (pid, source) for pid, act in self._active.items() for source in act.plugin.metadata_sources.values()
            ]
        return lookup(sources, self.run, query)

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


def _describe(e: BaseException) -> str:
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


__all__ = ["PluginHost", "Entry", "MethodNotAllowed", "NotFound"]
