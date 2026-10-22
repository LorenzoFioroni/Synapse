"""A plugin's ``manifest.json``: what the plugin is, which API it targets, what it needs and which
settings it has. Read and checked before any of the plugin's code runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

API_VERSION = (1, 0)  # the plugin API this Synapse offers (major, minor)

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
HOST_RE = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
API_RE = re.compile(r"^\^?(\d+)(?:\.(\d+))?$")

LIBRARY_PERMISSIONS = ("read", "add", "edit")
SETTING_TYPES = ("bool", "int", "float", "text", "choice", "multi")


class ManifestError(ValueError):
    """The manifest cannot be used; the message says what to fix."""


@dataclass
class Setting:
    key: str
    type: str
    label: str
    default: object
    description: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)  # (value, label), for choice and multi
    min: float | None = None
    max: float | None = None
    # a core plugin's setting that used to be one of Synapse's own (settings.json): its value
    # carries over when the plugin first starts
    from_setting: str | None = None

    def describe(self) -> dict:
        out: dict = {"key": self.key, "type": self.type, "label": self.label, "default": self.default}
        if self.description:
            out["description"] = self.description
        if self.options:
            out["options"] = [[v, label] for v, label in self.options]
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        return out

    def clean(self, value):
        """``value`` as this setting stores it; raises ``ManifestError`` (shown to the user) if it
        does not fit."""
        t = self.type
        if t == "bool":
            if isinstance(value, bool):
                return value
            raise ManifestError(f"{self.label}: on or off")
        if t in ("int", "float"):
            if isinstance(value, bool):
                raise ManifestError(f"{self.label} must be a number")
            try:
                v = int(value) if t == "int" else float(value)
            except (TypeError, ValueError) as e:
                raise ManifestError(f"{self.label} must be a number") from e
            if t == "int" and isinstance(value, float) and value != int(value):
                raise ManifestError(f"{self.label} must be a whole number")
            if self.min is not None and v < self.min:
                raise ManifestError(f"{self.label}: at least {_num(self.min)}")
            if self.max is not None and v > self.max:
                raise ManifestError(f"{self.label}: at most {_num(self.max)}")
            return v
        if t == "text":
            v = "" if value is None else str(value)
            if len(v) > 10000:
                raise ManifestError(f"{self.label}: at most 10,000 characters")
            return v
        allowed = [v for v, _ in self.options]
        if t == "choice":
            v = "" if value is None else str(value)
            if v not in allowed:
                raise ManifestError(f"{self.label}: one of {', '.join(allowed)}")
            return v
        # multi: a list of strings, from the options when there are any
        if isinstance(value, str):
            value = [p.strip() for p in value.split(",")]
        if not isinstance(value, list):
            raise ManifestError(f"{self.label}: a list")
        out: list[str] = []
        for v in value:
            s = str(v).strip()
            if not s or s in out:
                continue
            if allowed and s not in allowed:
                raise ManifestError(f"{self.label}: {s} is not one of the choices")
            out.append(s)
        if len(out) > 500:
            raise ManifestError(f"{self.label}: at most 500 entries")
        return out


def _num(x: float) -> str:
    return str(int(x)) if x == int(x) else str(x)


@dataclass
class Manifest:
    id: str
    name: str
    version: str
    api: tuple[int, int]
    folder: Path
    description: str = ""
    author: str = ""
    backend: str | None = None  # a package folder or a .py file, relative to the plugin's folder
    frontend: str | None = None
    styles: str | None = None
    network: list[str] = field(default_factory=list)
    library: list[str] = field(default_factory=list)
    mcp: bool = False
    settings: list[Setting] = field(default_factory=list)
    python: list[str] = field(default_factory=list)  # packages it needs (deps.py)
    # A core plugin: part of Synapse, in its plugins/ folder (honoured only there). It starts on
    # (or as the Synapse setting ``enabled_from_setting`` was, for a feature that had a switch),
    # may use Synapse's own modules, keeps its tools' names unprefixed, and its frontend
    # ("core:<name>") is built into the app (frontend/src/plugins/core/).
    core: bool = False
    enabled_by_default: bool = False
    enabled_from_setting: str | None = None

    def compatible(self) -> str | None:
        """Why this Synapse cannot run the plugin, or None if it can."""
        major, minor = self.api
        if major != API_VERSION[0] or minor > API_VERSION[1]:
            have = f"{API_VERSION[0]}.{API_VERSION[1]}"
            return f"It needs plugin API {major}.{minor}; this Synapse offers {have}."
        return None

    def permissions(self) -> dict:
        return {
            "network": list(self.network),
            "library": list(self.library),
            "mcp": self.mcp,
            "python": list(self.python),
        }

    def setting(self, key: str) -> Setting | None:
        return next((s for s in self.settings if s.key == key), None)

    def defaults(self) -> dict:
        return {s.key: s.default for s in self.settings}

    def backend_path(self) -> Path | None:
        return _inside(self.folder, self.backend) if self.backend else None


def _inside(folder: Path, rel: str) -> Path:
    p = (folder / rel).resolve()
    if p != folder.resolve() and folder.resolve() not in p.parents:
        raise ManifestError(f"{rel} is outside the plugin's folder")
    return p


def _text(data: dict, key: str, *, required: bool = False, limit: int = 200) -> str:
    v = data.get(key)
    if v is None or v == "":
        if required:
            raise ManifestError(f'"{key}" is missing')
        return ""
    if not isinstance(v, str):
        raise ManifestError(f'"{key}" must be text')
    v = v.strip()
    if len(v) > limit:
        raise ManifestError(f'"{key}": at most {limit} characters')
    return v


def _options(raw, key: str) -> list[tuple[str, str]]:
    if not isinstance(raw, list) or not raw:
        raise ManifestError(f'setting "{key}": "options" must be a non-empty list')
    out = []
    for o in raw:
        if isinstance(o, str):
            out.append((o, o))
        elif isinstance(o, list) and len(o) == 2 and all(isinstance(x, str) for x in o):
            out.append((o[0], o[1]))
        else:
            raise ManifestError(f'setting "{key}": each option is "value" or ["value", "Label"]')
    return out


def _setting(raw) -> Setting:
    if not isinstance(raw, dict):
        raise ManifestError('each entry of "settings" is an object')
    key = raw.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise ManifestError(f"setting key {key!r}: lowercase letters, digits and _, starting with a letter")
    t = raw.get("type")
    if t not in SETTING_TYPES:
        raise ManifestError(f'setting "{key}": type is one of {", ".join(SETTING_TYPES)}')
    s = Setting(key=key, type=t, label=_text(raw, "label") or key, default=None)
    s.description = _text(raw, "description", limit=500)
    s.from_setting = _text(raw, "from_setting", limit=80) or None
    if t in ("choice", "multi") and ("options" in raw or t == "choice"):
        s.options = _options(raw.get("options"), key)
    for bound in ("min", "max"):
        if bound in raw:
            if t not in ("int", "float") or not isinstance(raw[bound], int | float) or isinstance(raw[bound], bool):
                raise ManifestError(f'setting "{key}": "{bound}" is a number, for int and float settings')
            setattr(s, bound, float(raw[bound]))
    fallback = {"bool": False, "int": 0, "float": 0.0, "text": "", "multi": []}
    default = raw["default"] if "default" in raw else fallback.get(t, s.options[0][0] if s.options else "")
    try:
        s.default = s.clean(default)
    except ManifestError as e:
        raise ManifestError(f'setting "{key}": the default does not fit ({e})') from e
    return s


def parse(data: object, folder: Path) -> Manifest:
    """The manifest in ``data`` (the parsed JSON) of the plugin in ``folder``."""
    if not isinstance(data, dict):
        raise ManifestError("manifest.json must hold an object")
    pid = data.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        raise ManifestError('"id": 2–64 lowercase letters, digits and -, e.g. "arxiv-today"')
    if pid != folder.name:
        raise ManifestError(f'"id" is {pid!r} but the folder is called {folder.name!r}; they must match')
    api = data.get("api")
    m = API_RE.match(str(api).strip()) if isinstance(api, str | int) and not isinstance(api, bool) else None
    if not m:
        raise ManifestError('"api" is the plugin API the plugin was written for, e.g. "^1.0"')
    man = Manifest(
        id=pid,
        name=_text(data, "name", required=True, limit=80),
        version=_text(data, "version", required=True, limit=40),
        api=(int(m.group(1)), int(m.group(2) or 0)),
        folder=folder,
        description=_text(data, "description", limit=500),
        author=_text(data, "author", limit=120),
    )
    if data.get("core", False) not in (True, False):
        raise ManifestError('"core" is true or false')
    man.core = bool(data.get("core"))
    if data.get("enabled_by_default", man.core) not in (True, False):
        raise ManifestError('"enabled_by_default" is true or false')
    man.enabled_by_default = bool(data.get("enabled_by_default", man.core))
    man.enabled_from_setting = _text(data, "enabled_from_setting", limit=80) or None
    for part in ("backend", "frontend", "styles"):
        rel = _text(data, part, limit=200)
        if part == "frontend" and rel.startswith("core:"):
            if not man.core:
                raise ManifestError('"frontend": "core:…" is only for the core plugins that come with Synapse')
            man.frontend = rel
            continue
        if rel:
            path = _inside(folder, rel)
            if not path.exists():
                raise ManifestError(f'"{part}": {rel} does not exist')
            setattr(man, part, rel)
    perms = data.get("permissions") or {}
    if not isinstance(perms, dict):
        raise ManifestError('"permissions" must be an object')
    unknown = set(perms) - {"network", "library", "mcp"}
    if unknown:
        raise ManifestError(f"unknown permission {sorted(unknown)[0]!r} (does it need a newer Synapse?)")
    network = perms.get("network") or []
    if not isinstance(network, list) or not all(isinstance(h, str) and HOST_RE.match(h.lower()) for h in network):
        raise ManifestError('"network" is a list of host names, e.g. ["export.arxiv.org"]')
    man.network = sorted({h.lower() for h in network})
    library = perms.get("library") or []
    if not isinstance(library, list) or any(p not in LIBRARY_PERMISSIONS for p in library):
        raise ManifestError(f'"library" is a list of {", ".join(LIBRARY_PERMISSIONS)}')
    man.library = [p for p in LIBRARY_PERMISSIONS if p in library]
    if (perms.get("mcp") or False) not in (True, False):
        raise ManifestError('"mcp" is true or false')
    man.mcp = bool(perms.get("mcp"))
    python = data.get("python") or []
    if not isinstance(python, list) or not all(isinstance(r, str) for r in python):
        raise ManifestError('"python" is a list of package requirements, e.g. ["scirate>=0.0.7"]')
    from ..config import APP_ROOT
    from .deps import check, core_packages
    from .errors import PluginError

    try:
        man.python = check(python, core_packages(APP_ROOT))
    except PluginError as e:
        raise ManifestError(str(e)) from e
    raw_settings = data.get("settings") or []
    if not isinstance(raw_settings, list):
        raise ManifestError('"settings" must be a list')
    for raw in raw_settings:
        s = _setting(raw)
        if man.setting(s.key):
            raise ManifestError(f'setting "{s.key}" appears twice')
        man.settings.append(s)
    return man


def read(folder: Path) -> Manifest:
    """The manifest of the plugin in ``folder``."""
    path = folder / "manifest.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError as e:
        raise ManifestError("manifest.json is missing") from e
    except (OSError, ValueError) as e:
        raise ManifestError(f"manifest.json cannot be read: {e}") from e
    return parse(data, folder)
