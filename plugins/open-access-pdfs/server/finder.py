"""Where an open-access copy of a paper may be: Unpaywall and arXiv. Requests fail quietly (a
service that is down must not stop the others): a URL list may simply come back shorter."""

from __future__ import annotations

import logging
import urllib.parse
import xml.etree.ElementTree as ET

import requests

from backend.online.http import TIMEOUT, arxiv_wait
from backend.online.pdf_finder import FALLBACK_EMAIL, arxiv_pdf_url

log = logging.getLogger(__name__)


def _email(email: str | None) -> str:
    return urllib.parse.quote(email or FALLBACK_EMAIL, safe="@")  # a "+" would read as a space


def by_identifier(doi: str | None, arxiv_id: str | None, email: str | None) -> list[str]:
    """Best first: for a preprint (an arXiv id and no DOI, or only arXiv's) its arXiv PDF; for a
    published paper the publisher's open-access copy (Unpaywall) and then arXiv."""
    out: list[str] = []
    preprint = bool(arxiv_id) and (not doi or doi.lower().startswith("10.48550/"))
    if arxiv_id and preprint:
        out.append(arxiv_pdf_url(arxiv_id))
    if doi and not doi.lower().startswith("10.48550/"):
        try:
            r = requests.get(
                f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={_email(email)}", timeout=10
            )
            if r.ok:
                loc = r.json().get("best_oa_location") or {}
                if loc.get("url_for_pdf"):
                    out.append(loc["url_for_pdf"])
        except Exception as e:
            log.info("Unpaywall DOI lookup failed: %s", e)
    if arxiv_id and not preprint:
        out.append(arxiv_pdf_url(arxiv_id))
    return out


def by_title(title: str | None, email: str | None) -> list[str]:
    """Unpaywall's search (its first two results) and arXiv's (its first)."""
    if not title:
        return []
    out: list[str] = []
    try:
        r = requests.get(
            f"https://api.unpaywall.org/v2/search?query={urllib.parse.quote(title)}&email={_email(email)}", timeout=10
        )
        if r.ok:
            for res in r.json().get("results", [])[:2]:
                loc = (res.get("response") or {}).get("best_oa_location") or {}
                if loc.get("url_for_pdf"):
                    out.append(loc["url_for_pdf"])
    except Exception as e:
        log.info("Unpaywall search failed: %s", e)
    try:
        arxiv_wait()
        r = requests.get(
            f"https://export.arxiv.org/api/query?search_query=ti:%22{urllib.parse.quote(title)}%22&max_results=1",
            timeout=TIMEOUT,
        )
        if r.ok:
            ns = "{http://www.w3.org/2005/Atom}"
            for entry in ET.fromstring(r.content).findall(f"{ns}entry"):
                for link in entry.findall(f"{ns}link"):
                    if link.attrib.get("title") == "pdf" and link.attrib.get("href"):
                        out.append(link.attrib["href"])
    except Exception as e:
        log.info("arXiv lookup failed: %s", e)
    return out
