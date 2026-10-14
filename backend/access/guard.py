"""Who may use the server: other websites never, other devices only once paired (#17, #34).

The server has no accounts. Three checks decide whether a request goes ahead:

* **Host** — every request must name the server by a loopback name (``127.0.0.1``,
  ``localhost``, ``[::1]``), a host the user allowed with ``--allow-host``, or — when the server
  listens on the network (``--lan``) — an IP address or this computer's ``.local`` name. A
  DNS-rebinding page (``evil.example`` resolving to 127.0.0.1) sends its own name, so it can
  neither change nor read anything.
* **Device** — a request that comes over the network from another device (judged by the
  connection's address, never by a header) needs the cookie of a paired device; see
  ``devices.py``. Requests from this computer itself — loopback or one of its own LAN
  addresses — need none, as before. Without the cookie, pages answer with "pair this device"
  and the API with 401.
* **Origin** — a request that changes something (POST, PUT, PATCH, DELETE) must come from the
  app's own pages. On this computer a browser extension (the "Save to library" button) and
  clients that send no Origin at all (curl, scripts, the test client) are also allowed; from
  another device only the app's own pages are (``Origin``, or ``Sec-Fetch-Site: same-origin``).

Reading (GET) needs no Origin check: the browser does not let other origins read the answers,
and the Host check stops rebinding.
"""
from __future__ import annotations

from flask import Flask, Response, current_app, g, jsonify, request

from . import lan
from .devices import COOKIE, COOKIE_MAX_AGE

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
EXTENSION_SCHEMES = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

# What an unpaired device may open: the pairing page and the call it makes, plus the icon.
PAIRING_PATHS = {"/pair", "/api/devices/pair", "/favicon.ico", "/assets/favicon.svg"}


def host_name(host: str) -> str:
    """``127.0.0.1:8000`` → ``127.0.0.1``; ``[::1]:8000`` → ``::1``; lower case."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host[1:]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def allowed_origin(origin: str, host: str, extensions: bool = True) -> bool:
    if origin.startswith(EXTENSION_SCHEMES):
        return extensions
    return origin.lower() in {f"http://{host.lower()}", f"https://{host.lower()}"}


def host_allowed(host: str, allowed_hosts: set[str], lan_mode: bool = False) -> bool:
    name = host_name(host)
    if name in LOOPBACK | allowed_hosts:
        return True
    if lan_mode:
        # An IP address can't be a rebinding attack (the page's own origin would be that address);
        # the .local name is this computer's own.
        own = lan.local_hostname()
        return lan.is_ip_literal(name) or (own is not None and name == own.lower())
    return False


def refusal(host: str, method: str, origin: str | None, fetch_site: str | None,
            allowed_hosts: set[str], *, local: bool = True, lan_mode: bool = False) -> str | None:
    """Why this request is refused (Host and Origin checks), or ``None`` when it may go ahead.
    The device check is separate (``install``)."""
    if not host_allowed(host, allowed_hosts, lan_mode):
        return f"Requests for host '{host}' are not accepted. Open the app at http://127.0.0.1."
    if method not in UNSAFE:
        return None
    if origin is not None:
        if allowed_origin(origin, host, extensions=local):
            return None
        return "Changes can only be made from the app itself."
    if local:
        if fetch_site in ("cross-site", "same-site"):
            return "Changes can only be made from the app itself."
        return None
    # another device: only the app's own pages (a browser that sends no Origin still says same-origin)
    if fetch_site == "same-origin":
        return None
    return "Changes can only be made from the app itself."


NOT_PAIRED_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Synapse — device not paired</title><link rel="icon" href="/assets/favicon.svg">
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0;
         display: grid; place-items: center; min-height: 100vh; background: Canvas; color: CanvasText; }
  main { max-width: 30em; padding: 24px; }
  h1 { font-size: 22px; margin: 0 0 12px; }
  ol { padding-left: 1.2em; } li { margin: 6px 0; }
  .muted { opacity: .7; font-size: 14px; }
</style></head>
<body><main>
  <h1>This device isn't paired with Synapse</h1>
  <p>To use your library here:</p>
  <ol>
    <li>On the computer running Synapse, open <b>Settings → Devices</b>.</li>
    <li>Click <b>Add a Device</b>.</li>
    <li>Scan the QR code with this device's camera, or open the link it shows.</li>
  </ol>
  <p class="muted">If this device was paired before, it was signed out or its pairing was removed.</p>
</main></body></html>
"""


def not_paired() -> Response:
    if request.path.startswith("/api/"):
        resp = jsonify(error="This device is not paired with Synapse. Pair it in Settings → Devices on the computer "
                             "running Synapse.", paired=False)
        resp.status_code = 401
        return resp
    return Response(NOT_PAIRED_PAGE, status=401, mimetype="text/html",
                    headers={"Cache-Control": "no-store"})


def set_device_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax",
                    secure=request.is_secure, path="/")


def install(app: Flask, allowed_hosts: set[str] | None = None, lan_mode: bool = False) -> None:
    hosts = {host_name(h) for h in (allowed_hosts or set()) if h}

    @app.before_request
    def _guard():
        local = lan.is_this_computer(request.remote_addr)
        g.this_computer = local
        g.device = None
        why = refusal(request.host, request.method, request.headers.get("Origin"),
                      request.headers.get("Sec-Fetch-Site"), hosts, local=local, lan_mode=lan_mode)
        if why:
            return jsonify(error=why), 403
        if local:
            return None
        devices = current_app.extensions["zclone"].devices
        device = devices.check(request.cookies.get(COOKIE), request.remote_addr)
        if device is None:
            if request.path in PAIRING_PATHS:
                return None
            return not_paired()
        g.device = device
        return None

    @app.after_request
    def _refresh_cookie(resp: Response):
        # Keep a device's cookie from running out while the device is in use.
        dev = g.get("device")
        if dev and dev.get("_touched") and not any(c.startswith(COOKIE + "=") for c in resp.headers.getlist("Set-Cookie")):
            set_device_cookie(resp, request.cookies.get(COOKIE, ""))
        return resp
