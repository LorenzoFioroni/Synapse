"""Synapse behind a tunnel or reverse proxy (``python server.py --public-url https://name``).

A tunnel such as Cloudflare Tunnel runs a program on this computer that receives the requests of the
whole internet and passes them on to ``http://127.0.0.1:<port>``. Judged by the connection alone,
every one of them would come from this computer, which needs no sign-in and may make pairing codes
and access tokens (``guard.py``). So a request counts as *proxied* — another device, whatever
address it connects from — when

* it names the public host (the tunnel passes the visitor's ``Host`` on unchanged, and nobody can
  make it send a loopback name), or
* it carries a header that proxies add (``X-Forwarded-For``, ``Cf-Connecting-Ip`` …): browsers and
  apps on this computer don't send them, and wrongly counting a request as proxied only asks for a
  sign-in.

Requests for the public host are also treated as HTTPS: the visitor's browser speaks HTTPS to the
tunnel even though the tunnel speaks plain HTTP to Synapse. Passkeys and secure cookies need that.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from .lan import host_name, is_this_computer, parse_ip

# Headers a proxy adds; any of them means the request did not start on this computer.
FORWARD_HEADERS = (
    "HTTP_CF_CONNECTING_IP",
    "HTTP_CF_RAY",
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_FORWARDED_HOST",
    "HTTP_X_REAL_IP",
    "HTTP_FORWARDED",
)
PROXIED = "synapse.proxied"  # WSGI environ keys this module sets
CLIENT_ADDR = "synapse.client_addr"


class PublicUrlError(ValueError):
    pass


def parse_public_url(url: str) -> str:
    """``https://synapse.example.com`` → its normalized form. Only an HTTPS origin: no path, query or
    port other than 443 (a tunnel serves the standard port)."""
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() != "https":
        raise PublicUrlError("--public-url must start with https:// (the tunnel or proxy serves HTTPS).")
    if not parts.hostname:
        raise PublicUrlError("--public-url needs a host name, like https://synapse.example.com.")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.password:
        raise PublicUrlError("--public-url is the address alone, like https://synapse.example.com (no path).")
    if parts.port not in (None, 443):
        raise PublicUrlError("--public-url must use the standard HTTPS port (leave the port out).")
    if parse_ip(parts.hostname) is not None:
        raise PublicUrlError("--public-url needs a host name, not an IP address (passkeys need a name).")
    return f"https://{parts.hostname.lower()}"


def public_host(public_url: str | None) -> str | None:
    """The host name of a ``--public-url`` (already parsed), or None."""
    return urlsplit(public_url).hostname if public_url else None


def _client(environ: dict) -> str:
    """The visitor's address as the proxy reports it, if the request came from a proxy on this
    computer; else the connection's address. Only for display (devices' "last used from")."""
    addr = environ.get("REMOTE_ADDR") or ""
    if not is_this_computer(addr):
        return addr  # a proxy elsewhere could put anything in its headers
    for key in ("HTTP_CF_CONNECTING_IP", "HTTP_X_REAL_IP"):
        value = (environ.get(key) or "").strip()
        if parse_ip(value) is not None:
            return value
    forwarded = (environ.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded if parse_ip(forwarded) is not None else addr


class ProxyMiddleware:
    """Marks proxied requests (``environ["synapse.proxied"]``), records the visitor's address
    (``environ["synapse.client_addr"]``) and makes requests for the public host HTTPS."""

    def __init__(self, app, public_url: str | None = None):
        self.app = app
        self.host = public_host(public_url)

    def __call__(self, environ, start_response):
        for_public = self.host is not None and host_name(environ.get("HTTP_HOST", "")) == self.host
        proxied = for_public or any(environ.get(h) for h in FORWARD_HEADERS)
        environ[PROXIED] = proxied
        environ[CLIENT_ADDR] = _client(environ) if proxied else (environ.get("REMOTE_ADDR") or "")
        if for_public:
            environ["wsgi.url_scheme"] = "https"
        return self.app(environ, start_response)


def is_proxied(environ: dict) -> bool:
    return bool(environ.get(PROXIED))


def client_addr(environ: dict) -> str:
    return environ.get(CLIENT_ADDR) or environ.get("REMOTE_ADDR") or ""
