"""What every part of the companion shares: messages, the activity log, where its files live, the
connection profile with its settings, and how to start this program again."""

from __future__ import annotations

import collections
import copy
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

ENV_TOKEN = "SYNAPSE_COMPANION_TOKEN"
CHILD_TOKEN = "SYNAPSE_MCP_TOKEN"  # how a child gets its token when it cannot read a config file
LOG_MAX_BYTES = 1024 * 1024


class Fail(Exception):
    """An error to show the user (no traceback)."""


def clean(text: object) -> str:
    """Text from the server for the terminal or the window, without control characters."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(text)).strip()


# ---------------------------------------------------------------------- the activity log

_recent: collections.deque[str] = collections.deque(maxlen=300)
_log_lock = threading.Lock()
_log_file: Path | None = None


def log_to(path: Path | None) -> None:
    """Also append the log to ``path`` (a background service has no terminal), kept under 1 MB."""
    global _log_file
    _log_file = path


def log(message: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {clean(message)}"
    with _log_lock:
        _recent.append(line)
        if _log_file is not None:
            try:
                if _log_file.exists() and _log_file.stat().st_size > LOG_MAX_BYTES:
                    _log_file.replace(_log_file.with_name(_log_file.name + ".old"))
                fd = os.open(_log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d ") + line + "\n")
            except OSError:
                pass
    if sys.stdout is not None:  # pythonw has none
        try:
            print(line, flush=True)
        except (OSError, ValueError):
            pass


def recent_log() -> list[str]:
    with _log_lock:
        return list(_recent)


# ---------------------------------------------------------------------- files


def home() -> Path:
    if env := os.environ.get("SYNAPSE_COMPANION_HOME"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "synapse-companion"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "synapse-companion"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "synapse-companion"


def check_profile_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        raise Fail("A profile name is letters, digits, - and _ (up to 40).")
    return name


def profile_path(name: str) -> Path:
    return home() / f"{check_profile_name(name)}.json"


def make_home() -> Path:
    """The companion's folder, private to this user (it holds the token, the log and the window key)."""
    folder = home()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, readable by this user only (it may hold a token)."""
    if path.parent == home():
        make_home()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------- the profile

# Sites the AI tools may open when "Open pages of these sites" is on: arXiv, DOI links and the major
# physics publishers (a DOI redirects to its publisher). Each also covers its subdomains.
DEFAULT_SITES = [
    "arxiv.org",
    "doi.org",
    "aps.org",
    "iop.org",
    "aip.org",
    "nature.com",
    "springer.com",
    "sciencedirect.com",
    "elsevier.com",
    "wiley.com",
    "science.org",
    "pnas.org",
    "cambridge.org",
    "oup.com",
    "optica.org",
    "worldscientific.com",
    "epj.org",
    "edpsciences.org",
    "aanda.org",
    "jps.jp",
    "scipost.org",
    "quantum-journal.org",
    "royalsocietypublishing.org",
    "annualreviews.org",
    "tandfonline.com",
    "acs.org",
    "rsc.org",
    "ieee.org",
    "mdpi.com",
    "frontiersin.org",
]
MAX_SITES = 100
SITE = re.compile(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]")

# The AI tools the companion knows. Claude Code runs without its own tools; Codex runs with its shell
# off in a sandbox, but the companion cannot check what else it has, so it is off until the user turns
# it on. ``access``: what the user allows beyond Synapse's own tools (all off by default).
DEFAULT_SETTINGS: dict = {
    "providers": {
        "claude": {"enabled": True, "models": None, "efforts": None},  # None: every one the tool offers
        "codex": {"enabled": False, "models": None, "efforts": None},
    },
    "access": {"web_search": False, "web_fetch": False, "sites": DEFAULT_SITES, "library": False},
    "max_running": 2,
    "extension_updates": True,  # apply updates that do not change the extension's permissions
}
MAX_RUNNING = 2  # never more AI tools at once, whatever the settings or the server say


def clean_site(text: object) -> str | None:
    """A site as the user typed it ("https://www.nature.com/nphys", "*.aps.org") as a host name, or
    None when it is not one (an address, a single label, a port, other characters)."""
    host = str(text).strip().lower()
    host = re.sub(r"^[a-z]+://", "", host).split("/", 1)[0].removeprefix("*.").rstrip(".")
    return host if SITE.fullmatch(host) else None


def site_allowed(host: str, sites: list[str]) -> bool:
    """Whether ``host`` is one of ``sites`` or a subdomain of one."""
    host = host.lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in sites)


def _access(value: object) -> dict:
    out = copy.deepcopy(DEFAULT_SETTINGS["access"])
    if not isinstance(value, dict):
        return out
    for key in ("web_search", "web_fetch", "library"):
        if isinstance(value.get(key), bool):
            out[key] = value[key]
    if isinstance(value.get("sites"), list):
        sites = [s for s in map(clean_site, value["sites"]) if s]
        out["sites"] = list(dict.fromkeys(sites))[:MAX_SITES]
    return out


def normal_settings(value: object) -> dict:
    """``value`` (stored or sent by the window) checked against the defaults: unknown keys are
    dropped, wrong types fall back to the default."""
    out = copy.deepcopy(DEFAULT_SETTINGS)
    if not isinstance(value, dict):
        return out
    providers = value.get("providers")
    for pid, default in out["providers"].items():
        given = providers.get(pid) if isinstance(providers, dict) else None
        if not isinstance(given, dict):
            continue
        if isinstance(given.get("enabled"), bool):
            default["enabled"] = given["enabled"]
        for key in ("models", "efforts"):
            choice = given.get(key)
            if choice is None or (isinstance(choice, list) and all(isinstance(x, str) for x in choice)):
                default[key] = None if choice is None else list(dict.fromkeys(choice))[:32]
    out["access"] = _access(value.get("access"))
    running = value.get("max_running")
    if isinstance(running, int) and not isinstance(running, bool) and 1 <= running <= MAX_RUNNING:
        out["max_running"] = running
    if isinstance(value.get("extension_updates"), bool):
        out["extension_updates"] = value["extension_updates"]
    return out


def load_profile(name: str) -> dict | None:
    """The stored connection ``{server, token, client, name, extension, settings}``, or None when
    this profile is not connected yet."""
    from .remote import check_url

    profile = read_json(profile_path(name))
    try:
        check_url(str(profile["server"]))
        token, client = str(profile["token"]), str(profile["client"])
    except (KeyError, Fail):
        return None
    ext = profile.get("extension")
    return {
        **profile,
        "token": token,
        "client": client,
        "name": str(profile.get("name") or "computer")[:60],
        "extension": ext if isinstance(ext, dict) else None,
        "settings": normal_settings(profile.get("settings")),
    }


def save_profile(name: str, profile: dict) -> None:
    write_private(profile_path(name), json.dumps(profile, indent=2))


# ---------------------------------------------------------------------- this program


def program_path() -> Path:
    """What ``python <path>`` runs to start this program again: the .pyz it was started from, or
    the package folder of a repository checkout."""
    package = Path(__file__).resolve().parent
    return package.parent if package.parent.is_file() else package


def is_packaged() -> bool:
    return program_path().is_file()


def python_exe(*, windowless: bool = False) -> str:
    """The Python running this program; on Windows ``pythonw`` for a start without a console."""
    exe = Path(sys.executable)
    if windowless and sys.platform == "win32" and exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def command(*args: str, windowless: bool = False) -> list[str]:
    """The command line that runs this program with ``args``."""
    return [python_exe(windowless=windowless), str(program_path()), *args]
