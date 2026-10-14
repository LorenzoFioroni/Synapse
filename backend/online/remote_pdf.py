"""Download the PDF of an attachment that points to a web address (issue #21).

Only the URL stored on an attachment is ever fetched, never one supplied by the browser, and
only from public addresses: a link to the local network (a router page, ``169.254.169.254``,
another service on this machine) is refused, also when a redirect leads there. The response
must be a PDF and is capped in size.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

MAX_BYTES = 100 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = 30


class RemoteError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _public(host: str) -> bool:
    """True when every address the host resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        raise RemoteError(f"Cannot resolve {host}.", 502)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


def check_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise RemoteError("Only http(s) links can be opened.")
    if not _public(p.hostname):
        raise RemoteError("Links to this computer or the local network are not opened.", 403)


def fetch_pdf(url: str, user_agent: str, max_bytes: int | None = None) -> bytes:
    """The PDF at ``url``, following up to five redirects; :class:`RemoteError` otherwise."""
    max_bytes = max_bytes or MAX_BYTES
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        try:
            r = requests.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False,
                             headers={"User-Agent": user_agent})
        except requests.RequestException:
            raise RemoteError("The PDF could not be downloaded.", 502)
        with r:
            if r.is_redirect:
                url = urljoin(url, r.headers.get("Location", ""))
                continue
            if not r.ok:
                raise RemoteError(f"The site answered {r.status_code}.", 502)
            declared = r.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise RemoteError("The file is too large.", 413)
            data = bytearray()
            for chunk in r.iter_content(64 * 1024):
                data += chunk
                if len(data) > max_bytes:
                    raise RemoteError("The file is too large.", 413)
        if b"%PDF-" not in bytes(data[:1024]):   # the header may follow a little junk
            raise RemoteError("The link does not lead to a PDF.", 415)
        return bytes(data)
    raise RemoteError("Too many redirects.", 502)
