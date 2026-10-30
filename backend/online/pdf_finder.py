"""Download an open-access PDF of an item and verify it is the right paper. Where to look comes
from the plugins' PDF sources (``@plugin.pdf_source``; Unpaywall and arXiv are the plugin
plugins/open-access-pdfs, on by default)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import urllib.parse
import uuid
from collections.abc import Callable
from pathlib import Path

from ..library.identifiers import clean_doi
from ..pdf.text import extract_text
from .http import BROWSER_UA, arxiv_wait
from .remote_pdf import fetch_pdf

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


def _unique(urls: list) -> list[str]:
    seen, uniq = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


NO_SOURCES = "No PDF sources are on: turn on Open-Access PDFs in Settings → Plugins."


def find_and_download(
    title: str | None,
    doi: str | None,
    pdf_dir: Path,
    email: str = "",
    arxiv_id: str | None = None,
    candidates: Callable[[bool], list[str]] | None = None,
) -> dict:
    """Returns {"status": "success", "filename": ...} or {"status": "not_found", "message": ...}.
    ``candidates(by_title)`` gives the URLs to try (the plugins' PDF sources). ``email`` is kept
    for callers that pass it; the sources read the contact address themselves."""
    doi = clean_doi(doi)
    if candidates is None:
        return {"status": "not_found", "message": NO_SOURCES}
    if arxiv_id:
        # The arXiv copy is known: try the sources that go by identifier first; the searches by
        # title only run if none of those is the paper.
        first = candidates(False)
        later = lambda: [u for u in candidates(True) if u not in first]  # noqa: E731
    else:
        first, later = _unique(candidates(False) + candidates(True)), list
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
            content = fetch_pdf(url, BROWSER_UA)
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
