"""Online metadata lookup: Crossref (DOI / title), OpenAlex, arXiv and Open Library."""
from __future__ import annotations

import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

from .formats.common import join_authors, parse_display_name, split_authors, strip_tags
from .pdf_finder import clean_doi

log = logging.getLogger(__name__)
TIMEOUT = 15


class LookupError_(Exception):
    pass


def _headers(email: str) -> dict:
    ua = "ZoteroClone/2.0 (https://github.com/; mailto:%s)" % (email or "zotero-clone@example.com")
    return {"User-Agent": ua}


# ------------------------------------------------------------ identifiers
def classify_identifier(s: str) -> tuple[str, str]:
    s = (s or "").strip()
    m = re.search(r"(10\.\d{4,9}/\S+)", s)
    if m:
        return "doi", clean_doi(m.group(1)).rstrip(".")
    m = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?(\d{4}\.\d{4,5})(v\d+)?", s, re.I)
    if m:
        return "arxiv", m.group(1)
    m = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)([a-z\-]+(?:\.[A-Z]{2})?/\d{7})", s, re.I)
    if m:
        return "arxiv", m.group(1)
    digits = re.sub(r"[^0-9Xx]", "", s)
    if len(digits) in (10, 13) and re.fullmatch(r"[\d\-\sXx]+", s.replace("ISBN", "").replace(":", "").strip()):
        return "isbn", digits.upper()
    m = re.fullmatch(r"(?:PMID:?\s*)?(\d{5,9})", s, re.I)
    if m:
        return "pmid", m.group(1)
    raise LookupError_("Not a recognised identifier. Try a DOI, arXiv ID or ISBN.")


# ------------------------------------------------------- author resolution
def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def _given_compatible(fetched: str, known: str) -> bool:
    fp = [p for p in re.split(r"[\s.\-]+", fetched or "") if p]
    kp = [p for p in re.split(r"[\s.\-]+", known or "") if p]
    return bool(fp) and len(fp) == len(kp) and all(_plain(a) == _plain(b) for a, b in zip(fp, kp))


def resolve_authors(pairs: list[tuple[str, str]], known: list[tuple[str, str]]) -> str:
    """Prefer the accented spelling already in the library (Banuls -> Bañuls)."""
    out = []
    for fam, giv in pairs:
        best = None
        for kf, kg in known:
            if _plain(kf) == _plain(fam) and _given_compatible(giv, kg):
                cand = f"{kf}, {kg}"
                if re.search(r"[^\x00-\x7f]", cand) and not re.search(r"[^\x00-\x7f]", f"{fam}, {giv}"):
                    best = (kf, kg)
                    break
        out.append(best or (fam, giv))
    return join_authors(out)


def known_authors(papers: list[dict]) -> list[tuple[str, str]]:
    seen = set()
    out = []
    for p in papers:
        for pair in split_authors(p.get("authors")):
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
    return out


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
    itype = {"journal-article": "journalArticle", "book": "book", "monograph": "book", "edited-book": "book",
             "book-chapter": "bookSection", "proceedings-article": "conferencePaper", "dissertation": "thesis",
             "report": "report", "posted-content": "preprint", "dataset": "dataset"}.get(ctype, "journalArticle")
    authors = [(a.get("family") or a.get("name") or "", a.get("given") or "") for a in m.get("author") or []]
    issn = m.get("ISSN") or []
    isbn = m.get("ISBN") or []
    lic = m.get("license") or []
    return {k: v for k, v in {
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
    }.items() if v}


def crossref_doi(doi: str, email: str = "") -> dict:
    r = requests.get(f"https://api.crossref.org/works/{requests.utils.quote(doi)}",
                     headers=_headers(email), timeout=TIMEOUT)
    if r.status_code == 404:
        raise LookupError_(f"DOI {doi} was not found on Crossref.")
    r.raise_for_status()
    return _crossref_to_item(r.json()["message"])


def crossref_title(title: str, email: str = "") -> dict:
    r = requests.get("https://api.crossref.org/works", params={"query.bibliographic": title, "rows": 3},
                     headers=_headers(email), timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json()["message"].get("items") or []
    if not items:
        raise LookupError_("No match found on Crossref for this title.")
    # Pick the closest title rather than blindly the first hit.
    want = _plain(title)
    def score(it):
        got = _plain((it.get("title") or [""])[0])
        if not got:
            return 0
        common = sum(1 for a, b in zip(want, got) if a == b)
        return common / max(len(want), len(got))
    best = max(items, key=score)
    if score(best) < 0.6:
        raise LookupError_("Crossref returned results, but none matched the title closely.")
    return _crossref_to_item(best)


def openalex_authors(doi: str, email: str = "") -> list[tuple[str, str]]:
    try:
        r = requests.get(f"https://api.openalex.org/works/doi:{requests.utils.quote(doi)}",
                         headers=_headers(email), timeout=TIMEOUT)
        if not r.ok:
            return []
        return [parse_display_name((a.get("author") or {}).get("display_name") or "")
                for a in r.json().get("authorships") or []]
    except Exception as e:
        log.info("OpenAlex failed: %s", e)
        return []


def arxiv(arxiv_id: str) -> dict:
    r = requests.get("http://export.arxiv.org/api/query", params={"id_list": arxiv_id}, timeout=TIMEOUT)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    entry = ET.fromstring(r.content).find("a:entry", ns)
    if entry is None or entry.find("a:title", ns) is None:
        raise LookupError_(f"arXiv:{arxiv_id} was not found.")
    t = lambda path: " ".join((entry.findtext(path, default="", namespaces=ns) or "").split())
    authors = [parse_display_name(a.findtext("a:name", default="", namespaces=ns)) for a in entry.findall("a:author", ns)]
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
    r = requests.get("https://openlibrary.org/api/books",
                     params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"}, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json().get(f"ISBN:{isbn}")
    if not d:
        raise LookupError_(f"ISBN {isbn} was not found on Open Library.")
    return {k: v for k, v in {
        "item_type": "book",
        "title": d.get("title", "") + (f": {d['subtitle']}" if d.get("subtitle") else ""),
        "authors": join_authors(parse_display_name(a.get("name", "")) for a in d.get("authors") or []),
        "year": re.search(r"\d{4}", d.get("publish_date", "") or "").group(0) if re.search(r"\d{4}", d.get("publish_date", "") or "") else "",
        "publisher": ", ".join(p.get("name", "") for p in d.get("publishers") or []),
        "place": ", ".join(p.get("name", "") for p in d.get("publish_places") or []),
        "isbn": isbn,
        "url": d.get("url", ""),
        "library_catalog": "Open Library",
    }.items() if v}


# ------------------------------------------------------------------ facade
def lookup(*, identifier: str | None = None, doi: str | None = None, title: str | None = None,
           known: list[tuple[str, str]] | None = None, email: str = "") -> dict:
    """Return normalized metadata for an identifier, a DOI or a title."""
    kind, value = ("doi", clean_doi(doi)) if doi else (classify_identifier(identifier) if identifier else ("title", title))
    if not value:
        raise LookupError_("Nothing to look up: the item needs a DOI or a title.")
    try:
        if kind == "doi":
            item = crossref_doi(value, email)
        elif kind == "arxiv":
            item = arxiv(value)
        elif kind == "isbn":
            item = openlibrary_isbn(value)
        elif kind == "title":
            item = crossref_title(value, email)
        else:
            raise LookupError_("PubMed lookups are not supported yet.")
    except requests.RequestException as e:
        host = urlparse(e.request.url).netloc if getattr(e, "request", None) is not None and e.request.url else "the metadata service"
        raise LookupError_(f"Could not reach {host}. Check your internet connection and try again.") from e

    if item.get("doi") and kind in ("doi", "title"):
        oa = openalex_authors(item["doi"], email)
        # Same rule as before the refactor: prefer OpenAlex when it is more complete.
        if oa and len(join_authors(oa)) > len(item.get("authors") or ""):
            item["authors"] = join_authors(oa)
    if known and item.get("authors"):
        item["authors"] = resolve_authors(split_authors(item["authors"]), known)
    return item
