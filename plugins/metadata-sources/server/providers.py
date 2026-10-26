"""Crossref, OpenAlex, arXiv and Open Library metadata providers."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

from backend.formats.common import join_authors, parse_display_name, strip_tags
from backend.library.identifiers import ARXIV_ID, clean_doi
from backend.online.http import TIMEOUT, LookupFailed, api_headers, arxiv_wait
from backend.online.metadata import TITLE_MATCH, title_similarity

log = logging.getLogger(__name__)


def _four_digits(s: str | None) -> str:
    """The first run of four digits in ``s`` (Open Library's dates are free text), or ''."""
    m = re.search(r"\d{4}", s or "")
    return m.group(0) if m else ""


# ------------------------------------------------------------- providers
def _crossref_to_item(m: dict) -> dict:
    def first(key):
        v = m.get(key)
        return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else "")

    year = ""
    for k in ("published-print", "published-online", "issued", "created"):
        dp = (m.get(k) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            year = str(dp[0][0])
            break
    ctype = m.get("type", "")
    itype = {
        "journal-article": "journalArticle",
        "book": "book",
        "monograph": "book",
        "edited-book": "book",
        "book-chapter": "bookSection",
        "proceedings-article": "conferencePaper",
        "dissertation": "thesis",
        "report": "report",
        "posted-content": "preprint",
        "dataset": "dataset",
    }.get(ctype, "journalArticle")
    authors = [(a.get("family") or a.get("name") or "", a.get("given") or "") for a in m.get("author") or []]
    issn = m.get("ISSN") or []
    isbn = m.get("ISBN") or []
    lic = m.get("license") or []
    return {
        k: v
        for k, v in {
            "item_type": itype,
            "title": strip_tags(first("title")),
            "authors": join_authors(authors),
            "year": year,
            "journal": first("container-title"),
            "volume": m.get("volume", ""),
            "issue": m.get("issue", ""),
            "pages": (m.get("page") or "").replace("–", "-"),
            "publisher": m.get("publisher", ""),
            "doi": m.get("DOI", ""),
            "url": m.get("URL", ""),
            "issn": ", ".join(issn),
            "isbn": ", ".join(isbn),
            "abstract": strip_tags(m.get("abstract", "")),
            "short_title": first("short-title"),
            "language": m.get("language", ""),
            "license": lic[0].get("URL", "") if lic else "",
            "library_catalog": "Crossref",
        }.items()
        if v
    }


def crossref_doi(doi: str, email: str = "") -> dict:
    r = requests.get(
        f"https://api.crossref.org/works/{requests.utils.quote(doi)}", headers=api_headers(email), timeout=TIMEOUT
    )
    if r.status_code == 404:
        raise LookupFailed(f"DOI {doi} was not found on Crossref.")
    r.raise_for_status()
    return _crossref_to_item(r.json()["message"])


def crossref_title(title: str, email: str = "") -> dict:
    r = requests.get(
        "https://api.crossref.org/works",
        params={"query.bibliographic": title, "rows": "3"},
        headers=api_headers(email),
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    items = r.json()["message"].get("items") or []
    if not items:
        raise LookupFailed("No match found on Crossref for this title.")
    # Pick the closest title rather than blindly the first hit.
    best = max(items, key=lambda it: title_similarity(title, (it.get("title") or [""])[0]))
    if title_similarity(title, (best.get("title") or [""])[0]) < TITLE_MATCH:
        raise LookupFailed("Crossref returned results, but none matched the title closely.")
    return _crossref_to_item(best)


def openalex_authors(doi: str, email: str = "") -> list[tuple[str, str]]:
    try:
        r = requests.get(
            f"https://api.openalex.org/works/doi:{requests.utils.quote(doi)}",
            headers=api_headers(email),
            timeout=TIMEOUT,
        )
        if not r.ok:
            return []
        # Profile names can be wrong; the raw name is specific to this work.
        return [
            parse_display_name(
                (a.get("raw_author_name") or "").strip() or (a.get("author") or {}).get("display_name") or ""
            )
            for a in r.json().get("authorships") or []
        ]
    except Exception as e:
        log.info("OpenAlex failed: %s", e)
        return []


ARXIV_API = "https://export.arxiv.org/api/query"
_ARXIV_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def _arxiv_get(params: dict):
    arxiv_wait()
    return requests.get(ARXIV_API, params=params, timeout=TIMEOUT)


def _arxiv_entries(params: dict) -> list:
    r = _arxiv_get(params)
    r.raise_for_status()
    return [
        e
        for e in ET.fromstring(r.content).findall("a:entry", _ARXIV_NS)
        if e.find("a:title", _ARXIV_NS) is not None
        and (e.findtext("a:id", default="", namespaces=_ARXIV_NS) or "").find("api/errors") < 0
    ]


def arxiv_title(title: str) -> dict:
    """Search arXiv by title (for preprints that Crossref does not know)."""
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    if not words:
        raise LookupFailed("No title to search for.")
    entries = _arxiv_entries({"search_query": "ti:(" + " AND ".join(words[:12]) + ")", "max_results": 5})
    scored = [(title_similarity(title, e.findtext("a:title", default="", namespaces=_ARXIV_NS)), e) for e in entries]
    scored = [x for x in scored if x[0] >= TITLE_MATCH]
    if not scored:
        raise LookupFailed("No close match on Crossref or arXiv for this title.")
    entry = max(scored, key=lambda x: x[0])[1]
    m = ARXIV_ID.search(entry.findtext("a:id", default="", namespaces=_ARXIV_NS).split("/abs/")[-1])
    return _arxiv_item(entry, m.group(1) if m else "")


def arxiv(arxiv_id: str) -> dict:
    entries = _arxiv_entries({"id_list": arxiv_id})
    if not entries:
        raise LookupFailed(f"arXiv:{arxiv_id} was not found.")
    return _arxiv_item(entries[0], arxiv_id)


def _arxiv_item(entry, arxiv_id: str) -> dict:
    ns = _ARXIV_NS
    t = lambda path: " ".join((entry.findtext(path, default="", namespaces=ns) or "").split())
    authors = [
        parse_display_name(a.findtext("a:name", default="", namespaces=ns)) for a in entry.findall("a:author", ns)
    ]
    item = {
        "item_type": "preprint",
        "title": t("a:title"),
        "authors": join_authors(authors),
        "year": t("a:published")[:4],
        "abstract": t("a:summary"),
        "journal": "arXiv",
        "archive": "arXiv",
        "archive_id": f"arXiv:{arxiv_id}",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "doi": t("x:doi") or f"10.48550/arXiv.{arxiv_id}",
        "library_catalog": "arXiv.org",
    }
    jref = t("x:journal_ref")
    if jref:
        item["extra"] = f"Published as: {jref}"
    return {k: v for k, v in item.items() if v}


def openlibrary_isbn(isbn: str) -> dict:
    r = requests.get(
        "https://openlibrary.org/api/books",
        params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    d = r.json().get(f"ISBN:{isbn}")
    if not d:
        raise LookupFailed(f"ISBN {isbn} was not found on Open Library.")
    return {
        k: v
        for k, v in {
            "item_type": "book",
            "title": d.get("title", "") + (f": {d['subtitle']}" if d.get("subtitle") else ""),
            "authors": join_authors(parse_display_name(a.get("name", "")) for a in d.get("authors") or []),
            "year": _four_digits(d.get("publish_date")),
            "publisher": ", ".join(p.get("name", "") for p in d.get("publishers") or []),
            "place": ", ".join(p.get("name", "") for p in d.get("publish_places") or []),
            "isbn": isbn,
            "url": d.get("url", ""),
            "library_catalog": "Open Library",
        }.items()
        if v
    }


pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lookup")


def resolve(query: dict) -> dict:
    kind, value = query["kind"], query["value"]
    email = query.get("email") or ""
    # With a DOI known up front, ask OpenAlex for the authors at the same time as Crossref.
    oa_future = pool.submit(openalex_authors, value, email) if kind == "doi" else None
    try:
        if kind == "doi":
            item = crossref_doi(value, email)
        elif kind == "arxiv":
            item = arxiv(value)
        elif kind == "isbn":
            item = openlibrary_isbn(value)
        elif kind == "title":
            try:
                item = crossref_title(value, email)
            except LookupFailed as crossref_error:
                try:
                    item = arxiv_title(value)
                except LookupFailed:
                    raise LookupFailed(
                        "No close match for this title on Crossref or arXiv. "
                        "Add the DOI or arXiv ID to the item and try again."
                    ) from None
                except (requests.RequestException, ET.ParseError):
                    raise crossref_error from None
        else:
            raise LookupFailed("PubMed lookups are not supported yet.")
    except requests.RequestException as e:
        url = getattr(getattr(e, "request", None), "url", None)
        host = urlparse(str(url)).netloc if url else "the metadata service"
        raise LookupFailed(f"Could not reach {host}. Check your internet connection and try again.") from e

    if item.get("doi") and kind in ("doi", "title"):
        if oa_future is not None and clean_doi(item["doi"]).lower() == value.lower():
            oa = oa_future.result()
        else:
            oa = openalex_authors(item["doi"], email)
        # OpenAlex's author list wins when it is more complete than Crossref's.
        if oa and len(join_authors(oa)) > len(item.get("authors") or ""):
            item["authors"] = join_authors(oa)
    return item
