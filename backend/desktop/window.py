"""Opening Synapse in a window of its own (``python server.py --window``), like a program rather
than a tab of the browser you browse with.

In order of preference:

1. **Synapse installed as an app** by Chrome, Edge or Brave ("Install page as app…"). It has the
   Synapse icon in the Dock and in the app switcher. macOS only: the browser keeps such apps in
   ``~/Applications`` as small app bundles whose ``Info.plist`` names the address they open.
2. **A Chromium browser in app mode** (``--app=<address>``): a window without tabs or address bar,
   shown with the browser's icon.
3. **The default browser**, in a new window when it can.

The app's own tabs (``frontend/src/tabs/``) hold the PDFs either way.
"""

from __future__ import annotations

import json
import logging
import os
import plistlib
import shutil
import ssl
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

log = logging.getLogger(__name__)

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CHROME_APP_KEY = "CrAppModeShortcutURL"  # the address an app bundle made by a Chromium browser opens

# Chromium browsers, most likely first.
MAC_BROWSERS = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Vivaldi", "Google Chrome Beta")
LINUX_BROWSERS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
    "vivaldi",
)
WINDOWS_BROWSERS = (
    r"Google\Chrome\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
)


def synapse_at(url: str, timeout: float = 1.5) -> bool | None:
    """Whether Synapse answers at ``url``: True, False (something else does) or None (nothing)."""
    ctx = ssl._create_unverified_context() if url.startswith("https:") else None  # noqa: S323 - this computer
    try:
        with urlopen(url.rstrip("/") + "/manifest.webmanifest", timeout=timeout, context=ctx) as r:  # noqa: S310
            return json.loads(r.read(65536)).get("name") == "Synapse"
    except HTTPError:
        return False
    except (URLError, OSError):
        return None
    except ValueError:
        return False


def _same_server(a: str, b: str) -> bool:
    """Two addresses of this server: the same scheme, port and local host."""
    ua, ub = urlsplit(a), urlsplit(b)
    port = lambda u: u.port or (443 if u.scheme == "https" else 80)  # noqa: E731
    hosts = ((ua.hostname or "").lower(), (ub.hostname or "").lower())
    return (
        ua.scheme == ub.scheme
        and port(ua) == port(ub)
        and (hosts[0] == hosts[1] or (hosts[0] in LOOPBACK and hosts[1] in LOOPBACK))
    )


def installed_apps(home: Path | None = None) -> list[tuple[Path, str]]:
    """The apps Chromium browsers installed on this Mac: (bundle, address it opens)."""
    base = (home or Path.home()) / "Applications"
    found = []
    for bundle in [*base.glob("*.app"), *base.glob("*/*.app")]:
        try:
            with open(bundle / "Contents" / "Info.plist", "rb") as f:
                url = plistlib.load(f).get(CHROME_APP_KEY)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        if isinstance(url, str):
            found.append((bundle, url))
    return found


def installed_app(url: str, home: Path | None = None) -> Path | None:
    """Synapse installed as an app for this server's address, if it was."""
    for bundle, app_url in installed_apps(home):
        if _same_server(app_url, url):
            return bundle
    return None


def chromium(platform: str = sys.platform, home: Path | None = None) -> str | None:
    """The program of a Chromium browser on this computer."""
    home = home or Path.home()
    if platform == "darwin":
        for name in MAC_BROWSERS:
            for apps in (Path("/Applications"), home / "Applications"):
                exe = apps / f"{name}.app" / "Contents" / "MacOS" / name
                if exe.exists():
                    return str(exe)
        return None
    if platform.startswith("win"):
        roots = [os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for rel in WINDOWS_BROWSERS:
            for root in filter(None, roots):
                exe = Path(root) / rel
                if exe.exists():
                    return str(exe)
        return None
    for name in LINUX_BROWSERS:
        if path := shutil.which(name):
            return path
    return None


def _detached(cmd: list[str]) -> None:
    """Start a program that outlives this one (the browser must not stop with the server)."""
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        kw["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)  # noqa: S603


def open_window(url: str) -> str:
    """Open Synapse at ``url`` in a window of its own. Says how, for the log."""
    if sys.platform == "darwin" and (app := installed_app(url)):
        _detached(["open", str(app)])
        return f"opened the installed Synapse app ({app})"
    if exe := chromium():
        _detached([exe, f"--app={url}"])
        tip = ""
        if sys.platform == "darwin":
            tip = (
                f" — for the Synapse icon in the Dock, install it once: open {url} in Chrome and choose "
                "Cast, Save and Share → Install Page as App… (see docs/desktop.md)"
            )
        return f"opened a window of {Path(exe).name}{tip}"
    webbrowser.open_new(url)
    return "no Chromium browser found (Chrome, Edge, Brave…): opened your default browser instead"
