"""``plugin.http``: how plugins reach the web. Only the hosts a plugin's manifest names, only public
addresses, spaced requests per host (shared by every plugin, so two plugins cannot flood one
service), a polite User-Agent, answers cached by ETag / Last-Modified, and readable errors."""

from __future__ import annotations

import ipaddress
import json as jsonlib
import socket
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import requests

from .errors import PluginError

TIMEOUT = 20  # seconds
MAX_BYTES = 50 * 1024 * 1024
MAX_REDIRECTS = 5
DEFAULT_GAP = 0.5  # seconds between two requests to one host
# Services that ask for more room (arXiv's API terms: one request every three seconds).
GAPS = {"arxiv.org": 3.0}
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


class _Gaps:
    """When each host may next be asked (process-wide)."""

    def __init__(self):
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, gap: float) -> None:
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next.get(host, 0.0))
            self._next[host] = at + gap
        if at > now:
            time.sleep(at - now)


GAPS_STATE = _Gaps()


def _gap_for(host: str) -> float:
    for suffix, gap in GAPS.items():
        if host == suffix or host.endswith("." + suffix):
            return gap
    return DEFAULT_GAP


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


def public(host: str) -> bool:
    """Every address ``host`` resolves to is a public one (not this computer, not the local network)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as e:
        raise Offline(f"Cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped:
            ip = mapped
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


@dataclass
class _Cached:
    etag: str | None
    modified: str | None
    response: Response


@dataclass
class PluginHttp:
    """What ``plugin.http`` is. ``user_agent()`` is asked at each request (the contact address in
    Settings may change). ``private`` lets tests reach a server on this computer."""

    pid: str
    hosts: list[str]
    user_agent: Callable[[], str]
    private: bool = False
    _gaps: dict[str, float] = field(default_factory=dict)
    _cache: OrderedDict = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def limit(self, host: str, seconds: float) -> None:
        """Ask ``host`` at most once every ``seconds`` (more than the default, never less)."""
        self._gaps[host.lower()] = max(float(seconds), 0.0)

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
        timeout: float = TIMEOUT,
        max_bytes: int = MAX_BYTES,
        cache: bool = True,
        raise_for_status: bool = True,
    ) -> Response:
        method = method.upper()
        if params:
            url = requests.Request("GET", url, params=params).prepare().url or url
        hdrs = {"User-Agent": self.user_agent(), **(headers or {})}
        cached = self._cache.get(url) if method == "GET" and cache else None
        if cached:
            if cached.etag:
                hdrs.setdefault("If-None-Match", cached.etag)
            if cached.modified:
                hdrs.setdefault("If-Modified-Since", cached.modified)
        for _ in range(MAX_REDIRECTS + 1):
            host = self._check(url)
            GAPS_STATE.wait(host, max(_gap_for(host), self._gaps.get(host, 0.0)))
            try:
                r = requests.request(
                    method,
                    url,
                    data=data,
                    json=json,
                    headers=hdrs,
                    timeout=timeout,
                    stream=True,
                    allow_redirects=False,
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                raise Offline(f"{host} could not be reached: {type(e).__name__}") from e
            except requests.RequestException as e:
                raise HttpError(f"{host}: {e}") from e
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
        if not allowed(host, self.hosts):
            raise PluginError(
                f'{host} is not in the plugin\'s "network" permission'
                + (f" ({', '.join(self.hosts)})" if self.hosts else " (it names no host)")
            )
        if not self.private and not public(host):
            raise HttpError(f"{host} is on this computer or the local network; plugins may only reach the web")
        return host


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
