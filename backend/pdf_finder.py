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

from .pdftext import extract_text

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
FALLBACK_EMAIL = "zotero-clone@example.com"


def clean_doi(doi: str | None) -> str:
    doi = (doi or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    return re.sub(r"^doi:\s*", "", doi, flags=re.I).strip()


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
            best = max(best, SequenceMatcher(None, a, b[i:i + len(a)]).ratio())
        return best * 100


def verify_pdf(path: str, title: str | None, doi: str | None) -> bool:
    text, _ = extract_text(path, max_pages=2)
    low = text.lower()
    if doi and re.sub(r"[^a-z0-9]", "", doi.lower()) in re.sub(r"[^a-z0-9]", "", low):
        return True
    if title:
        t = re.sub(r"\s+", "", title.lower())[:150]
        return _ratio(t, re.sub(r"\s+", "", low)) >= 85
    return False


def candidate_urls(title: str | None, doi: str | None, email: str) -> list[str]:
    out: list[str] = []
    email = email or FALLBACK_EMAIL
    if doi:
        try:
            r = requests.get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={email}", timeout=10)
            if r.ok:
                loc = r.json().get("best_oa_location") or {}
                out.append(loc.get("url_for_pdf"))
        except Exception as e:
            log.info("Unpaywall DOI lookup failed: %s", e)
    if title:
        try:
            r = requests.get(f"https://api.unpaywall.org/v2/search?query={urllib.parse.quote(title)}&email={email}",
                             timeout=10)
            if r.ok:
                for res in r.json().get("results", [])[:2]:
                    loc = (res.get("response") or {}).get("best_oa_location") or {}
                    out.append(loc.get("url_for_pdf"))
        except Exception as e:
            log.info("Unpaywall search failed: %s", e)
        try:
            gs = f"https://scholar.google.com/scholar?q=%22{urllib.parse.quote(title)}%22"
            r = requests.get(gs, headers={"User-Agent": UA}, timeout=10)
            if r.ok:
                m = re.search(r'<div class="gs_r gs_or gs_scl".*?(?=<div class="gs_r gs_or gs_scl"|id="gs_bdy_ft"|$)',
                              r.text, re.DOTALL)
                if m:
                    pm = re.search(r'<div class="gs_or_ggsm"[^>]*>.*?<a[^>]+href="([^"]+)"', m.group(0), re.DOTALL)
                    if pm:
                        u = pm.group(1)
                        out.append("https://scholar.google.com" + u if u.startswith("/") else u)
        except Exception as e:
            log.info("Scholar lookup failed: %s", e)
        try:
            r = requests.get("http://export.arxiv.org/api/query?search_query=ti:%22"
                             f"{urllib.parse.quote(title)}%22&max_results=1", timeout=10)
            if r.ok:
                ns = "{http://www.w3.org/2005/Atom}"
                for entry in ET.fromstring(r.content).findall(f"{ns}entry"):
                    for link in entry.findall(f"{ns}link"):
                        if link.attrib.get("title") == "pdf":
                            out.append(link.attrib.get("href"))
        except Exception as e:
            log.info("arXiv lookup failed: %s", e)
    seen, uniq = set(), []
    for u in out:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def find_and_download(title: str | None, doi: str | None, pdf_dir: Path, email: str = "") -> dict:
    """Returns {"status": "success", "filename": ...} or {"status": "not_found", "message": ...}."""
    doi = clean_doi(doi)
    urls = candidate_urls(title, doi, email)
    if not urls:
        return {"status": "not_found", "message": "No open-access copy was found online."}
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            ctype = r.headers.get("Content-Type", "application/pdf")
            if not (r.ok and ("pdf" in ctype or r.content[:4] == b"%PDF")):
                continue
            fd, tmp = tempfile.mkstemp(suffix=".pdf")
            with os.fdopen(fd, "wb") as fh:
                fh.write(r.content)
            if verify_pdf(tmp, title, doi):
                filename = f"{uuid.uuid4().hex}_downloaded.pdf"
                shutil.move(tmp, Path(pdf_dir) / filename)
                return {"status": "success", "filename": filename, "source": url}
            os.remove(tmp)
        except Exception as e:
            log.info("Download failed for %s: %s", url, e)
    return {"status": "not_found",
            "message": "Candidates were found, but none passed verification (wrong paper or a login page)."}

