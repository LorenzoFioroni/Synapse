"""Who may use the server: other websites never, other devices only once paired.

The server has no accounts. Three checks decide whether a request goes ahead:

* **Host** — every request must name the server by a loopback name (``127.0.0.1``,
  ``localhost``, ``[::1]``), a host the user allowed with ``--allow-host``, or — when the server
  listens on the network (``--lan``) — an IP address or this computer's ``.local`` name. A
  DNS-rebinding page (``evil.example`` resolving to 127.0.0.1) sends its own name, so it can
  neither change nor read anything.
* **Device** — a request that comes over the network from another device (judged by the
  connection's address, never by a header) needs the cookie of a device that was paired or
  signed in with a passkey; see ``devices.py`` and ``passkeys.py``. Requests from this computer
  itself — loopback or one of its own LAN addresses — need none, unless they came through a tunnel
  or proxy (``proxy.py``): those count as another device, whatever address they connect from. Without the cookie, pages answer
  with the sign-in page (``frontend/signin.html``) and the API with 401.
* **Origin** — a request that changes something (POST, PUT, PATCH, DELETE) must come from the
  app's own pages. On this computer clients that send no Origin at all (curl, scripts, the test
  client) are also allowed. Browser extensions never are: the "Save to Library" extension reaches
  the server only through the companion (see below), so no installed extension can change the
  library. From another device only the app's own pages are allowed (``Origin``, or
  ``Sec-Fetch-Site: same-origin``).

Reading (GET) needs no Origin check: the browser does not let other origins read the answers,
and the Host check stops rebinding.

The MCP endpoint (``/mcp``) is for AI apps, not browsers: it takes neither cookies nor the Origin
rules above. Every request to it, from this computer too, needs an access token
(``Authorization: Bearer …``, see ``tokens.py``), and a browser's request with a foreign Origin is
refused. The AI tool a companion runs gets no stored token but a token for its one question
(``Companions.job_caller``), which works on ``/mcp`` only, never offers the tools that change the
library, and stops working when the question ends.

The companion endpoints (``/api/companion/``) and the connector endpoints (``/api/connector/``,
which the companion forwards for the browser extension) are for the companion program on the
user's own computer, not browsers: a request with a token needs a companion-scoped one, and any
``Origin`` is refused. On the connector endpoints, requests from this computer without a token
and without an Origin (scripts, the tests) follow the usual rules.
"""

from __future__ import annotations

import re

from flask import Flask, Response, current_app, g, jsonify, request

from . import lan
from .devices import COOKIE, COOKIE_MAX_AGE
from .lan import host_name
from .proxy import client_addr, is_proxied, public_host
from .tokens import bearer

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CONNECTOR_PREFIX = "/api/connector/"  # the extension's endpoints, forwarded by the companion
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

# What an unpaired device may open: the pairing page and the calls it and the sign-in page make,
# plus their two standalone scripts, the icons and the web app manifest. Browsers fetch the
# manifest without cookies, and iOS fetches the Home Screen icon the same way, so a paired iPad
# or phone would get the sign-in page
# instead and draw a plain tile. They say nothing about the library.
PAIRING_PATHS = {
    "/pair",
    "/js/access/pair.js",
    "/js/access/signin.js",
    "/api/devices/pair",
    "/api/passkeys/login/options",
    "/api/passkeys/login",
    "/favicon.ico",
    "/assets/favicon.svg",
    "/assets/favicon-32.png",
    "/assets/apple-touch-icon.png",
    "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png",
    "/assets/icon-192.png",
    "/assets/icon-512.png",
    "/manifest.webmanifest",
}
MCP_PATH = "/mcp"  # AI apps, with an access token instead of a device cookie
COMPANION_PREFIX = "/api/companion/"  # the companion program, with a companion token


def allowed_origin(origin: str, host: str) -> bool:
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


def refusal(
    host: str,
    method: str,
    origin: str | None,
    fetch_site: str | None,
    allowed_hosts: set[str],
    *,
    local: bool = True,
    lan_mode: bool = False,
) -> str | None:
    """Why this request is refused (Host and Origin checks), or ``None`` when it may go ahead.
    The device check is separate (``install``)."""
    if not host_allowed(host, allowed_hosts, lan_mode):
        return f"Requests for host '{host}' are not accepted. Open the app at http://127.0.0.1."
    if method not in UNSAFE:
        return None
    if origin is not None:
        if allowed_origin(origin, host):
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


# Shown when frontend/signin.html cannot be read (it always can in a normal checkout).
NOT_PAIRED_FALLBACK = """<!doctype html><meta charset="utf-8"><title>Synapse</title>
<h1>This device isn't paired with Synapse</h1>
<p>On the computer running Synapse, open Settings → Devices and click Add a Device.</p>"""


def _signin_page() -> str:
    path = current_app.extensions["synapse"].config.frontend_dir / "signin.html"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return NOT_PAIRED_FALLBACK


def not_paired() -> Response:
    if request.path.startswith("/api/"):
        resp = jsonify(
            error="This device is not paired with Synapse. Pair it in Settings → Devices on the computer "
            "running Synapse.",
            paired=False,
        )
        resp.status_code = 401
        return resp
    return Response(_signin_page(), status=401, mimetype="text/html", headers={"Cache-Control": "no-store"})


_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{1,128}")  # what secrets.token_urlsafe produces


def set_device_cookie(resp: Response, token: str) -> None:
    """Give the device its cookie. Tokens are URL-safe by construction; anything else is refused
    rather than written into a header."""
    if not _TOKEN_SHAPE.fullmatch(token):
        raise ValueError("Not a device token.")
    resp.set_cookie(
        COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax", secure=request.is_secure, path="/"
    )


def install(
    app: Flask, allowed_hosts: set[str] | None = None, lan_mode: bool = False, public_url: str | None = None
) -> None:
    hosts = {host_name(h) for h in (allowed_hosts or set()) if h}
    public = public_host(public_url)
    if public:
        hosts.add(public)

    @app.before_request
    def _guard():
        addr = client_addr(request.environ)
        local = lan.is_this_computer(request.remote_addr) and not is_proxied(request.environ)
        g.this_computer = local
        g.device = None
        if request.path == MCP_PATH:
            if not host_allowed(request.host, hosts, lan_mode):
                return jsonify(error=f"Requests for host '{request.host}' are not accepted."), 403
            return _ai_app()
        if request.path.startswith(COMPANION_PREFIX):
            if not host_allowed(request.host, hosts, lan_mode):
                return jsonify(error=f"Requests for host '{request.host}' are not accepted."), 403
            return _companion()
        if request.path.startswith(CONNECTOR_PREFIX) and request.headers.get("Authorization"):
            if not host_allowed(request.host, hosts, lan_mode):
                return jsonify(error=f"Requests for host '{request.host}' are not accepted."), 403
            return _companion()
        why = refusal(
            request.host,
            request.method,
            request.headers.get("Origin"),
            request.headers.get("Sec-Fetch-Site"),
            hosts,
            local=local,
            lan_mode=lan_mode,
        )
        if why:
            return jsonify(error=why), 403
        if local:
            return None
        devices = current_app.extensions["synapse"].devices
        device = devices.check(request.cookies.get(COOKIE), addr)
        if device is None:
            if request.path in PAIRING_PATHS:
                return None
            return not_paired()
        g.device = device
        return None

    def _ai_app():
        origin = request.headers.get("Origin")
        if origin is not None and not allowed_origin(origin, request.host):
            return jsonify(error="The MCP endpoint does not answer other websites."), 403
        services = current_app.extensions["synapse"]
        token = bearer(request.headers.get("Authorization"))
        entry = services.tokens.check(token, client_addr(request.environ), scope="mcp")
        caller = services.companions.job_caller(token) if entry is None else None
        if caller and any(t["id"] == caller for t in services.tokens.list()):
            # a companion's AI tool, for one question, while the companion's own token still exists
            entry = {"id": caller, "scope": "job"}
        if entry is None:
            resp = jsonify(
                error="This needs an access token. Make one in Settings → AI Access on the computer running "
                "Synapse and send it as  Authorization: Bearer <token>."
            )
            resp.status_code = 401
            resp.headers["WWW-Authenticate"] = 'Bearer realm="Synapse"'
            return resp
        g.access_token = entry
        return None

    def _companion():
        if request.headers.get("Origin") is not None:
            return jsonify(error="These endpoints answer the companion program, not browsers."), 403
        tokens = current_app.extensions["synapse"].tokens
        entry = tokens.check(
            bearer(request.headers.get("Authorization")), client_addr(request.environ), scope="companion"
        )
        if entry is None:
            resp = jsonify(
                error="This needs a companion token. Make one in Settings → AI Access on the computer running "
                "Synapse (New Companion Token)."
            )
            resp.status_code = 401
            resp.headers["WWW-Authenticate"] = 'Bearer realm="Synapse companion"'
            return resp
        g.access_token = entry
        return None

    @app.after_request
    def _content_security_policy(resp: Response):
        # PDF decoders compile WebAssembly; JavaScript eval and inline scripts stay forbidden.
        resp.headers["Content-Security-Policy"] = (
            "script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        )
        # A file is only ever what its declared type says: no guessing a page or script from bytes.
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.after_request
    def _refresh_cookie(resp: Response):
        # Keep a device's cookie from running out while the device is in use.
        dev = g.get("device")
        if (
            dev
            and dev.get("_touched")
            and not any(c.startswith(COOKIE + "=") for c in resp.headers.getlist("Set-Cookie"))
        ):
            set_device_cookie(resp, request.cookies.get(COOKIE, ""))
        return resp
