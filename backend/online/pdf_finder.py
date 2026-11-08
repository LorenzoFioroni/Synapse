"""Download a PDF of an item and verify it is the right paper. Where to look comes from the
plugins' PDF sources (``@plugin.pdf_source``): the publisher's own copy is plugins/publisher-pdfs,
Unpaywall and arXiv plugins/open-access-pdfs, both on by default. A candidate may be an article's
web page: its PDF link is followed (``article_page``)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import unicodedata
import urllib.parse
import uuid
from collections.abc import Callable
from pathlib import Path

from requests.cookies import RequestsCookieJar

from ..library.identifiers import ARXIV_ID, clean_doi, norm_doi
from ..pdf.text import DOI_RE, clean_text, extract_text
from . import article_page
from .http import BROWSER_UA, arxiv_wait
from .remote_pdf import RemoteError, fetch, is_pdf

log = logging.getLogger(__name__)


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


# Publishers sometimes prepend a cover or access page. Keep extraction bounded rather than
# reading the reference list at the end of an arbitrarily long document.
VERIFY_MAX_PAGES = 5


def _title_text(value: str) -> str:
    text = clean_text(value).casefold().replace("\u00ad", "")
    # Typography and line wrapping must not make an otherwise identical title fail.
    return re.sub(r"[\W_]+", "", text)


def pdf_verification(path: str, title: str | None, doi: str | None, arxiv_id: str | None = None) -> dict:
    """Verified, contradictory identity, or indeterminate; never attach indeterminate PDFs."""
    text, pages = extract_text(path, max_pages=2)
    result = _verify_text(text, title, doi, arxiv_id)
    # A later reference must not override contradictory identity on the opening pages.
    if result["status"] == "unverified" and pages > 2:
        text, _ = extract_text(path, max_pages=VERIFY_MAX_PAGES)
        return _verify_text(text, title, doi, arxiv_id)
    return result


def _verify_text(text: str, title: str | None, doi: str | None, arxiv_id: str | None) -> dict:
    low = unicodedata.normalize("NFKC", text).casefold().replace("\u00ad", "")
    found_dois = {norm_doi(m.group(1).rstrip(".,;)]}")) for m in DOI_RE.finditer(low)}
    expected_doi = norm_doi(doi)
    if expected_doi:
        # PDF layout may wrap a DOI anywhere, including after a meaningful hyphen. Preserve
        # punctuation and require a complete identifier, not a prefix of another paper's DOI.
        pattern = r"(?<![a-z0-9])" + r"\s*".join(re.escape(c) for c in expected_doi)
        end = r"(?=$|[\s\"'<>]|[.,;:)\]}]+(?=\s|$))"
        if re.search(pattern + end, low):
            return {"status": "verified"}
    # arXiv stamps include the version, but the library stores the unversioned identifier.
    found_arxiv = {
        m.group(1).lower() for m in re.finditer(r"arxiv:\s*" + ARXIV_ID.pattern + r"(?![a-z0-9])", low, re.I)
    }
    if arxiv_id and arxiv_id.lower() in found_arxiv:
        return {"status": "verified"}
    if title:
        expected_title = _title_text(title)[:150]
        if expected_title and _ratio(expected_title, _title_text(low)) >= 85:
            return {"status": "verified"}
    if not low.strip():
        return {
            "status": "unverified",
            "reason": "No readable text could be extracted. This may be a scanned or damaged PDF; review it manually.",
        }
    if (expected_doi and len(found_dois) == 1) or (arxiv_id and len(found_arxiv) == 1):
        return {
            "status": "wrong-paper",
            "reason": "The PDF contains a different paper identifier and its title did not match the library item.",
        }
    return {
        "status": "unverified",
        "reason": "The readable PDF text did not establish this item's title or identifier. Review the PDF manually.",
    }


def verify_pdf(path: str, title: str | None, doi: str | None, arxiv_id: str | None = None) -> bool:
    """Compatibility helper for PDF sources: only positively verified PDFs may be kept."""
    return pdf_verification(path, title, doi, arxiv_id)["status"] == "verified"


def _unique(urls: list) -> list[str]:
    seen, uniq = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


NO_SOURCES = "No PDF sources are on: turn on Publisher PDFs or Open-Access PDFs in Settings → Plugins."


def find_and_download(
    title: str | None,
    doi: str | None,
    pdf_dir: Path,
    email: str = "",
    arxiv_id: str | None = None,
    candidates: Callable[[bool], list[str]] | None = None,
) -> dict:
    """Returns {"status": "success", "filename": ...} or {"status": "not_found", "message": ...}.
    ``candidates(by_title)`` gives the URLs to try (the plugins' PDF sources): those by
    identifier, then those searching by title, asked only when needed. ``email`` is kept
    for callers that pass it; the sources read the contact address themselves."""
    doi = clean_doi(doi)
    if candidates is None:
        return {"status": "not_found", "message": NO_SOURCES}
    # The sources that go by DOI or arXiv id come first; the searches by title only run if none
    # of those is the paper (a title search finds other papers too, and costs requests).
    first = _unique(candidates(False))
    found = _download_first(first, title, doi, arxiv_id, pdf_dir)
    rest = [] if found else [u for u in _unique(candidates(True)) if u not in first]
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
            content = _fetch_candidate(url)
            fd, tmp = tempfile.mkstemp(suffix=".pdf")
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            if verify_pdf(tmp, title, doi, arxiv_id):
                filename = f"{uuid.uuid4().hex}_downloaded.pdf"
                shutil.move(tmp, Path(pdf_dir) / filename)
                return {"status": "success", "filename": filename, "source": url}
            os.remove(tmp)
        except Exception as e:
            log.info("Download failed for %s: %s", url, e)
    return None


# A DOI's page can take a refresh page (Elsevier) and the article page before the PDF.
PAGE_HOPS = 3


def _fetch_candidate(url: str) -> bytes:
    """The PDF at ``url``; when it is an article's page, the PDF the page names. The cookies the
    sites set travel along: publishers check them between the page and the PDF. Behind a
    subscription the PDF link leads to a login page instead, which ends the attempt."""
    cookies = RequestsCookieJar()
    seen = {url}
    for _ in range(PAGE_HOPS + 1):
        if urllib.parse.urlparse(url).hostname in ("arxiv.org", "export.arxiv.org"):
            arxiv_wait()  # batch Find PDF: keep arXiv requests apart
        final, data = fetch(url, BROWSER_UA, cookies=cookies)
        if is_pdf(data):
            return data
        pdf, refresh = article_page.links(data, final)
        url = next((u for u in (pdf, refresh) if u and u not in seen), "")
        if not url:
            break
        seen.add(url)
    raise RemoteError("The link does not lead to a PDF.", 415)
