"""The browser extension on this computer: its folder (downloaded from the server, kept current),
and its registration with the browsers as a native messaging host, through which it reaches the
server via this companion (see native.py)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path

from .common import Fail, clean, command, home, read_json, write_private
from .remote import Server

HOST_NAME = "org.synapse.companion"


SECURITY_KEYS = (
    "permissions",
    "optional_permissions",
    "host_permissions",
    "optional_host_permissions",
    "content_scripts",
    "externally_connectable",
    "web_accessible_resources",
    "background",
    "content_security_policy",
)


class ExtensionReview(Fail):
    """An extension update changes what the extension may do and needs the user's say-so."""

    def __init__(self, keys: list[str]):
        super().__init__("The extension update changes: " + ", ".join(keys))
        self.keys = keys


def _safe_extract(data: bytes, target: Path) -> None:
    """Unpack the zip into ``target``: regular files only, nothing outside it."""
    root = target.resolve()
    try:
        _extract(data, root)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, RuntimeError, EOFError, NotImplementedError):
        raise Fail("The extension download could not be unpacked.") from None
    if not (root / "manifest.json").is_file():
        raise Fail("The download is not a browser extension (no manifest.json).")


def _extract(data: bytes, root: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > 2000 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
            raise Fail("The extension download is larger than expected.")
        for info in infos:
            name = info.filename
            mode = info.external_attr >> 16
            if info.is_dir():
                continue
            parts = name.split("/")
            if (
                name.startswith(("/", "\\"))
                or "\\" in name
                or re.match(r"[A-Za-z]:", name)
                or any(p in ("", ".", "..") for p in parts)
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
            ):
                raise Fail(f"The download contains an unsafe path: {clean(repr(name))}")
            dest = (root / name).resolve()
            if root not in dest.parents:
                raise Fail(f"The download contains an unsafe path: {clean(repr(name))}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def folder_build(folder: Path) -> str | None:
    """The build of the files in ``folder``, computed as the server does (names and contents of the
    regular, non-hidden files except build.json), so local edits show; None if it cannot be read."""
    digest = hashlib.sha256()
    try:
        for path in sorted(folder.rglob("*")):
            rel = path.relative_to(folder)
            if path.is_symlink() or not path.is_file() or any(p.startswith(".") for p in rel.parts):
                continue
            if rel.as_posix() == "build.json":
                continue
            digest.update(rel.as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    except OSError:
        return None
    return digest.hexdigest()[:16]


def _manifest(folder: Path) -> dict:
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def sync_extension(
    server: Server, folder: Path, *, force: bool = False, review: Callable[[list[str]], bool] | None = None
) -> bool:
    """Download the extension into ``folder`` when it differs from the server's. True if it changed.

    An update that changes the manifest's security-relevant keys (``SECURITY_KEYS``) is applied only
    if ``review`` approves the list of changed keys; without ``review`` it raises ``ExtensionReview``.
    A non-empty folder is replaced only if an earlier download put a build.json in it."""
    build = server.hello()["extension"]
    if not build:
        raise Fail("This server has no browser extension to download.")
    if not force and build == folder_build(folder):
        return False
    installed = folder.is_dir() and any(folder.iterdir())
    if installed and not (folder / "build.json").is_file():
        raise Fail(
            f"{folder} is not a folder this program installed (it has no build.json); choose another with --dir."
        )
    folder.parent.mkdir(parents=True, exist_ok=True)
    new = Path(tempfile.mkdtemp(prefix=folder.name + ".new-", dir=folder.parent))  # ours alone
    old = folder.with_name(f"{folder.name}.old-{uuid.uuid4().hex[:8]}")
    try:
        _safe_extract(server.call("GET", "/api/companion/extension", raw=True, timeout=120), new)
        if installed:
            before, after = _manifest(folder), _manifest(new)
            changed = [k for k in SECURITY_KEYS if before.get(k) != after.get(k)]
            if changed and not (review and review(changed)):
                raise ExtensionReview(changed)
        # Two renames, not one atomic step: a crash between them leaves the old copy as <folder>.old-…
        if folder.exists():
            os.replace(folder, old)
        try:
            os.replace(new, folder)
        except OSError:
            if old.exists():
                os.replace(old, folder)
            raise Fail("The extension folder could not be replaced; it was left as it was.") from None
    finally:
        shutil.rmtree(new, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    return True


# ---------------------------------------------------------------------- identity and status


def extension_id(folder: Path) -> str | None:
    """Chrome's id for the extension in ``folder``: from the manifest's public ``key``, the SHA-256
    of the key as letters a-p. Any unpacked extension with that key gets the same id."""
    key = _manifest(folder).get("key")
    if not isinstance(key, str):
        return None
    try:
        der = base64.b64decode(key, validate=True)
    except ValueError:
        return None
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32])


def seen_path(profile: str) -> Path:
    return home() / f"{profile}-extension-seen.json"


def record_seen(profile: str, version: object, build: object) -> None:
    """Called by the native host when the extension says hello: shown in the window."""
    entry = {
        "time": time.time(),
        "version": clean(version)[:20] if isinstance(version, str) else None,
        "build": clean(build)[:40] if isinstance(build, str) else None,
    }
    try:
        write_private(seen_path(profile), json.dumps(entry))
    except OSError:
        pass


def last_seen(profile: str) -> dict | None:
    entry = read_json(seen_path(profile))
    return entry if isinstance(entry.get("time"), (int, float)) else None


# ---------------------------------------------------------------------- native messaging host

# Where each Chromium browser looks for a user's native messaging hosts.
if sys.platform == "darwin":
    _SUPPORT = Path.home() / "Library" / "Application Support"
    BROWSER_DIRS = {
        "Chrome": _SUPPORT / "Google" / "Chrome",
        "Chromium": _SUPPORT / "Chromium",
        "Brave": _SUPPORT / "BraveSoftware" / "Brave-Browser",
        "Edge": _SUPPORT / "Microsoft Edge",
        "Vivaldi": _SUPPORT / "Vivaldi",
    }
else:
    _CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    BROWSER_DIRS = {
        "Chrome": _CONFIG / "google-chrome",
        "Chromium": _CONFIG / "chromium",
        "Brave": _CONFIG / "BraveSoftware" / "Brave-Browser",
        "Edge": _CONFIG / "microsoft-edge",
        "Vivaldi": _CONFIG / "vivaldi",
    }
REGISTRY_KEYS = {  # Windows: HKEY_CURRENT_USER\<key>\NativeMessagingHosts\<HOST_NAME> names the manifest
    "Chrome": r"Software\Google\Chrome",
    "Chromium": r"Software\Chromium",
    "Brave": r"Software\BraveSoftware\Brave-Browser",
    "Edge": r"Software\Microsoft\Edge",
    "Vivaldi": r"Software\Vivaldi",
}


def host_launcher(profile: str) -> Path:
    """The small script a browser starts for the extension. Browsers pass only the extension's
    origin, so the profile is written into it."""
    return home() / ("native-host.bat" if sys.platform == "win32" else "native-host")


def _launcher_text(profile: str) -> str:
    args = command("native-host", "--profile", profile)
    if sys.platform == "win32":
        return "@echo off\r\n" + " ".join(f'"{a}"' for a in args) + " %*\r\n"
    quoted = " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
    return f'#!/bin/sh\nexec {quoted} "$@"\n'


def host_manifest(ext_id: str, launcher: Path) -> dict:
    return {
        "name": HOST_NAME,
        "description": "Synapse Companion: passes the Synapse extension's requests to your library",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }


def write_host_manifest(folder: Path, ext_id: str, launcher: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{HOST_NAME}.json"
    path.write_text(json.dumps(host_manifest(ext_id, launcher), indent=2), encoding="utf-8")
    return path


def register_host(profile: str, ext_id: str) -> list[str]:
    """Let the browsers on this computer start this companion for the extension ``ext_id``. Answers
    the browsers it was registered with. One profile serves the extension: the last registered."""
    launcher = host_launcher(profile)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(_launcher_text(profile), encoding="utf-8")
    if sys.platform != "win32":
        launcher.chmod(0o700)
    done = []
    if sys.platform == "win32":
        import winreg

        manifest = write_host_manifest(home(), ext_id, launcher)
        for browser, key in REGISTRY_KEYS.items():
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{key}\NativeMessagingHosts\{HOST_NAME}") as k:
                    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(manifest))
                done.append(browser)
            except OSError:
                pass
        return done
    for browser, folder in BROWSER_DIRS.items():
        if folder.is_dir():  # only browsers that are installed (they made their folder)
            try:
                write_host_manifest(folder / "NativeMessagingHosts", ext_id, launcher)
                done.append(browser)
            except OSError:
                pass
    return done


def registered_browsers(ext_id: str | None) -> list[str]:
    """The browsers whose registration points at this computer's launcher for ``ext_id``."""
    if not ext_id:
        return []
    origin = f"chrome-extension://{ext_id}/"
    if sys.platform == "win32":
        import winreg

        found = []
        for browser, key in REGISTRY_KEYS.items():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{key}\NativeMessagingHosts\{HOST_NAME}") as k:
                    path = Path(winreg.QueryValue(k, None))
            except OSError:
                continue
            if origin in read_json(path).get("allowed_origins", []):
                found.append(browser)
        return found
    return [
        browser
        for browser, folder in BROWSER_DIRS.items()
        if origin in read_json(folder / "NativeMessagingHosts" / f"{HOST_NAME}.json").get("allowed_origins", [])
    ]
