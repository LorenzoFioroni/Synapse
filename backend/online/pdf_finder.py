"""Find an open-access PDF for an item and verify it is the right paper."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from ..library.identifiers import clean_doi
from ..pdf.text import extract_text
from .http import BROWSER_UA, TIMEOUT, arxiv_wait

log = logging.getLogger(__name__)

FALLBACK_EMAIL = "synapse@example.com"


def _ratio(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz

        return fuzz.partial_ratio(a, b)
    except ImportError:
        from difflib import SequenceMatcher

        if len(a) > len(b):
            a, b = b, a
        best = 0.0
        step = max(1, len(a) // 4)
        for i in range(0, max(1, len(b) - len(a) + 1), step):
            best = max(best, SequenceMatcher(None, a, b[i : i + len(a)]).ratio())
        return best * 100


def verify_pdf(path: str, title: str | None, doi: str | None, arxiv_id: str | None = None) -> bool:
    text, _ = extract_text(path, max_pages=2)
    low = text.lower()
    if doi and re.sub(r"[^a-z0-9]", "", doi.lower()) in re.sub(r"[^a-z0-9]", "", low):
        return True
    # arXiv stamps "arXiv:1412.6980v9 [cs.LG] …" in the margin of the first page
    if arxiv_id and re.search(r"arxiv:\s*" + re.escape(arxiv_id.lower()) + r"(?:v\d+)?\b", low):
        return True
    if title:
        t = re.sub(r"\s+", "", title.lower())[:150]
        return _ratio(t, re.sub(r"\s+", "", low)) >= 85
    return False


def arxiv_pdf_url(arxiv_id: str) -> str:
    """arxiv.org/pdf/<id> (latest version unless the id pins one); old ids like quant-ph/0001106 too."""
    return f"https://arxiv.org/pdf/{arxiv_id}"


def candidate_urls(
    title: str | None, doi: str | None, email: str, arxiv_id: str | None = None, title_search: bool = True
) -> list[str]:
    """Where to look, best first. With a known arXiv id its PDF comes first for a preprint; for a
    published paper the publisher's open-access copy (Unpaywall) comes first and arXiv second.
    ``title_search=False`` skips the searches by title (Unpaywall, arXiv)."""
    out: list[str | None] = []  # _unique drops the empty ones
    email = urllib.parse.quote(email or FALLBACK_EMAIL, safe="@")  # a "+" would read as a space
    preprint = bool(arxiv_id) and (not doi or doi.lower().startswith("10.48550/"))
    if arxiv_id and preprint:
        out.append(arxiv_pdf_url(arxiv_id))
    if doi and not doi.lower().startswith("10.48550/"):
        try:
            r = requests.get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={email}", timeout=10)
            if r.ok:
                loc = r.json().get("best_oa_location") or {}
                out.append(loc.get("url_for_pdf"))
        except Exception as e:
            log.info("Unpaywall DOI lookup failed: %s", e)
    if arxiv_id and not preprint:
        out.append(arxiv_pdf_url(arxiv_id))
    if title and title_search:
        try:
            r = requests.get(
                f"https://api.unpaywall.org/v2/search?query={urllib.parse.quote(title)}&email={email}", timeout=10
            )
            if r.ok:
                for res in r.json().get("results", [])[:2]:
                    loc = (res.get("response") or {}).get("best_oa_location") or {}
                    out.append(loc.get("url_for_pdf"))
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
                        if link.attrib.get("title") == "pdf":
                            out.append(link.attrib.get("href"))
        except Exception as e:
            log.info("arXiv lookup failed: %s", e)
    return _unique(out)


def _unique(urls: list) -> list[str]:
    seen, uniq = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def find_and_download(
    title: str | None, doi: str | None, pdf_dir: Path, email: str = "", arxiv_id: str | None = None
) -> dict:
    """Returns {"status": "success", "filename": ...} or {"status": "not_found", "message": ...}."""
    doi = clean_doi(doi)
    if arxiv_id:
        # The arXiv copy is known: try it (and Unpaywall first for a published paper); the
        # searches by title only run if none of those is the paper.
        first = candidate_urls(title, doi, email, arxiv_id, title_search=False)
        later = lambda: [u for u in candidate_urls(title, None, email) if u not in first]  # noqa: E731
    else:
        first, later = candidate_urls(title, doi, email), list
    found = _download_first(first, title, doi, arxiv_id, pdf_dir)
    rest = [] if found else later()
    found = found or _download_first(rest, title, doi, arxiv_id, pdf_dir)
    if found:
        return found
    if not first and not rest:
        return {"status": "not_found", "message": "No open-access copy was found online."}
    return {
        "status": "not_found",
        "message": "Candidates were found, but none passed verification (wrong paper or a login page).",
    }


def _download_first(urls: list[str], title, doi, arxiv_id, pdf_dir) -> dict | None:
    """Download the candidates in order; the first that is the right paper is kept."""
    for url in urls:
        try:
            if urllib.parse.urlparse(url).hostname in ("arxiv.org", "export.arxiv.org"):
                arxiv_wait()  # batch Find PDF: keep arXiv requests apart
            r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=20)
            ctype = r.headers.get("Content-Type", "application/pdf")
            if not (r.ok and ("pdf" in ctype or r.content[:4] == b"%PDF")):
                continue
            fd, tmp = tempfile.mkstemp(suffix=".pdf")
            with os.fdopen(fd, "wb") as fh:
                fh.write(r.content)
            if verify_pdf(tmp, title, doi, arxiv_id):
                filename = f"{uuid.uuid4().hex}_downloaded.pdf"
                shutil.move(tmp, Path(pdf_dir) / filename)
                return {"status": "success", "filename": filename, "source": url}
            os.remove(tmp)
        except Exception as e:
            log.info("Download failed for %s: %s", url, e)
    return None
