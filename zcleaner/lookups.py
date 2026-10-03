"""External metadata sources: Crossref, DOI content negotiation (DataCite etc.),
arXiv.  All results are cached in the workspace; requests are rate limited and
retried politely."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

from . import textutil as tu

log = logging.getLogger(__name__)
UA = "ZoteroLibraryCleaner/2.0 (https://github.com/; local research utility)"

CROSSREF_TYPES = {
    "journal-article": "journalArticle", "proceedings-article": "conferencePaper",
    "book-chapter": "bookSection", "book-section": "bookSection", "book-part": "bookSection",
    "reference-entry": "encyclopediaArticle", "book": "book", "monograph": "book", "edited-book": "book",
    "reference-book": "book", "book-set": "book", "book-series": "book", "posted-content": "preprint",
    "dissertation": "thesis", "report": "report", "report-series": "report", "dataset": "dataset",
    "standard": "standard", "journal-issue": "journalArticle", "peer-review": "journalArticle",
}
CSL_TYPES = {
    "article-journal": "journalArticle", "paper-conference": "conferencePaper", "chapter": "bookSection",
    "book": "book", "report": "report", "thesis": "thesis", "dataset": "dataset", "article": "preprint",
    "manuscript": "manuscript", "entry-encyclopedia": "encyclopediaArticle", "standard": "standard",
    "software": "computerProgram", "webpage": "webpage", "article-magazine": "magazineArticle",
    "article-newspaper": "newspaperArticle", "patent": "patent", "speech": "presentation",
}


class RateLimiter:
    def __init__(self, rps: float):
        self.interval = 1.0 / max(0.1, rps)
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self):
        with self.lock:
            t = time.monotonic()
            if t < self.next:
                time.sleep(self.next - t)
            self.next = max(t, self.next) + self.interval


class NotFound(Exception):
    pass


class NetworkDown(RuntimeError):
    pass


def _date_from_parts(parts) -> str:
    try:
        p = parts[0]
        if not p or p[0] is None:
            return ""
        y = int(p[0])
        if len(p) >= 3 and p[1] and p[2]:
            return f"{y:04d}-{int(p[1]):02d}-{int(p[2]):02d}"
        if len(p) >= 2 and p[1]:
            return f"{y:04d}-{int(p[1]):02d}"
        return f"{y:04d}"
    except Exception:
        return ""


def _person(given: str, family: str, literal: str = "", ctype="author") -> dict:
    if family:
        return {"creatorType": ctype, "firstName": given or "", "lastName": family}
    if literal:
        return {"creatorType": ctype, "name": literal}
    return {"creatorType": ctype, "firstName": "", "lastName": given or ""}


class Lookups:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.http = requests.Session()
        self.http.headers["User-Agent"] = UA
        rps = settings.get("lookups", "requests_per_second") or 5
        self.crossref_rl = RateLimiter(rps)
        self.doi_rl = RateLimiter(max(1, rps / 2))
        self.arxiv_rl = RateLimiter(1 / 3.1)  # arXiv asks for one request every 3 seconds
        self.net_failures = 0
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ http
    def _get(self, url, rl: RateLimiter, *, params=None, headers=None, tries=5):
        mailto = (self.settings.get("mailto") or "").strip()
        h = dict(headers or {})
        if mailto:
            h["User-Agent"] = f"{UA} mailto:{mailto}"
        delay = 2.0
        for attempt in range(tries):
            if self.net_failures >= 6:
                raise NetworkDown("Cannot reach the metadata services (network/firewall/proxy?). "
                                  "Lookups stopped; results obtained so far are kept.")
            rl.wait()
            try:
                r = self.http.get(url, params=params, headers=h, timeout=30, allow_redirects=True)
            except requests.RequestException as e:
                with self.lock:
                    self.net_failures += 1
                if attempt >= 1:
                    raise
                time.sleep(delay)
                delay *= 2
                continue
            with self.lock:
                self.net_failures = 0
            if r.status_code == 404:
                raise NotFound(url)
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else delay
                time.sleep(min(wait, 120))
                delay *= 2
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{url} → HTTP {r.status_code}")
            return r
        raise RuntimeError(f"{url}: too many retries")

    def _cached(self, key, fn, refresh=False):
        if not refresh:
            v = self.store.cache_get(key)
            if v is not None:
                if v.get("_notfound"):
                    raise NotFound(key)
                return v
        try:
            v = fn()
        except NotFound:
            self.store.cache_set(key, {"_notfound": True})
            raise
        self.store.cache_set(key, v)
        return v

    # ------------------------------------------------------------------ Crossref
    def crossref(self, doi: str, refresh=False) -> dict:
        doi = tu.clean_doi(doi)
        mailto = (self.settings.get("mailto") or "").strip()

        def fetch():
            params = {"mailto": mailto} if mailto else None
            r = self._get("https://api.crossref.org/works/" + quote(doi, safe=""), self.crossref_rl, params=params)
            return r.json()["message"]
        return self.crossref_record(self._cached("crossref:" + doi.lower(), fetch, refresh))

    @staticmethod
    def crossref_record(m: dict) -> dict:
        title = (m.get("title") or [""])[0] if m.get("title") else ""
        sub = (m.get("subtitle") or [""])[0] if m.get("subtitle") else ""
        if sub and tu.canon(sub) not in tu.canon(title):
            title = f"{title.rstrip(' :')}: {sub}"
        creators = [_person(a.get("given", ""), a.get("family", ""), a.get("name", ""), "author") for a in m.get("author", [])]
        creators += [_person(a.get("given", ""), a.get("family", ""), a.get("name", ""), "editor") for a in m.get("editor", [])]
        date = ""
        for k in ("issued", "published-print", "published-online", "published", "created"):
            date = _date_from_parts((m.get(k) or {}).get("date-parts") or [[None]])
            if date:
                break
        typ = CROSSREF_TYPES.get(m.get("type", ""), "")
        if m.get("type") == "posted-content" and m.get("subtype") not in (None, "preprint"):
            typ = "manuscript"
        rel = m.get("relation") or {}
        rec = {
            "source": "crossref", "itemType": typ, "title": title, "creators": creators, "date": date,
            "publicationTitle": (m.get("container-title") or [""])[0] if m.get("container-title") else "",
            "journalAbbreviation": (m.get("short-container-title") or [""])[0] if m.get("short-container-title") else "",
            "volume": m.get("volume", ""), "issue": m.get("issue", ""),
            "pages": m.get("page", "") or m.get("article-number", ""),
            "DOI": m.get("DOI", ""), "ISSN": ", ".join(m.get("ISSN", []) or []),
            "ISBN": " ".join(m.get("ISBN", []) or []), "publisher": m.get("publisher", ""),
            "place": m.get("publisher-location", ""), "abstractNote": m.get("abstract", ""),
            "language": m.get("language", ""),
            "is_preprint_of": [x.get("id") for x in rel.get("is-preprint-of", []) if x.get("id-type") == "doi"],
            "has_preprint": [x.get("id") for x in rel.get("has-preprint", []) if x.get("id-type") == "doi"],
            "event": ((m.get("event") or {}).get("name") or ""),
        }
        if typ == "conferencePaper" and rec["event"]:
            rec["conferenceName"] = rec["event"]
        if typ == "preprint":
            rec["repository"] = rec.get("publisher") or (m.get("institution") or [{}])[0].get("name", "")
        return rec

    def crossref_search(self, title: str, author: str = "", year: str = "", rows: int = 5) -> list[dict]:
        q = " ".join(x for x in (tu.plain(title), tu.plain(author), year) if x)
        key = "crsearch:" + hashlib.sha1(q.lower().encode()).hexdigest()
        mailto = (self.settings.get("mailto") or "").strip()

        def fetch():
            params = {"query.bibliographic": q[:500], "rows": rows,
                      "select": "DOI,title,subtitle,author,editor,issued,container-title,short-container-title,type,"
                                "volume,issue,page,ISSN,ISBN,publisher,published-print,published-online,article-number"}
            if mailto:
                params["mailto"] = mailto
            r = self._get("https://api.crossref.org/works", self.crossref_rl, params=params)
            return {"items": r.json()["message"].get("items", [])}
        try:
            data = self._cached(key, fetch)
        except NotFound:
            return []
        return [self.crossref_record(m) for m in data.get("items", [])]

    # ------------------------------------------------------------------ DOI content negotiation
    def doi_csl(self, doi: str, refresh=False) -> dict:
        doi = tu.clean_doi(doi)

        def fetch():
            r = self._get("https://doi.org/" + quote(doi, safe="/"), self.doi_rl,
                          headers={"Accept": "application/vnd.citationstyles.csl+json"})
            if "json" not in r.headers.get("Content-Type", ""):
                raise NotFound(doi)
            return r.json()
        m = self._cached("csl:" + doi.lower(), fetch, refresh)
        creators = [_person(a.get("given", ""), a.get("family", ""), a.get("literal", ""), "author") for a in m.get("author", [])]
        date = _date_from_parts((m.get("issued") or {}).get("date-parts") or [[None]])
        ct = m.get("container-title")
        if isinstance(ct, list):
            ct = ct[0] if ct else ""
        return {"source": "doi.org", "itemType": CSL_TYPES.get(m.get("type", ""), ""), "title": m.get("title", "") if isinstance(m.get("title"), str) else (m.get("title") or [""])[0],
                "creators": creators, "date": date, "publicationTitle": ct or "", "volume": str(m.get("volume", "") or ""),
                "issue": str(m.get("issue", "") or ""), "pages": str(m.get("page", "") or ""), "DOI": m.get("DOI", doi),
                "publisher": m.get("publisher", ""), "abstractNote": m.get("abstract", ""), "ISSN": "", "ISBN": "",
                "journalAbbreviation": "", "place": "", "language": m.get("language", "") or ""}

    # ------------------------------------------------------------------ arXiv
    def arxiv_batch(self, ids: list[str], refresh=False) -> dict[str, dict]:
        """Records for many arXiv ids (100 per request, cached per id)."""
        out, todo = {}, []
        for i in ids:
            v = None if refresh else self.store.cache_get("arxiv:" + i)
            if v is None:
                todo.append(i)
            elif not v.get("_notfound"):
                out[i] = v
        for k in range(0, len(todo), 100):
            chunk = todo[k:k + 100]
            r = self._get("https://export.arxiv.org/api/query", self.arxiv_rl,
                          params={"id_list": ",".join(chunk), "max_results": len(chunk)})
            got = self.parse_arxiv(r.text)
            for i in chunk:
                rec = got.get(i)
                self.store.cache_set("arxiv:" + i, rec or {"_notfound": True})
                if rec:
                    out[i] = rec
        return out

    @staticmethod
    def parse_arxiv(xml_text: str) -> dict[str, dict]:
        ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
        root = ET.fromstring(xml_text)
        res = {}
        for e in root.findall("a:entry", ns):
            idurl = e.findtext("a:id", default="", namespaces=ns)
            m = re.search(r"arxiv\.org/abs/(.+?)(v\d+)?$", idurl)
            if not m:
                continue
            aid = m.group(1)
            title = " ".join((e.findtext("a:title", default="", namespaces=ns) or "").split())
            if not title or title == "Error":
                continue
            creators = []
            for a in e.findall("a:author", ns):
                n = " ".join((a.findtext("a:name", default="", namespaces=ns) or "").split())
                sp = tu.split_single_field_name(n)
                creators.append({"creatorType": "author", "firstName": sp[1], "lastName": sp[0]} if sp
                                else {"creatorType": "author", "name": n})
            prim = e.find("x:primary_category", ns)
            res[aid] = {
                "source": "arxiv", "itemType": "preprint", "title": title, "creators": creators,
                "date": (e.findtext("a:published", default="", namespaces=ns) or "")[:10],
                "abstractNote": " ".join((e.findtext("a:summary", default="", namespaces=ns) or "").split()),
                "repository": "arXiv", "archiveID": f"arXiv:{aid}", "url": f"https://arxiv.org/abs/{aid}",
                "DOI": f"10.48550/arXiv.{aid}", "published_doi": e.findtext("x:doi", default="", namespaces=ns) or "",
                "journal_ref": e.findtext("x:journal_ref", default="", namespaces=ns) or "",
                "category": prim.get("term") if prim is not None else "",
            }
        return res
