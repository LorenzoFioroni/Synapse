"""Refuse requests that other websites make to the local server (issue #17).

The server listens on the loopback address and has no login, so the browser is the only
thing standing between a web page in another tab and the library. Two checks close that gap:

* **Host** — every request must name the server by a loopback name (``127.0.0.1``,
  ``localhost``, ``[::1]``) or a host the user allowed with ``--allow-host``. A DNS-rebinding
  page (``evil.example`` resolving to 127.0.0.1) sends its own name, so it can neither change
  nor read anything.
* **Origin** — a request that changes something (POST, PUT, PATCH, DELETE) must come from the
  app's own pages or from a browser extension (the "Save to library" button). Browsers send
  ``Origin`` on such requests; when it is missing, ``Sec-Fetch-Site`` is used instead. Clients
  that send neither (curl, scripts, the test client) are not browsers and are allowed.

Reading (GET) needs no Origin check: the browser does not let other origins read the answers,
and the Host check stops rebinding.
"""
from __future__ import annotations

from flask import Flask, jsonify, request

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
EXTENSION_SCHEMES = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def host_name(host: str) -> str:
    """``127.0.0.1:8000`` → ``127.0.0.1``; ``[::1]:8000`` → ``::1``; lower case."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host[1:]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def allowed_origin(origin: str, host: str) -> bool:
    if origin.startswith(EXTENSION_SCHEMES):
        return True
    return origin.lower() in {f"http://{host.lower()}", f"https://{host.lower()}"}


def refusal(host: str, method: str, origin: str | None, fetch_site: str | None,
            allowed_hosts: set[str]) -> str | None:
    """Why this request is refused, or ``None`` when it may go ahead."""
    if host_name(host) not in LOOPBACK | allowed_hosts:
        return f"Requests for host '{host}' are not accepted. Open the app at http://127.0.0.1."
    if method not in UNSAFE:
        return None
    if origin is not None:
        if allowed_origin(origin, host):
            return None
        return "Changes can only be made from the app itself."
    if fetch_site in ("cross-site", "same-site"):
        return "Changes can only be made from the app itself."
    return None


def install(app: Flask, allowed_hosts: set[str] | None = None) -> None:
    hosts = {host_name(h) for h in (allowed_hosts or set()) if h}

    @app.before_request
    def _guard():
        why = refusal(request.host, request.method, request.headers.get("Origin"),
                      request.headers.get("Sec-Fetch-Site"), hosts)
        if why:
            return jsonify(error=why), 403
        return None
