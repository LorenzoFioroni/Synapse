"""Where the publisher's PDF of a DOI may be.

1. The full-text links the publisher deposited with Crossref, the PDF ones (Springer, Wiley, IOP,
   ACS, AIP, Nature, …). Those meant only for text-mining APIs that need a key are left out.
2. The DOI's own page (``https://doi.org/<doi>``): Synapse follows it to the article page and on
   to the PDF the page names in its ``citation_pdf_url`` tag (APS, Elsevier, Optica, …).

The Crossref request goes through ``plugin.http`` and fails quietly: without it the DOI page is
still worth a try. Synapse downloads the candidates itself and checks each is the paper.
"""

from __future__ import annotations

import logging
import urllib.parse

from backend.plugins import HttpError

log = logging.getLogger(__name__)

CROSSREF = "https://api.crossref.org/works/"
TIMEOUT = 10  # seconds
# Text-mining APIs: they answer only with a key, never with a reader's PDF.
API_HOSTS = ("api.elsevier.com", "api.wiley.com")


def candidates(http, doi: str | None, email: str | None) -> list[str]:
    """The publisher's PDF links for ``doi``, then its DOI page; nothing for an arXiv DOI (that
    copy is arXiv's) or without a DOI."""
    if not doi or doi.lower().startswith("10.48550/"):
        return []
    quoted = urllib.parse.quote(doi)
    return crossref_pdf_links(http, quoted, email) + [f"https://doi.org/{quoted}"]


def crossref_pdf_links(http, quoted_doi: str, email: str | None) -> list[str]:
    params = {"mailto": email} if email else {}
    try:
        message = http.get(CROSSREF + quoted_doi, params=params, timeout=TIMEOUT).json().get("message") or {}
    except (HttpError, ValueError, AttributeError) as e:
        log.info("Crossref lookup failed: %s", e)
        return []
    out: list[str] = []
    for link in message.get("link") or []:
        url = link.get("URL") or ""
        if (link.get("content-type") or "").lower() != "application/pdf":
            continue
        if link.get("content-version") not in (None, "vor"):
            continue  # an accepted manuscript: the open-access sources find those
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not url.startswith(("https://", "http://")) or host in API_HOSTS:
            continue
        out.append(reader_pdf(url, host))
    return list(dict.fromkeys(out))


def reader_pdf(url: str, host: str) -> str:
    """Wiley's /doi/pdf/ is a viewer page around the PDF; /doi/pdfdirect/ is the file."""
    if host == "wiley.com" or host.endswith(".wiley.com"):
        return url.replace("/doi/pdf/", "/doi/pdfdirect/", 1)
    return url
