"""Where an open-access copy of a paper may be: Unpaywall and arXiv. Requests go through
``plugin.http`` (the hosts in the manifest, arXiv's spacing shared with Synapse) and fail quietly:
a service that is down must not stop the others, so a URL list may simply come back shorter."""

from __future__ import annotations

import logging
import urllib.parse
import xml.etree.ElementTree as ET

from backend.plugins import HttpError

log = logging.getLogger(__name__)

UNPAYWALL = "https://api.unpaywall.org/v2/"
ARXIV_API = "https://export.arxiv.org/api/query"
TIMEOUT = 15  # seconds
# Unpaywall answers only requests carrying an address; this one when Settings has none.
FALLBACK_EMAIL = "synapse@example.com"


def arxiv_pdf_url(arxiv_id: str) -> str:
    """arxiv.org/pdf/<id> (latest version unless the id pins one); old ids like quant-ph/0001106 too."""
    return f"https://arxiv.org/pdf/{arxiv_id}"


def _get_json(http, url: str, params: dict, what: str):
    try:
        return http.get(url, params=params, timeout=10).json()
    except (HttpError, ValueError) as e:
        log.info("%s failed: %s", what, e)
        return None


def by_identifier(http, doi: str | None, arxiv_id: str | None, email: str | None) -> list[str]:
    """Best first: for a preprint (an arXiv id and no DOI, or only arXiv's) its arXiv PDF; for a
    published paper the publisher's open-access copy (Unpaywall) and then arXiv."""
    out: list[str] = []
    preprint = bool(arxiv_id) and (not doi or doi.lower().startswith("10.48550/"))
    if arxiv_id and preprint:
        out.append(arxiv_pdf_url(arxiv_id))
    if doi and not doi.lower().startswith("10.48550/"):
        # the DOI is part of the path: its "/" stays, anything else unsafe (a "?", a "#") is quoted
        url = UNPAYWALL + urllib.parse.quote(doi)
        found = _get_json(http, url, {"email": email or FALLBACK_EMAIL}, "Unpaywall DOI lookup")
        loc = (found or {}).get("best_oa_location") or {}
        if loc.get("url_for_pdf"):
            out.append(loc["url_for_pdf"])
    if arxiv_id and not preprint:
        out.append(arxiv_pdf_url(arxiv_id))
    return out


def by_title(http, title: str | None, email: str | None) -> list[str]:
    """Unpaywall's search (its first two results) and arXiv's (its first)."""
    if not title:
        return []
    out: list[str] = []
    found = _get_json(
        http, UNPAYWALL + "search", {"query": title, "email": email or FALLBACK_EMAIL}, "Unpaywall search"
    )
    for res in (found or {}).get("results", [])[:2]:
        loc = (res.get("response") or {}).get("best_oa_location") or {}
        if loc.get("url_for_pdf"):
            out.append(loc["url_for_pdf"])
    try:
        r = http.get(ARXIV_API, params={"search_query": f'ti:"{title}"', "max_results": 1}, timeout=TIMEOUT)
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in ET.fromstring(r.content).findall(f"{ns}entry"):
            for link in entry.findall(f"{ns}link"):
                if link.attrib.get("title") == "pdf" and link.attrib.get("href"):
                    out.append(link.attrib["href"])
    except (HttpError, ET.ParseError) as e:
        log.info("arXiv lookup failed: %s", e)
    return out
