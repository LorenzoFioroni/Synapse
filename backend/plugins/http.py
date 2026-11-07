"""``plugin.http``: how plugins reach the web. Only declared public hosts or explicitly
permitted loopback addresses (with the services the deployment names, ``--local-service``), spaced
requests per host (shared by every plugin and Synapse, so together they cannot flood one service),
a polite User-Agent, the user's keys for the scholarly services Synapse itself uses, answers cached
by ETag / Last-Modified, and readable errors."""

from __future__ import annotations

import ipaddress
import json as jsonlib
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import requests

from ..online import http as online_http
from .errors import PluginError

TIMEOUT = 20  # seconds
MAX_BYTES = 50 * 1024 * 1024
MAX_REDIRECTS = 5
DEFAULT_GAP = 0.5  # seconds between two requests to one host (arXiv asks for more: online/http.py)
CACHE_ENTRIES = 200


class HttpError(PluginError):
    """The request failed: an error status, a refused redirect, an answer too large."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Offline(HttpError):
    """The service could not be reached (no network, the host is down, a timeout)."""


@dataclass
class Response:
    status: int
    url: str
    headers: dict[str, str]
    content: bytes
    from_cache: bool = False
    encoding: str = "utf-8"

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    def json(self):
        return jsonlib.loads(self.content)


def _gap_for(host: str) -> tuple[str, float]:
    """(the line ``host`` waits in, its gap), shared with Synapse's own requests."""
    return online_http.spacing(host, DEFAULT_GAP)


def allowed(host: str, patterns: list[str]) -> bool:
    """``host`` is one the manifest names: exactly, or under a ``*.example.org`` pattern."""
    host = host.lower().rstrip(".")
    for p in patterns:
        if p.startswith("*."):
            if host == p[2:] or host.endswith(p[1:]):
                return True
        elif host == p:
            return True
    return False


def loopback(host: str) -> bool:
    """Only localhost and literal loopback IPs, never arbitrary names or LAN addresses."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """(scheme, host, port) with the scheme's default port filled in; None for no http(s) address."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    return scheme, parts.hostname.lower(), port if port is not None else (443 if scheme == "https" else 80)


def service_credentials(host: str, settings) -> tuple[dict, dict]:
    """(query parameters, headers) for a request to one of the scholarly services Synapse itself
    uses, from Settings → Online Services: OpenAlex's key and polite-pool address, Semantic
    Scholar's key. A plugin asking those services then gets the user's quota without a key of its
    own, as Synapse's own lookups do."""
    if settings is None:
        return {}, {}
    if host == "api.openalex.org":
        params = {}
        if settings.get("openalex_api_key"):
            params["api_key"] = settings.get("openalex_api_key")
        if settings.get("contact_email"):
            params["mailto"] = settings.get("contact_email")
        return params, {}
    if host == "api.semanticscholar.org" and settings.get("semantic_scholar_api_key"):
        return {}, {"x-api-key": settings.get("semantic_scholar_api_key")}
    return {}, {}


@dataclass
class _Cached:
    etag: str | None
    modified: str | None
    response: Response


@dataclass
class PluginHttp:
    """What ``plugin.http`` is. ``user_agent()`` and ``credentials(host)`` (the services' keys, see
    ``service_credentials``) are asked at each request: Settings may change. ``private`` lets tests
    reach a server on this computer. ``services`` are the deployment's local services (name →
    origin, ``Config.local_services``): with the "loopback" permission their exact origins count as
    this computer."""

    pid: str
    hosts: list[str]
    user_agent: Callable[[], str]
    private: bool = False
    credentials: Callable[[str], tuple[dict, dict]] | None = None
    services: dict[str, str] = field(default_factory=dict)
    _gaps: dict[str, float] = field(default_factory=dict)
    _cache: OrderedDict = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def limit(self, host: str, seconds: float) -> None:
        """Ask ``host`` at most once every ``seconds`` (more than the default, never less)."""
        self._gaps[host.lower()] = max(float(seconds), 0.0)

    def local_service(self, name: str) -> str | None:
        """The address of a service the deployment runs next to Synapse (``--local-service
        ollama=http://ollama:11434`` → ``"http://ollama:11434"`` for ``"ollama"``), or None."""
        return self.services.get(name.lower())

    def _is_service(self, url: str) -> bool:
        origin = _origin(url)
        return origin is not None and any(origin == _origin(s) for s in self.services.values())

    def get(self, url: str, params: dict | None = None, **kw) -> Response:
        return self.request("GET", url, params=params, **kw)

    def post(self, url: str, data=None, json=None, **kw) -> Response:
        return self.request("POST", url, data=data, json=json, **kw)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        data=None,
        json=None,
        headers: dict | None = None,
        timeout: float | tuple[float, float] = TIMEOUT,
        max_bytes: int = MAX_BYTES,
        cache: bool = True,
        raise_for_status: bool = True,
    ) -> Response:
        method = method.upper()
        first = (urlsplit(url).hostname or "").lower()
        cred_params, cred_headers = self.credentials(first) if self.credentials else ({}, {})
        if cred_params:
            params = {**cred_params, **(params or {})}  # the plugin's own values win
        if params:
            url = requests.Request("GET", url, params=params).prepare().url or url
        hdrs = {"User-Agent": self.user_agent(), **cred_headers, **(headers or {})}
        cached = self._cache.get(url) if method == "GET" and cache else None
        if cached:
            if cached.etag:
                hdrs.setdefault("If-None-Match", cached.etag)
            if cached.modified:
                hdrs.setdefault("If-Modified-Since", cached.modified)
        for _ in range(MAX_REDIRECTS + 1):
            host = self._check(url)
            local = (loopback(host) or self._is_service(url)) and ("loopback" in self.hosts or self.private)
            if local and host == "localhost":
                # Use a literal address: DNS cannot redirect note text off this computer.
                parts = urlsplit(url)
                url = parts._replace(netloc="127.0.0.1" + (f":{parts.port}" if parts.port else "")).geturl()
            if host != first:  # redirected elsewhere: the service's key stays with the service
                for k in cred_headers:
                    if k not in (headers or {}):
                        hdrs.pop(k, None)
            key, gap = _gap_for(host)
            online_http.HOST_GAPS.wait(key, max(0.0 if local else gap, self._gaps.get(host, 0.0)))
            try:
                # Local model requests must not pass through environment HTTP proxies.
                if local:
                    send = _local_request
                elif self.private:
                    send = requests.request
                else:
                    send = online_http.public_request
                r = send(
                    method,
                    url,
                    data=data,
                    json=json,
                    headers=hdrs,
                    timeout=timeout,
                    stream=True,
                    allow_redirects=False,
                )
            except online_http.PublicAddressError as e:
                if e.status == 502:
                    raise Offline(str(e)) from e
                raise HttpError(f"{host}: {e}", e.status) from e
            except (requests.ConnectionError, requests.Timeout) as e:
                raise Offline(f"{host} could not be reached: {type(e).__name__}") from e
            except requests.RequestException as e:
                # Not str(e): it can quote the URL, and its query can hold an API key.
                raise HttpError(f"{host}: {type(e).__name__}") from e
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                r.close()
                if r.status_code == 303:
                    method, data, json = "GET", None, None
                continue
            if r.status_code == 304 and cached:
                r.close()
                return Response(
                    200, url, cached.response.headers, cached.response.content, True, cached.response.encoding
                )
            content = _read(r, max_bytes, host)
            resp = Response(r.status_code, url, dict(r.headers), content, False, r.encoding or "utf-8")
            if raise_for_status and r.status_code >= 400:
                raise HttpError(f"{host} answered {r.status_code} {r.reason or ''}".strip(), r.status_code)
            if method == "GET" and cache and r.status_code == 200:
                etag, modified = r.headers.get("ETag"), r.headers.get("Last-Modified")
                if etag or modified:
                    with self._lock:
                        self._cache[url] = _Cached(etag, modified, resp)
                        self._cache.move_to_end(url)
                        while len(self._cache) > CACHE_ENTRIES:
                            self._cache.popitem(last=False)
            return resp
        raise HttpError(f"Too many redirects from {urlsplit(url).hostname}")

    def _check(self, url: str) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or not host:
            raise HttpError(f"Only http(s) addresses can be fetched: {url}")
        local = loopback(host) or self._is_service(url)
        if local and "loopback" in self.hosts:
            if parts.username or parts.password:
                raise HttpError("Loopback addresses cannot contain credentials")
            return host
        if not allowed(host, [name for name in self.hosts if name != "loopback"]):
            raise PluginError(
                f'{host} is not in the plugin\'s "network" permission'
                + (f" ({', '.join(self.hosts)})" if self.hosts else " (it names no host)")
            )
        return host


def _local_request(*args, **kwargs) -> requests.Response:
    with requests.Session() as session:
        session.trust_env = False
        return session.request(*args, **kwargs)


def _read(r: requests.Response, max_bytes: int, host: str) -> bytes:
    chunks, size = [], 0
    try:
        for chunk in r.iter_content(64 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise HttpError(f"The answer from {host} is larger than {max_bytes // (1024 * 1024)} MB")
            chunks.append(chunk)
    except requests.RequestException as e:
        raise Offline(f"{host}: the answer was cut off ({type(e).__name__})") from e
    finally:
        r.close()
    return b"".join(chunks)
