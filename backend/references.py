"""Citation graph: the reference lists of library items, fetched online.

For each item we store the works it cites (in order), whether or not they are in the library.
Together these give a graph — library item -> cited work — where a cited work that is also in
the library is matched by DOI or arXiv id. It is meant as the base for later AI features
("what does this project rest on", "what is cited a lot that I have not read").

Stored in its own database (``data/citations.db``) so the library database is never touched.
Deleting the file loses the fetched lists, which can be fetched again.

Sources, tried in order until one returns references:
  1. Semantic Scholar  (DOI or arXiv id; one request, titles included; good for arXiv)
  2. OpenAlex          (DOI; referenced works + their details; an API key raises the daily quota)
  3. Crossref          (DOI; the publisher's reference list, often with DOIs only)
"""
from __future__ import annotations

import logging
import queue
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import requests

from .db import utcnow
from .formats.common import join_authors, parse_display_name
from .metadata import TIMEOUT, LookupError_, _headers, arxiv_id_of
from .pdf_finder import clean_doi

log = logging.getLogger(__name__)

S2_API = "https://api.semanticscholar.org/graph/v1"
OPENALEX_API = "https://api.openalex.org"
CROSSREF_API = "https://api.crossref.org"
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)
_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetches (
    item_id TEXT PRIMARY KEY,
    source TEXT,              -- semanticscholar | openalex | crossref | none
    fetched_at TEXT,
    doi TEXT, arxiv_id TEXT,  -- what the lookup used
    count INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS refs (
    item_id TEXT NOT NULL,
    position INTEGER NOT NULL,   -- order in the reference list
    doi TEXT, arxiv_id TEXT, openalex_id TEXT, s2_id TEXT,
    title TEXT, authors TEXT, year INTEGER, venue TEXT,
    raw TEXT,                    -- the citation as printed, when that is all the source gives
    PRIMARY KEY (item_id, position)
);
CREATE INDEX IF NOT EXISTS idx_refs_doi ON refs(doi);
CREATE INDEX IF NOT EXISTS idx_refs_arxiv ON refs(arxiv_id);
"""
REF_FIELDS = ("doi", "arxiv_id", "openalex_id", "s2_id", "title", "authors", "year", "venue", "raw")


# ------------------------------------------------------------------ identifiers
def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = clean_doi(str(doi).strip())
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d, flags=re.I).strip().rstrip(".")
    return d.lower() or None


def norm_arxiv(aid: str | None) -> str | None:
    if not aid:
        return None
    m = _ARXIV_ID.search(str(aid))
    return m.group(1).lower() if m else None


def identifiers(item: dict) -> tuple[str | None, str | None]:
    """(doi, arXiv id) of a library item; an arXiv DOI counts as its arXiv id."""
    doi = norm_doi(item.get("doi"))
    arx = norm_arxiv(arxiv_id_of(item))
    if doi and _ARXIV_DOI.match(doi):
        arx = arx or norm_arxiv(_ARXIV_DOI.match(doi).group(1))
    return doi, arx


def _clean_ref(r: dict) -> dict:
    out = {k: r.get(k) for k in REF_FIELDS}
    out["doi"] = norm_doi(out["doi"])
    out["arxiv_id"] = norm_arxiv(out["arxiv_id"])
    if out["doi"] and not out["arxiv_id"] and _ARXIV_DOI.match(out["doi"]):
        out["arxiv_id"] = norm_arxiv(_ARXIV_DOI.match(out["doi"]).group(1))
    try:
        out["year"] = int(out["year"]) if out["year"] else None
    except (TypeError, ValueError):
        out["year"] = None
    for k in ("title", "authors", "venue", "raw"):
        out[k] = " ".join(str(out[k]).split()) if out[k] else None
    return out


# ------------------------------------------------------------------ sources
def _get(url: str, *, params=None, headers=None, retries: int = 1):
    """GET with one retry on 429/5xx. Returns the response, or None for 404."""
    for attempt in range(retries + 1):
        r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            try:
                wait = float(r.headers.get("Retry-After") or 2)
            except ValueError:   # an HTTP date
                wait = 2
            time.sleep(min(max(wait, 0), 10))
            continue
        r.raise_for_status()
        return r
    return None


def _q(s: str) -> str:
    """Quote an identifier for a URL path (old DOIs can contain '#', ';', '<' …)."""
    return requests.utils.quote(s, safe="/:")


def semantic_scholar(doi: str | None, arxiv_id: str | None, *, email: str = "", api_key: str = "") -> list[dict]:
    """Try the DOI first (a journal version), then the arXiv id."""
    ids = []
    if doi and not _ARXIV_DOI.match(doi):
        ids.append(f"DOI:{doi}")
    if arxiv_id:
        ids.append(f"arXiv:{arxiv_id}")
    elif doi and _ARXIV_DOI.match(doi):
        ids.append(f"DOI:{doi}")
    for pid in ids:
        refs = _s2_references(pid, email=email, api_key=api_key)
        if refs:
            return refs
    return []


def _s2_references(pid: str, *, email: str, api_key: str) -> list[dict]:
    headers = _headers(email)
    if api_key:
        headers["x-api-key"] = api_key
    out, offset = [], 0
    while True:
        r = _get(f"{S2_API}/paper/{_q(pid)}/references", headers=headers,
                 params={"fields": "title,year,authors,venue,externalIds", "limit": 1000, "offset": offset})
        if r is None:
            return []
        d = r.json()
        for row in d.get("data") or []:
            p = row.get("citedPaper") or {}
            if not p.get("title") and not p.get("externalIds"):
                continue
            ext = p.get("externalIds") or {}
            out.append({"doi": ext.get("DOI"), "arxiv_id": ext.get("ArXiv"), "s2_id": p.get("paperId"),
                        "title": p.get("title"), "year": p.get("year"), "venue": p.get("venue"),
                        "authors": join_authors([parse_display_name(a.get("name") or "") for a in p.get("authors") or []])})
        if d.get("next") is None or not d.get("data"):
            return out
        offset = d["next"]


def _openalex_work(w: dict) -> dict:
    ids = w.get("ids") or {}
    arx = None
    for loc in [w.get("primary_location") or {}] + list(w.get("locations") or []):
        m = re.search(r"arxiv\.org/(?:abs|pdf)/" + _ARXIV_ID.pattern, (loc or {}).get("landing_page_url") or "", re.I)
        if m:
            arx = m.group(1)
            break
    src = ((w.get("primary_location") or {}).get("source") or {})
    return {"doi": w.get("doi") or ids.get("doi"), "arxiv_id": arx, "openalex_id": (w.get("id") or "").rsplit("/", 1)[-1] or None,
            "title": w.get("title") or w.get("display_name"), "year": w.get("publication_year"),
            "venue": src.get("display_name"),
            "authors": join_authors([parse_display_name((a.get("author") or {}).get("display_name") or "")
                                     for a in w.get("authorships") or []])}


def openalex(doi: str | None, *, email: str = "", api_key: str = "") -> list[dict]:
    if not doi:
        return []
    params = {"select": "id,referenced_works"}
    if api_key:
        params["api_key"] = api_key
    elif email:
        params["mailto"] = email
    r = _get(f"{OPENALEX_API}/works/doi:{_q(doi)}", params=params, headers=_headers(email))
    if r is None:
        return []
    wids = [w.rsplit("/", 1)[-1] for w in r.json().get("referenced_works") or []]
    details: dict[str, dict] = {}
    for i in range(0, len(wids), 50):   # 50 ids per request (OpenAlex's OR-filter limit)
        chunk = wids[i:i + 50]
        p = {"filter": "openalex:" + "|".join(chunk), "per-page": 50,
             "select": "id,doi,title,display_name,publication_year,authorships,primary_location,locations,ids"}
        if api_key:
            p["api_key"] = api_key
        elif email:
            p["mailto"] = email
        rr = _get(f"{OPENALEX_API}/works", params=p, headers=_headers(email))
        for w in (rr.json().get("results") or []) if rr is not None else []:
            details[(w.get("id") or "").rsplit("/", 1)[-1]] = _openalex_work(w)
    return [details.get(w) or {"openalex_id": w} for w in wids]   # keep the original order


def crossref(doi: str | None, *, email: str = "") -> list[dict]:
    if not doi or _ARXIV_DOI.match(doi):
        return []
    r = _get(f"{CROSSREF_API}/works/{_q(doi)}", headers=_headers(email))
    if r is None:
        return []
    out = []
    for ref in (r.json().get("message") or {}).get("reference") or []:
        out.append({"doi": ref.get("DOI"), "title": ref.get("article-title") or ref.get("volume-title"),
                    "authors": ref.get("author"), "year": (re.search(r"\d{4}", ref.get("year") or "") or [None])[0],
                    "venue": ref.get("journal-title") or ref.get("series-title"), "raw": ref.get("unstructured")})
    return out


def fetch(item: dict, *, email: str = "", openalex_key: str = "", s2_key: str = "") -> tuple[str, list[dict]]:
    """The reference list of ``item``: (source name, references). Raises LookupError_ when the
    item has no DOI or arXiv id; returns ("none", []) when no source knows its references."""
    doi, arx = identifiers(item)
    if not doi and not arx:
        raise LookupError_("This item has no DOI or arXiv ID to look up its references with.")
    attempts = [
        ("semanticscholar", lambda: semantic_scholar(doi, arx, email=email, api_key=s2_key)),
        ("openalex", lambda: openalex(doi or (f"10.48550/arxiv.{arx}" if arx else None), email=email, api_key=openalex_key)),
        ("crossref", lambda: crossref(doi, email=email)),
    ]
    errors = []
    for name, run in attempts:
        try:
            refs = [_clean_ref(r) for r in run()]
        except Exception as e:   # a failing service must not hide the next one
            log.info("References from %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
            continue
        refs = [r for r in refs if any(r[k] for k in ("doi", "arxiv_id", "openalex_id", "s2_id", "title", "raw"))]
        if refs:
            return name, refs
    if errors:   # an answer of "no references" only counts when every service answered
        raise LookupError_("Could not get the references (" + "; ".join(errors) + ").")
    return "none", []


# ------------------------------------------------------------------ storage
class CitationStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        with self._session() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _session(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save(self, item_id: str, source: str, refs: list[dict], *, doi=None, arxiv_id=None) -> None:
        with self._session() as c:
            c.execute("DELETE FROM refs WHERE item_id = ?", (item_id,))
            c.executemany(f"INSERT INTO refs (item_id, position, {', '.join(REF_FIELDS)}) VALUES (?, ?, {', '.join('?' * len(REF_FIELDS))})",
                          [(item_id, i, *(r.get(k) for k in REF_FIELDS)) for i, r in enumerate(refs)])
            c.execute("INSERT OR REPLACE INTO fetches (item_id, source, fetched_at, doi, arxiv_id, count, error) "
                      "VALUES (?, ?, ?, ?, ?, ?, NULL)", (item_id, source, utcnow(), doi, arxiv_id, len(refs)))

    def save_error(self, item_id: str, message: str) -> None:
        """Remember a failed fetch without losing references fetched earlier."""
        with self._session() as c:
            if c.execute("SELECT 1 FROM fetches WHERE item_id = ?", (item_id,)).fetchone():
                c.execute("UPDATE fetches SET error = ? WHERE item_id = ?", (message, item_id))
            else:
                c.execute("INSERT INTO fetches (item_id, source, fetched_at, count, error) VALUES (?, 'none', ?, 0, ?)",
                          (item_id, utcnow(), message))

    def fetch_info(self, item_id: str) -> dict | None:
        with self._session() as c:
            r = c.execute("SELECT * FROM fetches WHERE item_id = ?", (item_id,)).fetchone()
            return dict(r) if r else None

    def fetched_ids(self) -> set[str]:
        with self._session() as c:
            return {r[0] for r in c.execute("SELECT item_id FROM fetches WHERE error IS NULL")}

    def references(self, item_id: str) -> list[dict]:
        with self._session() as c:
            return [dict(r) for r in c.execute("SELECT * FROM refs WHERE item_id = ? ORDER BY position", (item_id,))]

    def all_refs(self) -> list[dict]:
        with self._session() as c:
            return [dict(r) for r in c.execute("SELECT * FROM refs ORDER BY item_id, position")]

    def citing(self, doi: str | None, arxiv_id: str | None) -> list[str]:
        """Items whose reference list contains this DOI / arXiv id."""
        if not doi and not arxiv_id:
            return []
        with self._session() as c:
            rows = c.execute("SELECT DISTINCT item_id FROM refs WHERE (doi IS NOT NULL AND doi = ?) "
                             "OR (arxiv_id IS NOT NULL AND arxiv_id = ?)", (doi or "", arxiv_id or ""))
            return [r[0] for r in rows]

    def remove(self, item_ids) -> None:
        with self._session() as c:
            for i in item_ids:
                c.execute("DELETE FROM refs WHERE item_id = ?", (i,))
                c.execute("DELETE FROM fetches WHERE item_id = ?", (i,))


# ------------------------------------------------------------------ service
class Citations:
    """Fetching (now, or queued in the background) and reading the citation graph."""

    def __init__(self, store: CitationStore, papers, settings):
        self.store = store
        self.papers = papers
        self.settings = settings
        self._queue: queue.Queue[str] = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.running: str | None = None
        self.last_error: str | None = None

    # -- fetching
    def fetch_now(self, item_id: str) -> dict:
        item = self.papers.get(item_id)
        doi, arx = identifiers(item)
        try:
            source, refs = fetch(item, email=self.settings.get("contact_email") or "",
                                 openalex_key=self.settings.get("openalex_api_key") or "",
                                 s2_key=self.settings.get("semantic_scholar_api_key") or "")
        except Exception as e:
            self.store.save_error(item_id, str(e))
            raise
        self.store.save(item_id, source, refs, doi=doi, arxiv_id=arx)
        return self.item_view(item_id)

    def enqueue(self, ids) -> int:
        n = 0
        with self._lock:
            for i in ids:
                if i and i not in self._pending:
                    self._pending.add(i)
                    self._queue.put(i)
                    n += 1
            if n and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._loop, name="references", daemon=True)
                self._thread.start()
        return n

    def maybe_enqueue(self, item: dict | None) -> None:
        """After metadata was fetched for an item (if the setting is on and it has an id)."""
        if item and self.settings.get("references_auto") and any(identifiers(item)):
            self.enqueue([item["id"]])

    def _loop(self) -> None:
        while True:
            try:
                item_id = self._queue.get(timeout=2)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._thread = None
                        return
                continue
            self.running = item_id
            try:
                self.fetch_now(item_id)
                self.last_error = None
            except Exception as e:   # keep going with the rest of the queue
                self.last_error = str(e)
                log.info("Fetching references for %s failed: %s", item_id, e)
            finally:
                self.running = None
                with self._lock:
                    self._pending.discard(item_id)
            time.sleep(1.0)   # be gentle with the free APIs

    def wait(self, timeout: float = 30) -> None:
        t = self._thread
        if t:
            t.join(timeout)

    def status(self) -> dict:
        with self._lock:
            return {"queued": len(self._pending), "running": self.running, "last_error": self.last_error}

    # -- reading
    def _library_index(self):
        by_doi, by_arxiv, items = {}, {}, {}
        for p in self.papers.list():
            items[p["id"]] = p
            doi, arx = identifiers(p)
            if doi:
                by_doi.setdefault(doi, p["id"])
            if arx:
                by_arxiv.setdefault(arx, p["id"])
        return by_doi, by_arxiv, items

    @staticmethod
    def _match(ref: dict, by_doi: dict, by_arxiv: dict) -> str | None:
        return (by_doi.get(ref.get("doi")) if ref.get("doi") else None) or \
               (by_arxiv.get(ref.get("arxiv_id")) if ref.get("arxiv_id") else None)

    def item_view(self, item_id: str) -> dict:
        """References of an item (marked when they are in the library) and the library items citing it."""
        item = self.papers.get(item_id)
        by_doi, by_arxiv, items = self._library_index()
        refs = self.store.references(item_id)
        for r in refs:
            m = self._match(r, by_doi, by_arxiv)
            r["library_id"] = m if m != item_id else None   # the cited work is in the library
        doi, arx = identifiers(item)
        cited_by = [{"id": i, "title": items[i].get("title"), "authors": items[i].get("authors"), "year": items[i].get("year")}
                    for i in self.store.citing(doi, arx) if i in items and i != item_id]
        return {"fetch": self.store.fetch_info(item_id), "references": refs, "cited_by": cited_by,
                "can_fetch": bool(doi or arx), "queued": item_id in self._pending}

    def graph(self) -> dict:
        """The whole citation graph: library items and the works they cite, for analysis."""
        by_doi, by_arxiv, items = self._library_index()
        nodes: dict[str, dict] = {}
        for pid, p in items.items():
            doi, arx = identifiers(p)
            nodes[f"item:{pid}"] = {"id": f"item:{pid}", "item_id": pid, "in_library": True, "title": p.get("title"),
                                    "authors": p.get("authors"), "year": p.get("year"), "doi": doi, "arxiv_id": arx,
                                    "citation_key": p.get("citation_key")}
        edges = []
        for r in self.store.all_refs():
            if r["item_id"] not in items:
                continue   # item deleted since
            target = self._match(r, by_doi, by_arxiv)
            if target:
                tid = f"item:{target}"
            else:
                tid = (f"doi:{r['doi']}" if r["doi"] else f"arxiv:{r['arxiv_id']}" if r["arxiv_id"]
                       else f"openalex:{r['openalex_id']}" if r["openalex_id"] else f"ref:{r['item_id']}:{r['position']}")
                if tid not in nodes:
                    nodes[tid] = {"id": tid, "item_id": None, "in_library": False, "title": r["title"] or r["raw"],
                                  "authors": r["authors"], "year": r["year"], "doi": r["doi"], "arxiv_id": r["arxiv_id"]}
            if tid != f"item:{r['item_id']}":
                edges.append({"source": f"item:{r['item_id']}", "target": tid})
        cited = {}
        for e in edges:
            cited[e["target"]] = cited.get(e["target"], 0) + 1
        for n in nodes.values():
            n["cited_by_library"] = cited.get(n["id"], 0)
        fetched = self.store.fetched_ids() & set(items)
        return {"nodes": list(nodes.values()), "edges": edges,
                "stats": {"items": len(items), "items_with_references": len(fetched), "edges": len(edges),
                          "works": len(nodes)}}
