"""What every lookup on the web shares: the timeout, how we introduce ourselves, the error type,
and the spacing of requests (arXiv's, which plugins' requests share)."""

from __future__ import annotations

import threading
import time

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
        """Take the next free turn for ``key`` and sleep until it; the one after waits ``gap`` more."""
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next.get(key, 0.0))
            self._next[key] = at + gap
        if at > now:
            time.sleep(at - now)


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
