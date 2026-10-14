"""What every lookup on the web shares: the timeout, how we introduce ourselves, the error type,
and the spacing of requests to arXiv."""
from __future__ import annotations

import threading
import time

TIMEOUT = 15   # seconds, for API requests

# Browsers' User-Agent: some publishers only serve PDFs to what looks like a browser.
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"


class LookupFailed(Exception):
    """A lookup failed; the message is shown to the user as is."""


def api_headers(email: str) -> dict:
    """Headers for scholarly APIs (Crossref, OpenAlex …): they ask clients to name themselves and
    give a contact address, and answer such "polite" requests faster."""
    ua = "Synapse/2.0 (https://github.com/LorenzoFioroni/Synapse; mailto:%s)" % (email or "synapse@example.com")
    return {"User-Agent": ua}


# Lookups of several items run side by side; arXiv asks API users to space their requests.
ARXIV_SPACING = 1.0
_arxiv_lock = threading.Lock()
_arxiv_last = 0.0


def arxiv_wait() -> None:
    """Space requests to arXiv (API and PDF downloads alike) by ARXIV_SPACING seconds."""
    global _arxiv_last
    with _arxiv_lock:
        wait = _arxiv_last + ARXIV_SPACING - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _arxiv_last = time.monotonic()
