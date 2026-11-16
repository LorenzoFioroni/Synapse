"""The companion as a background program: it answers questions (runner.py), keeps the browser
extension current, and gives its window (ui.py) the state to show and the actions to run."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from . import autostart
from .common import (
    MAX_RUNNING,
    Fail,
    home,
    is_packaged,
    load_profile,
    log,
    normal_settings,
    profile_path,
    program_path,
    recent_log,
    save_profile,
)
from .extension import (
    ExtensionReview,
    extension_id,
    folder_build,
    last_seen,
    register_host,
    registered_browsers,
    sync_extension,
)
from .providers import PROVIDERS
from .remote import ApiError, NetworkError, Server, check_url
from .runner import Companion

SYNC_EVERY = 3600  # how often the extension and the program are compared with the server's


def own_build() -> str:
    """The build of this program, computed as the server computes its download's (bundle.py)."""
    path = program_path()
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return ""  # a checkout: never "out of date"


class Service:
    def __init__(self, profile_name: str = "default"):
        self.profile_name = profile_name
        self.lock = threading.RLock()
        self.companion: Companion | None = None
        self.hello: dict = {}
        self.review: list[str] | None = None  # extension update waiting for the user's approval
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.restart = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        profile = load_profile(self.profile_name)
        if profile:
            self._start_companion(profile)
        else:
            log("Not connected to a library yet: open the window and connect.")
        threading.Thread(target=self._maintain, daemon=True, name="maintain").start()

    def quit(self, *, restart: bool = False) -> None:
        self.restart = restart
        self.stopped.set()
        self.wake.set()
        self._stop_companion()

    def _start_companion(self, profile: dict) -> None:
        self._stop_companion()
        companion = Companion(profile, profile["name"], profile_name=self.profile_name)
        with self.lock:
            self.companion = companion
        threading.Thread(target=self._answer, args=(companion,), daemon=True, name="poll").start()

    def _answer(self, companion: Companion) -> None:
        try:
            companion.run()
        except Fail as e:
            log(str(e))
        except Exception as e:  # the window stays usable whatever happens here
            companion.state, companion.state_message = "unreachable", "The companion stopped unexpectedly."
            log(f"stopped answering: {type(e).__name__}")

    def _stop_companion(self) -> None:
        with self.lock:
            companion, self.companion = self.companion, None
        if companion:
            companion.shutdown()

    def _maintain(self) -> None:
        """At start and hourly: what the server offers; apply an extension update when allowed."""
        while not self.stopped.is_set():
            self.wake.clear()
            try:
                self.check()
            except Exception as e:  # an update problem never ends the companion
                log(f"update check skipped: {e}")
            self.wake.wait(SYNC_EVERY)

    def check(self) -> None:
        profile = load_profile(self.profile_name)
        if not profile:
            return
        server = Server(profile["server"], profile["token"])
        try:
            self.hello = server.hello()
        except (ApiError, NetworkError):
            return
        folder = self.extension_dir(profile)
        if profile.get("extension") and profile["settings"]["extension_updates"]:
            try:
                if sync_extension(server, folder):
                    log("The browser extension was updated; it reloads itself.")
                self.review = None
            except ExtensionReview as e:
                if self.review != e.keys:
                    log(f"An extension update changes {', '.join(e.keys)}: approve it in the window.")
                self.review = e.keys

    # ------------------------------------------------------------------ actions
    def connect(self, address: str, token: str, name: str) -> None:
        base = check_url(address)
        token = token.strip()
        previous = load_profile(self.profile_name) or {}
        if not token and previous.get("server") == base:
            token = previous["token"]  # renaming the computer keeps the stored token
        if not token:
            raise Fail("Enter the companion token from Synapse (Settings → AI Access).")
        try:
            hello = Server(base, token).hello()
        except ApiError as e:
            if e.status == 401:
                raise Fail("The server did not accept this token. It must be a companion token.") from None
            raise Fail(f"The server answered: {e}") from None
        except NetworkError as e:
            raise Fail(f"Cannot reach {base}: {e}") from None
        if hello.get("app") != "synapse":
            raise Fail("That address does not answer like Synapse.")
        profile = {
            "server": base,
            "token": token,
            "client": previous.get("client") or uuid.uuid4().hex,
            "name": (name.strip() or previous.get("name") or socket.gethostname() or "computer")[:60],
            "extension": previous.get("extension"),
            "settings": previous.get("settings") or normal_settings(None),
        }
        save_profile(self.profile_name, profile)
        self.hello = hello
        log(f"Connected to {base} as '{profile['name']}'.")
        self._start_companion(load_profile(self.profile_name) or profile)
        self.wake.set()

    def disconnect(self) -> None:
        self._stop_companion()
        profile_path(self.profile_name).unlink(missing_ok=True)
        self.hello = {}
        log("Disconnected: the token was removed from this computer.")

    def set_settings(self, settings: object) -> None:
        profile = self._profile()
        profile["settings"] = normal_settings(settings)
        save_profile(self.profile_name, profile)
        with self.lock:
            if self.companion:
                self.companion.update_settings(profile["settings"])
        log("Settings saved.")

    def install_extension(self, *, approve: bool = False) -> None:
        """Download (or refresh) the extension folder and register the native messaging host."""
        profile = self._profile()
        folder = self.extension_dir(profile)
        first = not profile.get("extension")
        try:
            sync_extension(
                Server(profile["server"], profile["token"]), folder, force=first, review=lambda keys: approve or first
            )
        except ExtensionReview as e:
            self.review = e.keys
            raise Fail(f"This update changes what the extension may do: {', '.join(e.keys)}.") from None
        except (ApiError, NetworkError) as e:
            raise Fail(f"Could not download the extension: {e}") from None
        self.review = None
        ext_id = extension_id(folder)
        if not ext_id:
            raise Fail("The downloaded extension has no fixed id (an old server?).")
        browsers = register_host(self.profile_name, ext_id)
        profile["extension"] = {"dir": str(folder)}
        save_profile(self.profile_name, profile)
        log(f"Extension folder ready: {folder}. Registered with: {', '.join(browsers) or 'no browser found'}.")

    def set_autostart(self, on: bool) -> None:
        autostart.set_enabled(self.profile_name, on, self.icon())
        log("Starts at login." if on else "No longer starts at login.")

    def update_program(self) -> None:
        """Replace the .pyz with the server's and start again."""
        if not is_packaged():
            raise Fail("This companion runs from a repository checkout; update it with git.")
        profile = self._profile()
        try:
            data = Server(profile["server"], profile["token"]).call("GET", "/api/companion/script", raw=True)
        except (ApiError, NetworkError) as e:
            raise Fail(f"Could not download the companion: {e}") from None
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
                ok = {"__main__.py", "synapse_companion/cli.py"} <= names and z.testzip() is None
        except (zipfile.BadZipFile, OSError, ValueError):
            ok = False
        if not ok:
            raise Fail("The download is not the Synapse companion; nothing was changed.")
        target = program_path()
        fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".new", dir=target.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            shutil.copymode(target, tmp)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
        log("The companion was updated; starting again.")
        self.quit(restart=True)

    # ------------------------------------------------------------------ state
    def _profile(self) -> dict:
        profile = load_profile(self.profile_name)
        if profile is None:
            raise Fail("Connect to a library first.")
        return profile

    def extension_dir(self, profile: dict) -> Path:
        ext = profile.get("extension") or {}
        return Path(ext["dir"]) if ext.get("dir") else home() / f"{self.profile_name}-extension"

    def icon(self) -> Path | None:
        """The window's icon as a file, for menu entries."""
        from importlib import resources

        target = home() / "icon.svg"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((resources.files(__package__) / "static" / "icon.svg").read_bytes())
        except OSError:
            return None
        return target

    def state(self) -> dict:
        profile = load_profile(self.profile_name)
        with self.lock:
            companion = self.companion
        settings = profile["settings"] if profile else normal_settings(None)
        providers = []
        found = companion.providers if companion else {}
        for cls in PROVIDERS:
            setting = settings["providers"][cls.id]
            exe = found[cls.id].exe if cls.id in found else shutil.which(cls.id)
            provider = cls(exe or "")
            providers.append(
                {
                    "id": cls.id,
                    "label": cls.label,
                    "found": bool(exe),
                    "enabled": setting["enabled"],
                    "models": [
                        {"id": i, "label": label, "on": i in provider.offered(setting, "models")}
                        for i, label in cls.models
                    ],
                    "efforts": [{"id": e, "on": e in provider.offered(setting, "efforts")} for e in cls.efforts],
                }
            )
        state: dict = {
            "profile": self.profile_name,
            "connected": profile is not None,
            "server": profile["server"] if profile else "",
            "name": profile["name"] if profile else socket.gethostname(),
            "connection": {
                "state": companion.state if companion else ("stopped" if profile else "none"),
                "message": companion.state_message if companion else "",
            },
            "settings": settings,
            "max_running": MAX_RUNNING,
            "providers": providers,
            "running": companion.running() if companion else [],
            "autostart": autostart.enabled(self.profile_name),
            "autostart_where": autostart.describe(self.profile_name),
            "program": str(program_path()),
            "update": bool(is_packaged() and self.hello.get("script") and self.hello["script"] != own_build()),
            "log": recent_log()[-60:],
        }
        state["extension"] = self._extension_state(profile)
        return state

    def _extension_state(self, profile: dict | None) -> dict:
        if not profile:
            return {"installed": False}
        folder = self.extension_dir(profile)
        installed = bool(profile.get("extension")) and (folder / "manifest.json").is_file()
        ext_id = extension_id(folder) if installed else None
        build = folder_build(folder) if installed else None
        seen = last_seen(self.profile_name)
        server_build = self.hello.get("extension") or None
        return {
            "installed": installed,
            "dir": str(folder),
            "id": ext_id,
            "up_to_date": None if not (installed and server_build) else build == server_build,
            "review": self.review,
            "browsers": registered_browsers(ext_id),
            "seen": seen
            and {
                "ago": max(0, int(time.time() - seen["time"])),
                "version": seen.get("version"),
                "current": seen.get("build") in (None, build),
            },
        }
