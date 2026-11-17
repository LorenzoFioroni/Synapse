"""The companion's icon in the system tray (the menu bar on macOS): its state at a glance, the
window, Synapse itself, and Quit.

The icon needs PySide6 (Qt), the one part of the companion outside Python's standard library, so it
is optional. It runs as a child process because Qt ends the whole process when it cannot reach the
display (a missing system library is enough): the companion keeps working whatever happens to the
icon. The child talks to the companion as the window does, through its API on 127.0.0.1, with the
window key it receives on its standard input (never on a command line, which other users can read).
"""

from __future__ import annotations

import collections
import importlib.util
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from importlib import resources
from urllib.parse import urlsplit

from .common import command, log, python_exe

NO_TRAY = 4  # the child's exit status when the desktop has no system tray

STATES = {
    "connected": "Connected",
    "connecting": "Connecting…",
    "unreachable": "Cannot reach the server",
    "refused": "The server refused the token",
    "stopped": "Not running",
    "none": "Not connected",
}


def summary(state: dict) -> str:
    """One line for the menu and the tooltip, from the window's state."""
    conn = state.get("connection") or {}
    text = STATES.get(str(conn.get("state")), str(conn.get("state")))
    if state.get("server"):
        text += f" · {urlsplit(state['server']).hostname}"
    if state.get("running"):
        text += f" · answering {len(state['running'])}"
    return text


def install_hint() -> str:
    """The command that gives the Python running the companion PySide6 (``--user`` fails in a venv)."""
    user = "" if sys.prefix != sys.base_prefix else " --user"
    return f"{python_exe()} -m pip install{user} PySide6-Essentials"


def _desktop() -> bool:
    if os.environ.get("SYNAPSE_COMPANION_NO_WINDOW"):  # tests and headless use
        return False
    return sys.platform in ("win32", "darwin") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# ---------------------------------------------------------------------- in the companion


class TrayProcess:
    """Starts the icon's process and knows what became of it, for the window."""

    def __init__(self, port: int, key: str):
        self.port, self.key = port, key
        self.status = "off"  # off · missing · starting · shown · no-tray · failed
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if not _desktop():
            return
        if importlib.util.find_spec("PySide6") is None:
            self.status = "missing"
            log(f"No tray icon: it needs PySide6 ({install_hint()}).")
            return
        self.status = "starting"
        try:
            self.proc = subprocess.Popen(
                command("tray", windowless=True),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps({"port": self.port, "key": self.key}))
            self.proc.stdin.close()
        except OSError as e:
            self.status = "failed"
            log(f"The tray icon did not start: {e}")
            return
        threading.Thread(target=self._watch, args=(self.proc,), daemon=True, name="tray").start()

    def _watch(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        last: collections.deque[str] = collections.deque(proc.stderr, maxlen=4)
        code = proc.wait()
        if proc is not self.proc:
            return
        if code == NO_TRAY:
            self.status = "no-tray"
            log("No tray icon: this desktop has no system tray.")
        else:
            self.status = "failed"
            log(f"The tray icon stopped (status {code}). " + " ".join(line.strip() for line in last)[:300])

    def stop(self) -> None:
        proc, self.proc, self.status = self.proc, None, "off"
        if proc and proc.poll() is None:
            proc.terminate()

    def describe(self) -> dict:
        return {"status": self.status, "install": install_hint()}


# ---------------------------------------------------------------------- the icon's own process


class _Companion:
    """The running companion, seen through its window API."""

    def __init__(self, port: int, key: str):
        self.base, self.key = f"http://127.0.0.1:{port}", key

    def call(self, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            method="GET" if body is None else "POST",
            headers={"X-Companion-Key": self.key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            value = json.loads(resp.read())
        return value if isinstance(value, dict) else {}


def main() -> int:
    """``synapse_companion tray``, started by the companion with ``{port, key}`` on stdin."""
    try:
        info = json.loads(sys.stdin.read(4096))
        companion = _Companion(int(info["port"]), str(info["key"]))
    except (ValueError, KeyError, TypeError):
        return 2
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    app = QApplication([sys.argv[0], "--name", "synapse-companion"])
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return NO_TRAY
    app.setQuitOnLastWindowClosed(False)  # Qt has no windows of its own here
    app.setApplicationDisplayName("Synapse Companion")
    pixmap = QPixmap()
    pixmap.loadFromData((resources.files(__package__) / "static" / "icon.svg").read_bytes(), "SVG")
    icon = QSystemTrayIcon(QIcon(pixmap))
    state: dict = {}
    failures = [0]

    def ask(path: str, body: dict | None = None) -> None:
        try:
            companion.call(path, body or {})
        except (urllib.error.URLError, OSError, ValueError):
            pass  # the next refresh notices a companion that is gone

    menu = QMenu()
    status = menu.addAction("Synapse Companion")
    status.setEnabled(False)
    menu.addSeparator()
    menu.addAction("Open Synapse Companion").triggered.connect(lambda: ask("/api/open"))
    library = menu.addAction("Open Synapse in the Browser")
    library.triggered.connect(lambda: state.get("server") and webbrowser.open(state["server"]))
    menu.addSeparator()
    menu.addAction("Quit Synapse Companion").triggered.connect(lambda: ask("/api/quit"))
    icon.setContextMenu(menu)

    def clicked(reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger and sys.platform != "darwin":
            ask("/api/open")  # a plain click; on macOS a click opens the menu

    icon.activated.connect(clicked)

    def refresh() -> None:
        try:
            state.clear()
            state.update(companion.call("/api/state"))
            failures[0] = 0
        except (urllib.error.URLError, OSError, ValueError):
            failures[0] += 1
            if failures[0] >= 3:  # the companion quit or restarted (a new one starts its own icon)
                app.quit()
            return
        text = summary(state)
        status.setText(text)
        library.setEnabled(bool(state.get("server")))
        icon.setToolTip(f"Synapse Companion\n{text}")

    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(2000)
    refresh()
    icon.show()
    ask("/api/tray", {"shown": True})
    app.exec()
    icon.hide()
    return 0
