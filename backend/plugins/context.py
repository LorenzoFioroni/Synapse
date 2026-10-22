"""``Plugin``: the object a plugin's ``activate(plugin)`` receives, and everything it registers
through it. The host keeps what is registered here and drops all of it when the plugin is turned
off, so a plugin needs no cleanup of its own for routes, tools or event listeners."""

from __future__ import annotations

import inspect
import logging
import re
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import PluginError
from .jobs import Handler, Jobs
from .manifest import Manifest
from .schedule import Schedule, make
from .storage import Storage

EVENTS = (
    "item.added",  # ids: the new items
    "item.changed",  # ids, fields: the fields that were set
    "item.trashed",  # ids
    "item.restored",  # ids
    "item.deleted",  # ids: deleted for good
    "citekey.changed",  # id, old, new
    "note.saved",  # path, hash, by ("app" or "ai")
    "app.started",  # the web app is up (not sent in the MCP server)
)
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


@dataclass
class Route:
    rule: str
    methods: tuple[str, ...]
    fn: Callable


@dataclass
class ToolSpec:
    """An MCP tool offered by a plugin. ``name`` is already prefixed with the plugin id."""

    name: str
    title: str
    description: str
    schema: dict
    fn: Callable
    read_only: bool = True


class Settings:
    """``plugin.settings``: the values of the settings the manifest declares (read-only here; the
    user changes them in Settings → Plugins)."""

    def __init__(self, values: Callable[[], dict]):
        self._values = values
        self._listeners: list[Callable[[dict], None]] = []

    def __getitem__(self, key: str):
        values = self._values()
        if key not in values:
            raise KeyError(f"No setting {key!r} in the manifest")
        return values[key]

    def get(self, key: str, default=None):
        return self._values().get(key, default)

    def all(self) -> dict:
        return dict(self._values())

    def on_change(self, fn: Callable[[dict], None]) -> Callable[[dict], None]:
        """Call ``fn(changed)`` after the user changes settings (``changed``: the new values of
        the keys that changed). Usable as a decorator."""
        self._listeners.append(fn)
        return fn


class Plugin:
    def __init__(
        self,
        manifest: Manifest,
        *,
        role: str,
        data_dir: Path,
        log: logging.Logger,
        settings: Callable[[], dict],
        library=None,
        before_upgrade: Callable[[str], None] | None = None,
        http=None,
        fields=None,
    ):
        self.manifest = manifest
        self.id = manifest.id
        self.role = role  # "app" (the web app) or "mcp" (the MCP server Claude starts)
        self.log = log
        self.storage = Storage(data_dir, before_upgrade)
        self.settings = Settings(settings)
        self._library = library
        self.routes: list[Route] = []
        self.tools: list[ToolSpec] = []
        self.listeners: dict[str, list[Callable]] = {}
        self.unload: list[Callable[[], None]] = []
        self.http = http  # PluginHttp (http.py): the web, within the manifest's "network" permission
        self.fields = fields  # PluginFields (itemfields.py): values attached to papers
        self.handlers: dict[str, Handler] = {}
        self.schedules: list[Schedule] = []
        self.wake: Callable[[], None] = lambda: None  # set by the host: wakes the background runner
        self.jobs = Jobs(data_dir, self.handlers, lambda: self.wake())

    @property
    def library(self):
        """Read access to the library (needs ``"library": ["read"]`` in the manifest)."""
        if "read" not in self.manifest.library or self._library is None:
            raise PluginError(f'{self.manifest.name} needs "library": ["read"] in its manifest to read the library')
        return self._library

    # ------------------------------------------------------------------ routes
    def route(self, rule: str, methods: tuple[str, ...] | list[str] = ("GET",)):
        """Serve ``fn`` at ``/api/plugins/<id><rule>`` (Flask/Werkzeug rule syntax, e.g.
        ``/items/<item_id>``). It returns a dict or list (sent as JSON), text, or a Flask response."""
        if not isinstance(rule, str) or not rule.startswith("/") or ".." in rule:
            raise PluginError(f"Route {rule!r} must start with /")
        ms = tuple(m.upper() for m in methods)
        bad = [m for m in ms if m not in METHODS]
        if bad or not ms:
            raise PluginError(f"Route {rule}: methods are {', '.join(METHODS)}")

        def register(fn: Callable) -> Callable:
            self.routes.append(Route(rule, ms, fn))
            return fn

        return register

    # ------------------------------------------------------------------ MCP
    def mcp_tool(
        self,
        description: str,
        *,
        name: str | None = None,
        title: str | None = None,
        schema: dict | None = None,
        read_only: bool = True,
    ):
        """Offer ``fn`` to Claude as a tool (needs ``"mcp": true`` in the manifest). Its arguments
        come from the function's signature unless ``schema`` (a JSON Schema object) is given. Raise
        ``PluginError`` for a problem Claude should read and can fix."""
        if not self.manifest.mcp:
            raise PluginError(f'{self.manifest.name} needs "mcp": true in its manifest to offer tools to Claude')

        def register(fn: Callable) -> Callable:
            tool = name or fn.__name__
            if not TOOL_NAME_RE.match(tool):
                raise PluginError(f"Tool name {tool!r}: lowercase letters, digits and _")
            full = f"{self.id}__{tool}"
            if any(t.name == full for t in self.tools):
                raise PluginError(f"Tool {tool} is registered twice")
            self.tools.append(
                ToolSpec(
                    full,
                    title or tool.replace("_", " ").capitalize(),
                    f"[{self.manifest.name}] {description}",
                    schema if schema is not None else schema_of(fn),
                    fn,
                    read_only,
                )
            )
            return fn

        return register

    # ------------------------------------------------------------------ events
    def on(self, event: str, fn: Callable | None = None):
        """Call ``fn(payload)`` after ``event`` (see ``EVENTS``), on the plugin's own worker thread.
        Usable as a decorator; called directly it returns a function that removes the listener."""
        if event not in EVENTS:
            raise PluginError(f"Unknown event {event!r}; events are {', '.join(EVENTS)}")

        def register(f: Callable) -> Callable:
            self.listeners.setdefault(event, []).append(f)
            return f

        if fn is None:
            return register
        register(fn)

        def remove() -> None:
            lst = self.listeners.get(event, [])
            if fn in lst:
                lst.remove(fn)

        return remove

    # ------------------------------------------------------------------ background work
    def schedule(
        self,
        name: str,
        run: Callable[[], object] | None = None,
        *,
        cron: str | None = None,
        every: float | str | None = None,
        tz: str | None = None,
        catch_up: bool = True,
    ):
        """Run ``run()`` on a cron rule (``cron="30 2 * * 1-5"``, in ``tz`` such as "Europe/Rome",
        else this computer's zone) or every so often (``every="6h"``), in the web app only. With
        ``catch_up``, a run missed while the computer slept or the app was closed happens once at the
        next start (and the very first time, at once). Usable as a decorator."""
        if any(s.name == name for s in self.schedules):
            raise PluginError(f"Schedule {name!r} is registered twice")

        def register(fn: Callable[[], object]) -> Callable[[], object]:
            self.schedules.append(make(name, fn, cron=cron, every=every, tz=tz, catch_up=catch_up))
            return fn

        if run is None:
            return register
        return register(run)

    def job(self, name: str, *, retries: int = 3):
        """Register ``fn`` as the handler of jobs called ``name``; queue them with
        ``plugin.jobs.enqueue(name, *args, key=…)``."""
        if not TOOL_NAME_RE.match(name):
            raise PluginError(f"Job name {name!r}: lowercase letters, digits and _")

        def register(fn: Callable) -> Callable:
            self.handlers[name] = Handler(fn, max(0, int(retries)))
            return fn

        return register

    def on_unload(self, fn: Callable[[], None]) -> Callable[[], None]:
        """Call ``fn()`` when the plugin is turned off (to stop a thread it started, say)."""
        self.unload.append(fn)
        return fn


_JSON_TYPES: dict[object, str] = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object"}


def _json_type(t) -> dict:
    origin = typing.get_origin(t)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(t) if a is not type(None)]
        return _json_type(args[0]) if len(args) == 1 else {}
    if t in _JSON_TYPES:
        return {"type": _JSON_TYPES[t]}
    if t is list or origin is list:
        inner = typing.get_args(t)
        items = _json_type(inner[0]) if inner else {}
        return {"type": "array", **({"items": items} if items else {})}
    if origin is dict:
        return {"type": "object"}
    if origin is typing.Literal:
        return {"type": "string", "enum": [str(a) for a in typing.get_args(t)]}
    return {}


def schema_of(fn: Callable) -> dict:
    """A JSON Schema for ``fn``'s keyword arguments, from its type hints: parameters without a
    default are required."""
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    props: dict[str, dict] = {}
    required = []
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        props[p.name] = _json_type(hints.get(p.name, str))
        if p.default is p.empty:
            required.append(p.name)
    out: dict = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out
