"""Online metadata lookup: Crossref (DOI / title), OpenAlex, arXiv and Open Library."""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
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
    best = max(items, key=lambda it: title_similarity(title, (it.get("title") or [""])[0]))
    if title_similarity(title, (best.get("title") or [""])[0]) < TITLE_MATCH:
        raise LookupError_("Crossref returned results, but none matched the title closely.")
    return _crossref_to_item(best)


TITLE_MATCH = 0.8


def title_similarity(a: str, b: str) -> float:
    """0..1, ignoring case, accents, punctuation and spacing.

    Uses difflib's matching blocks, so an extra or missing word ("The …", a subtitle) only
    lowers the score a little. (The old character-by-position comparison dropped to ~0 as
    soon as the two titles were shifted by one character.)
    """
    pa, pb = _plain(a), _plain(b)
    if not pa or not pb:
        return 0.0
    return difflib.SequenceMatcher(None, pa, pb, autojunk=False).ratio()


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


ARXIV_API = "https://export.arxiv.org/api/query"
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)
_ARXIV_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)


def arxiv_id_of(item: dict) -> str | None:
    """The arXiv identifier of an existing item, if it records one anywhere."""
    for field in ("archive_id", "doi", "url", "extra", "journal", "number"):
        v = str(item.get(field) or "")
        m = re.search(r"(?:arxiv[:./\s]*|arxiv\.org/(?:abs|pdf)/|10\.48550/arxiv\.)" + _ARXIV_ID.pattern, v, re.I)
        if m:
            return m.group(1)
    return None


# Lookups of several items now run side by side; arXiv asks API users to space their requests.
ARXIV_SPACING = 1.0
_arxiv_lock = threading.Lock()
_arxiv_last = 0.0


def _arxiv_get(params: dict):
    global _arxiv_last
    with _arxiv_lock:
        wait = _arxiv_last + ARXIV_SPACING - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _arxiv_last = time.monotonic()
    return requests.get(ARXIV_API, params=params, timeout=TIMEOUT)


def _arxiv_entries(params: dict) -> list:
    r = _arxiv_get(params)
    r.raise_for_status()
    return [e for e in ET.fromstring(r.content).findall("a:entry", _ARXIV_NS) if e.find("a:title", _ARXIV_NS) is not None
            and (e.findtext("a:id", default="", namespaces=_ARXIV_NS) or "").find("api/errors") < 0]


def arxiv_title(title: str) -> dict:
    """Search arXiv by title (for preprints that Crossref does not know)."""
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    if not words:
        raise LookupError_("No title to search for.")
    entries = _arxiv_entries({"search_query": "ti:(" + " AND ".join(words[:12]) + ")", "max_results": 5})
    scored = [(title_similarity(title, e.findtext("a:title", default="", namespaces=_ARXIV_NS)), e) for e in entries]
    scored = [x for x in scored if x[0] >= TITLE_MATCH]
    if not scored:
        raise LookupError_("No close match on Crossref or arXiv for this title.")
    entry = max(scored, key=lambda x: x[0])[1]
    m = _ARXIV_ID.search(entry.findtext("a:id", default="", namespaces=_ARXIV_NS).split("/abs/")[-1])
    return _arxiv_item(entry, m.group(1) if m else "")


def arxiv(arxiv_id: str) -> dict:
    entries = _arxiv_entries({"id_list": arxiv_id})
    if not entries:
        raise LookupError_(f"arXiv:{arxiv_id} was not found.")
    return _arxiv_item(entries[0], arxiv_id)


def _arxiv_item(entry, arxiv_id: str) -> dict:
    ns = _ARXIV_NS
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
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lookup")


def lookup(*, identifier: str | None = None, doi: str | None = None, title: str | None = None,
           arxiv_id: str | None = None, known: list[tuple[str, str]] | None = None, email: str = "") -> dict:
    """Return normalized metadata for an identifier, a DOI, an arXiv id or a title.

    arXiv's own DOIs (10.48550/arXiv.…) are registered with DataCite, not Crossref, so they and
    explicit arXiv ids go to the arXiv API. A title that Crossref cannot match closely is also
    tried on arXiv (preprints are often only there).
    """
    doi = clean_doi(doi) if doi else None
    if doi and (m := _ARXIV_DOI.match(doi)):
        arxiv_id, doi = arxiv_id or m.group(1), None
    if doi:
        kind, value = "doi", doi
    elif arxiv_id:
        kind, value = "arxiv", arxiv_id
    elif identifier:
        kind, value = classify_identifier(identifier)
        if kind == "doi" and (m := _ARXIV_DOI.match(value)):
            kind, value = "arxiv", m.group(1)
    else:
        kind, value = "title", title
    if not value:
        raise LookupError_("Nothing to look up: the item needs a DOI or a title.")
    # With a DOI known up front, ask OpenAlex for the authors at the same time as Crossref.
    oa_future = _POOL.submit(openalex_authors, value, email) if kind == "doi" else None
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
            except LookupError_ as crossref_error:
                try:
                    item = arxiv_title(value)
                except LookupError_:
                    raise LookupError_("No close match for this title on Crossref or arXiv. "
                                       "Add the DOI or arXiv ID to the item and try again.") from None
                except (requests.RequestException, ET.ParseError):
                    raise crossref_error from None
        else:
            raise LookupError_("PubMed lookups are not supported yet.")
    except requests.RequestException as e:
        host = urlparse(e.request.url).netloc if getattr(e, "request", None) is not None and e.request.url else "the metadata service"
        raise LookupError_(f"Could not reach {host}. Check your internet connection and try again.") from e

    if item.get("doi") and kind in ("doi", "title"):
        same = oa_future is not None and clean_doi(item["doi"]).lower() == value.lower()
        oa = oa_future.result() if same else openalex_authors(item["doi"], email)
        # Same rule as before the refactor: prefer OpenAlex when it is more complete.
        if oa and len(join_authors(oa)) > len(item.get("authors") or ""):
            item["authors"] = join_authors(oa)
    if known and item.get("authors"):
        item["authors"] = resolve_authors(split_authors(item["authors"]), known)
    return item
