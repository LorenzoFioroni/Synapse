"""What every lookup on the web shares: the timeout, how we introduce ourselves, the error type,
the spacing of requests, and the public-web transport shared by PDF links and plugins."""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util import Timeout

TIMEOUT = 15  # seconds, for API requests

# Browsers' User-Agent: some publishers only serve PDFs to what looks like a browser.
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"


class LookupFailed(Exception):
    """A lookup failed; the message is shown to the user as is."""


def api_headers(email: str) -> dict:
    """Headers for scholarly APIs (Crossref, OpenAlex …): they ask clients to name themselves and
    give a contact address, and answer such "polite" requests faster."""
    ua = "Synapse/2.0 (https://github.com/LorenzoFioroni/Synapse; mailto:%s)" % (email or "synapse@example.com")
    return {"User-Agent": ua}


# Lookups of several items run side by side, and plugins ask the same services: arXiv's API terms
# ask for one request every three seconds, from Synapse as a whole.
ARXIV_SPACING = 3.0


class HostGaps:
    """When each service may next be asked, for the whole process: Synapse's own requests and every
    plugin's (``plugin.http``) wait in the same line, so together they keep a service's spacing."""

    def __init__(self):
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str, gap: float) -> None:
        """Wait for a turn, spacing it from the previous caller's actual release time."""
        while True:
            with self._lock:
                now = time.monotonic()
                delay = self._next.get(key, 0.0) - now
                if delay <= 0:
                    self._next[key] = now + gap
                    return
            # Sleeping can finish late, or another caller can take the turn first.
            # Recheck without holding the lock so other services remain independent.
            time.sleep(delay)


HOST_GAPS = HostGaps()


def spacing(host: str, default: float) -> tuple[str, float]:
    """(the line ``host`` waits in, the gap after each request): all of arXiv's hosts (its API,
    PDFs, HTML, listings) share one line; any other host has its own, spaced by ``default``."""
    host = host.lower().rstrip(".")
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        return "arxiv.org", ARXIV_SPACING
    return host, default


def arxiv_wait() -> None:
    """Space requests to arXiv (API and PDF downloads alike) by ARXIV_SPACING seconds."""
    HOST_GAPS.wait("arxiv.org", ARXIV_SPACING)


class PublicAddressError(ValueError):
    """A URL or its DNS answers cannot safely be used for a public-web request."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class _Address:
    family: int
    socktype: int
    protocol: int
    sockaddr: tuple


def resolve_public_url(url: str) -> tuple[_Address, ...]:
    """Resolve once, retaining the socket addresses only if every answer is public."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise PublicAddressError("Invalid web address.", 400) from e
    if parts.scheme not in ("http", "https") or not host:
        raise PublicAddressError("Only http(s) addresses can be fetched.", 400)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise PublicAddressError(f"Cannot resolve {host}.", 502) from e
    addresses = []
    for family, socktype, protocol, _, sockaddr in infos:
        ip = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped:
            ip = mapped
        if family not in (socket.AF_INET, socket.AF_INET6) or not ip.is_global or ip.is_multicast:
            raise PublicAddressError("Links to this computer or the local network are not opened.")
        addresses.append(_Address(family, socktype, protocol, sockaddr))
    if not addresses:
        raise PublicAddressError(f"Cannot resolve {host}.", 502)
    return tuple(addresses)


def _connect(connection: HTTPConnection, addresses: tuple[_Address, ...]) -> socket.socket:
    last_error: OSError | None = None
    for address in addresses:
        sock = None
        try:
            sock = socket.socket(address.family, address.socktype, address.protocol)
            sock.settimeout(Timeout.resolve_default_timeout(connection.timeout))
            for option in connection.socket_options or ():
                sock.setsockopt(*option)
            if connection.source_address:
                sock.bind(connection.source_address)
            # Never hand the hostname back to a resolver between validation and connect.
            sock.connect(address.sockaddr)
            return sock
        except OSError as e:
            if sock is not None:
                sock.close()
            last_error = e
    if isinstance(last_error, socket.timeout):
        raise ConnectTimeoutError(connection, f"Connection to {connection.host} timed out") from last_error
    raise NewConnectionError(connection, f"Could not connect to {connection.host}: {last_error}") from last_error


def _pinned_adapter(addresses: tuple[_Address, ...]) -> HTTPAdapter:
    # Each request owns these pools: pins cannot leak between hosts, redirects or threads.
    class HTTP(HTTPConnection):
        def _new_conn(self) -> socket.socket:
            return _connect(self, addresses)

    class HTTPS(HTTPSConnection):
        def _new_conn(self) -> socket.socket:
            return _connect(self, addresses)

    class HTTPPool(HTTPConnectionPool):
        ConnectionCls = HTTP

    class HTTPSPool(HTTPSConnectionPool):
        ConnectionCls = HTTPS

    adapter = HTTPAdapter()
    adapter.poolmanager.pool_classes_by_scheme = {"http": HTTPPool, "https": HTTPSPool}
    return adapter


def public_request(method: str, url: str, **kwargs) -> requests.Response:
    """One public-web hop, connected to validated DNS answers with the original TLS identity.

    Redirects belong to the caller, which must check its permissions again for each new hop.
    Like requests.request, the response may be streamed and must be closed by the caller.
    """
    # Validate requests' normalized URL, including IDNA and percent-encoded hostnames.
    url = requests.Request(method, url).prepare().url or url
    addresses = resolve_public_url(url)
    with requests.Session() as session:
        # A proxy could resolve the hostname again or route to an unchecked destination.
        session.trust_env = False
        adapter = _pinned_adapter(addresses)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        kwargs["allow_redirects"] = False
        return session.request(method, url, **kwargs)
