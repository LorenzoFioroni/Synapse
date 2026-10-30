"""Download a PDF from a web address: an attachment stored as a link, or the PDF link of a page
saved with the browser button (``backend/connector.py``).

The server fetches these URLs on someone's behalf, so it must not become a way into the local
network: only public addresses are fetched, and a link to the local network (a router page,
``169.254.169.254``, another service on this machine) is refused, also when a redirect leads
there. The response must be a PDF and is capped in size.
"""

from __future__ import annotations

from urllib.parse import urljoin

import requests

from . import http

MAX_BYTES = 100 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = 30


class RemoteError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def fetch_pdf(url: str, user_agent: str, max_bytes: int | None = None) -> bytes:
    """The PDF at ``url``, following up to five redirects; :class:`RemoteError` otherwise."""
    max_bytes = max_bytes or MAX_BYTES
    for _ in range(MAX_REDIRECTS + 1):
        try:
            r = http.public_request(
                "GET", url, timeout=TIMEOUT, stream=True, allow_redirects=False, headers={"User-Agent": user_agent}
            )
        except http.PublicAddressError as e:
            raise RemoteError(str(e), e.status) from e
        except requests.RequestException as e:
            raise RemoteError("The PDF could not be downloaded.", 502) from e
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
        if b"%PDF-" not in bytes(data[:1024]):  # the header may follow a little junk
            raise RemoteError("The link does not lead to a PDF.", 415)
        return bytes(data)
    raise RemoteError("Too many redirects.", 502)
