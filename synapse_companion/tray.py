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
import time
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
        self.restarts: list[float] = []

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
            # Nobody asked the companion to quit, so the icon should still be there: start it again
            # (a few times only, in case it cannot run at all).
            now = time.monotonic()
            self.restarts = [t for t in self.restarts if now - t < 300]
            if len(self.restarts) < 3:
                self.restarts.append(now)
                time.sleep(2)
                if proc is self.proc:
                    self.start()

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


def _render(name: str, scale: float):
    """static/<name> as an icon at the sizes the system asks for, drawn ``scale`` of the full size."""
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray((resources.files(__package__) / "static" / name).read_bytes()))
    icon = QIcon()
    for size in (16, 22, 32, 44, 64, 128, 256, 512):
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        margin = size * (1 - scale) / 2
        renderer.render(painter, QRectF(margin, margin, size * scale, size * scale))
        painter.end()
        icon.addPixmap(QPixmap.fromImage(image))
    return icon


def _png(name: str, size: int, scale: float) -> bytes:
    """static/<name> as a PNG of ``size`` pixels, drawn ``scale`` of the full size."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    pixmap = _render(name, scale).pixmap(size, size)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(data.data())


class _Dock:
    """macOS: the Dock shows this process only while a window is open (the menu bar icon stays).

    The Dock icon is set on NSApplication directly: Qt's setWindowIcon draws it at the Dock tile's
    size, which is 0 while the process has no Dock entry, so the Dock showed Python's own icon
    (the rocket) until it happened to be set again once the entry existed."""

    def __init__(self, png: bytes):
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "libobjc.dylib")
        objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = objc.sel_registerName.argtypes = [ctypes.c_char_p]
        p, msg = ctypes.c_void_p, ("objc_msgSend", objc)
        self._sel = objc.sel_registerName
        self._send = ctypes.CFUNCTYPE(p, p, p)(msg)
        self._send_obj = ctypes.CFUNCTYPE(p, p, p, p)(msg)
        self._send_bytes = ctypes.CFUNCTYPE(p, p, p, ctypes.c_char_p, ctypes.c_ulong)(msg)
        self._policy = ctypes.CFUNCTYPE(ctypes.c_bool, p, p, ctypes.c_long)(msg)
        self._app = self._send(objc.objc_getClass(b"NSApplication"), self._sel(b"sharedApplication"))
        data = self._send_bytes(objc.objc_getClass(b"NSData"), self._sel(b"dataWithBytes:length:"), png, len(png))
        image = self._send(objc.objc_getClass(b"NSImage"), self._sel(b"alloc"))
        self._image = self._send_obj(image, self._sel(b"initWithData:"), data)  # kept for the process's life

    def set_icon(self) -> None:
        if self._image:
            self._send_obj(self._app, self._sel(b"setApplicationIconImage:"), self._image)

    def show(self, visible: bool) -> None:
        self.set_icon()  # before the Dock entry is made, so it does not start with the rocket
        self._policy(self._app, self._sel(b"setActivationPolicy:"), 0 if visible else 1)  # regular · accessory
        self.set_icon()


def main() -> int:
    """``synapse_companion tray``, started by the companion with ``{port, key}`` on stdin."""
    try:
        info = json.loads(sys.stdin.read(4096))
        companion = _Companion(int(info["port"]), str(info["key"]))
    except (ValueError, KeyError, TypeError):
        return 2
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    app = QApplication([sys.argv[0], "--name", "synapse-companion"])
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return NO_TRAY
    app.setQuitOnLastWindowClosed(False)  # Qt has no windows of its own here
    app.setApplicationDisplayName("Synapse Companion")
    dock: _Dock | None = None
    if sys.platform == "darwin":
        try:  # Dock icons have a margin around them
            dock = _Dock(_png("icon.svg", 512, 0.8))
            dock.show(False)
        except (OSError, AttributeError, TypeError):
            dock = None
    else:
        app.setWindowIcon(_render("icon.svg", 1.0))
    if sys.platform == "darwin":
        glyph = _render("tray.svg", 0.8)
        glyph.setIsMask(True)  # a menu bar glyph: macOS draws it in the menu bar's own colour
        icon = QSystemTrayIcon(glyph)
    else:
        icon = QSystemTrayIcon(_render("icon.svg", 1.0))
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

    docked: list[bool | None] = [None]

    def refresh() -> None:
        try:
            state.clear()
            state.update(companion.call("/api/state"))
            failures[0] = 0
        except (urllib.error.URLError, OSError, ValueError) as e:
            failures[0] += 1
            print(f"state failed ({failures[0]}): {e!r}", file=sys.stderr, flush=True)
            if failures[0] >= 3:  # the companion quit or restarted (a new one starts its own icon)
                app.quit()
            return
        window_open = bool(state.get("window_open"))
        if dock and docked[0] != window_open:
            docked[0] = window_open
            dock.show(window_open)
            if window_open:  # the Dock makes its entry asynchronously and may reset the icon meanwhile
                QTimer.singleShot(250, dock.set_icon)
        text = summary(state)
        status.setText(text)
        library.setEnabled(bool(state.get("server")))
        icon.setToolTip(f"Synapse Companion\n{text}")

    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(1000)
    refresh()
    icon.show()
    ask("/api/tray", {"shown": True})
    code = app.exec()
    print(f"exec returned {code}", file=sys.stderr, flush=True)
    icon.hide()
    return 0
