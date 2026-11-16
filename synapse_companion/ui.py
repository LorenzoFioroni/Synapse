"""The companion's window: a small page served on 127.0.0.1 only, opened as an app window of a
Chromium browser (else the default browser).

Who may use it: the page's API needs the key in ``<profile>.window.json`` (readable by this user
only). A window is opened at ``/?ticket=…``, a one-time ticket valid for a minute that the page
exchanges for the key, which it keeps in that tab's session storage. Neither appears in a command
line, which other users of the computer can read: the browser is started on a private file that
redirects to the ticket's address. Another start of the program asks for a ticket by proving it
knows the key (an HMAC over a fresh nonce) and checks the answer's proof in turn, so a stale
window file never hands the key to whatever program now has the port. Every request must name the server by its own address (no DNS rebinding), and
changes must carry the key in a header and JSON, which other websites cannot send without a CORS
preflight that is never answered.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from .common import Fail, home, log, read_json, write_private
from .service import Service

MAX_BODY = 64 * 1024
TICKET_SECONDS = 60
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
CSP = (
    "default-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


def window_file(profile: str) -> Path:
    return home() / f"{profile}.window.json"


def proof(key: str, *parts: str) -> str:
    return hmac.new(key.encode(), "\0".join(parts).encode(), hashlib.sha256).hexdigest()


class Window:
    """The HTTP server of one running companion."""

    def __init__(self, service: Service):
        self.service = service
        self.key = secrets.token_urlsafe(32)
        self.tickets: dict[str, float] = {}
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        write_private(
            window_file(self.service.profile_name), json.dumps({"pid": os.getpid(), "port": self.port, "key": self.key})
        )
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="window").start()

    def stop(self) -> None:
        self.httpd.shutdown()
        path = window_file(self.service.profile_name)
        if read_json(path).get("key") == self.key:
            path.unlink(missing_ok=True)

    def ticket(self) -> str:
        ticket = secrets.token_urlsafe(24)
        with self.lock:
            now = time.monotonic()
            self.tickets = {t: until for t, until in self.tickets.items() if until > now}
            self.tickets[ticket] = now + TICKET_SECONDS
        return ticket

    def redeem(self, ticket: object) -> bool:
        if not isinstance(ticket, str):
            return False
        with self.lock:
            until = self.tickets.pop(ticket, 0.0)
        return until > time.monotonic()

    def open(self) -> None:
        open_window(f"{self.origin}/?ticket={self.ticket()}", self.service.profile_name)


def _handler(window: Window):
    service = window.service

    class Handler(BaseHTTPRequestHandler):
        server_version = "synapse-companion"
        sys_version = ""

        def log_message(self, *args):  # requests are not worth a log line
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: dict) -> None:
            self._send(status, json.dumps(value).encode(), "application/json")

        def _host_ok(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{window.port}"

        def _key_ok(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Companion-Key", ""), window.key)

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "Open the companion at 127.0.0.1."})
            path = self.path.split("?", 1)[0]
            if path in STATIC:
                name, ctype = STATIC[path]
                data = (resources.files(__package__) / "static" / name).read_bytes()
                return self._send(200, data, ctype)
            if path == "/api/state":
                if not self._key_ok():
                    return self._json(403, {"error": "Wrong key."})
                return self._json(200, service.state())
            return self._json(404, {"error": "Not found."})

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"error": "Open the companion at 127.0.0.1."})
            origin = self.headers.get("Origin")
            if origin not in (None, window.origin) or self.headers.get("Content-Type") != "application/json":
                return self._json(403, {"error": "Not from this window."})
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = -1
            if not 0 <= size <= MAX_BODY:
                return self._json(413, {"error": "Too large."})
            try:
                data = json.loads(self.rfile.read(size) or b"{}")
            except ValueError:
                data = None
            if not isinstance(data, dict):
                return self._json(400, {"error": "Send a JSON object."})
            path = self.path
            if path == "/api/session":
                if not window.redeem(data.get("ticket")):
                    return self._json(403, {"error": "This ticket has expired."})
                return self._json(200, {"key": window.key})
            if path == "/api/ticket":  # another start of the program asks this one to open a window
                nonce = data.get("nonce")
                if not (
                    isinstance(nonce, str)
                    and 16 <= len(nonce) <= 128
                    and secrets.compare_digest(str(data.get("proof", "")), proof(window.key, "ask", nonce))
                ):
                    return self._json(403, {"error": "Wrong key."})
                ticket = window.ticket()
                return self._json(200, {"ticket": ticket, "proof": proof(window.key, "answer", nonce, ticket)})
            if not self._key_ok():
                return self._json(403, {"error": "Wrong key."})
            try:
                return self._json(200, self.action(path, data) or {"ok": True})
            except Fail as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                log(f"window action failed: {type(e).__name__}: {e}")
                return self._json(500, {"error": "That did not work; see Activity."})

        def action(self, path: str, data: dict) -> dict | None:
            text = lambda k: data[k] if isinstance(data.get(k), str) else ""  # noqa: E731
            if path == "/api/connect":
                service.connect(text("server"), text("token"), text("name"))
            elif path == "/api/disconnect":
                service.disconnect()
            elif path == "/api/settings":
                service.set_settings(data.get("settings"))
            elif path == "/api/extension":
                action = data.get("action")
                if action not in ("install", "repair", "apply"):
                    raise Fail("Unknown action.")
                service.install_extension(approve=action == "apply")
            elif path == "/api/autostart":
                service.set_autostart(data.get("on") is True)
            elif path == "/api/update":
                threading.Thread(target=self._later, args=(service.update_program,), daemon=True).start()
            elif path == "/api/quit":
                threading.Thread(target=self._later, args=(service.quit,), daemon=True).start()
            else:
                raise Fail("Unknown request.")
            return None

        @staticmethod
        def _later(fn) -> None:
            time.sleep(0.3)  # let the answer reach the window first
            try:
                fn()
            except Fail as e:
                log(str(e))

    return Handler


# ---------------------------------------------------------------------- reaching a running companion


def running_ticket(profile: str, *, timeout: float = 3) -> dict | None:
    """A window ticket from the companion already running for this profile, ``{ticket, port}``;
    None if none runs. The key itself is never sent, and the answer must prove the key too."""
    info = read_json(window_file(profile))
    port, key = info.get("port"), info.get("key")
    if not isinstance(port, int) or not isinstance(key, str):
        return None
    nonce = secrets.token_hex(16)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/ticket",
        data=json.dumps({"nonce": nonce, "proof": proof(key, "ask", nonce)}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            answer = json.loads(resp.read(65536))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(answer, dict) or not isinstance(answer.get("ticket"), str):
        return None
    if not secrets.compare_digest(str(answer.get("proof", "")), proof(key, "answer", nonce, answer["ticket"])):
        return None  # not our companion on that port any more
    return {"ticket": answer["ticket"], "port": port}


# ---------------------------------------------------------------------- the window itself

CHROMIUM = {
    "linux": ["google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge", "vivaldi"],
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "win32": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ],
}


def _chromium() -> str | None:
    for name in CHROMIUM.get(sys.platform if sys.platform in ("darwin", "win32") else "linux", []):
        path = os.path.expandvars(name)
        found = path if os.path.isabs(path) and os.path.exists(path) else shutil.which(path)
        if found:
            return found
    return None


def open_window(url: str, profile: str) -> None:
    """An app window (no tabs, no address bar) of a Chromium browser, else the default browser.
    The browser gets a private file that redirects to ``url``, so the ticket in it is in no
    command line."""
    if os.environ.get("SYNAPSE_COMPANION_NO_WINDOW"):  # tests and headless use
        log(f"Window: {url.split('?')[0]}")
        return
    redirect = home() / f"{profile}.open.html"
    write_private(
        redirect,
        f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={url}">'
        "<title>Synapse Companion</title>",
    )
    url = redirect.as_uri()
    browser = _chromium()
    if browser:
        try:
            subprocess.Popen(
                [browser, f"--app={url}", "--window-size=700,820"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=sys.platform != "win32",
            )
            return
        except OSError:
            pass
    webbrowser.open(url)
