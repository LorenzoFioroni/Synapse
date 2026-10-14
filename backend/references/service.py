"""Citation graph: the reference lists of library items, fetched online.

For each item we store the works it cites (in order), whether or not they are in the library.
Together these give a graph — library item -> cited work — where a cited work that is also in
the library is matched by DOI or arXiv id, or else by its title (and year, when both have one).
It is meant as the base for later AI features ("what does this project rest on", "what is cited
a lot that I have not read").

``sources.py`` fetches the lists, ``store.py`` keeps them; ``Citations`` here fetches now or in
the background, and matches cited works to library items.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from ..library.identifiers import identifiers, title_key
from .sources import fetch
from .store import CitationStore

log = logging.getLogger(__name__)


def _year(v) -> int | None:
    try:
        return int(str(v)[:4]) if v else None
    except ValueError:
        return None


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
            source, refs = fetch(
                item,
                email=self.settings.get("contact_email") or "",
                openalex_key=self.settings.get("openalex_api_key") or "",
                s2_key=self.settings.get("semantic_scholar_api_key") or "",
            )
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

    def maybe_enqueue(self, item: dict | None, asked: bool | None = None) -> None:
        """For a newly added item, following Settings → "Fetch references" for new items
        (`asked`: the user's answer where that setting is "ask"). Needs an identifier."""
        if item and self.settings.wants("add_fetch_references", asked) and any(identifiers(item)):
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
            except Exception as e:  # keep going with the rest of the queue
                self.last_error = str(e)
                log.info("Fetching references for %s failed: %s", item_id, e)
            finally:
                self.running = None
                with self._lock:
                    self._pending.discard(item_id)
            time.sleep(1.0)  # be gentle with the free APIs

    def wait(self, timeout: float = 30) -> None:
        t = self._thread
        if t:
            t.join(timeout)

    def status(self) -> dict:
        with self._lock:
            return {"queued": len(self._pending), "running": self.running, "last_error": self.last_error}

    # -- reading
    _index_cache = None  # (signature, index, items)

    def _library_signature(self) -> tuple:
        """Changes whenever an item is added, edited, trashed, restored or deleted."""
        with self.papers.db.session() as c:
            return (self.papers.db.version,) + tuple(
                c.execute("SELECT COUNT(*), MAX(date_modified), COUNT(deleted_at), MAX(rowid) FROM papers").fetchone()
            )

    def _library_index(self):
        """Lookup tables of the library: by DOI, by arXiv id, by title key -> [(id, year)].
        Built once and reused until the library changes: the item pane asks for it every time
        an item is selected, and building it reads the whole library."""
        sig = self._library_signature()
        cached = self._index_cache
        if cached and cached[0] == sig:
            return cached[1], cached[2]
        index, items = self._build_library_index()
        self._index_cache = (sig, index, items)
        return index, items

    def _build_library_index(self):
        by_doi, by_arxiv, by_title, items = {}, {}, {}, {}
        for p in self.papers.list():
            items[p["id"]] = {k: v for k, v in p.items() if k != "abstract"}  # kept in memory: no abstracts
            doi, arx = identifiers(p)
            if doi:
                by_doi.setdefault(doi, p["id"])
            if arx:
                by_arxiv.setdefault(arx, p["id"])
            t = title_key(p.get("title"))
            if t:
                by_title.setdefault(t, []).append((p["id"], _year(p.get("year"))))
        return (by_doi, by_arxiv, by_title), items

    @staticmethod
    def _match(ref: dict, index) -> str | None:
        """The library item a cited work is, by DOI, arXiv id, or title (+ year within one,
        as a preprint and its journal version are often a year apart)."""
        by_doi, by_arxiv, by_title = index
        hit = (by_doi.get(ref.get("doi")) if ref.get("doi") else None) or (
            by_arxiv.get(ref.get("arxiv_id")) if ref.get("arxiv_id") else None
        )
        if hit:
            return hit
        t = title_key(ref.get("title"))
        y = _year(ref.get("year"))
        for pid, py in by_title.get(t, []) if t else []:
            if not y or not py or abs(y - py) <= 1:
                return pid
        return None

    def item_view(self, item_id: str) -> dict:
        """References of an item (marked when they are in the library) and the library items citing it."""
        item = self.papers.get(item_id)
        index, items = self._library_index()
        refs = self.store.references(item_id)
        for r in refs:
            m = self._match(r, index)
            r["library_id"] = m if m != item_id else None  # the cited work is in the library
        doi, arx = identifiers(item)
        citing = {
            r["item_id"] for r in self.store.all_refs() if r["item_id"] != item_id and self._match(r, index) == item_id
        }
        cited_by = [
            {"id": i, "title": items[i].get("title"), "authors": items[i].get("authors"), "year": items[i].get("year")}
            for i in items
            if i in citing
        ]
        return {
            "fetch": self.store.fetch_info(item_id),
            "references": refs,
            "cited_by": cited_by,
            "can_fetch": bool(doi or arx),
            "queued": item_id in self._pending,
        }

    def graph(self) -> dict:
        """The whole citation graph: library items and the works they cite, for analysis."""
        index, items = self._library_index()
        nodes: dict[str, dict] = {}
        for pid, p in items.items():
            doi, arx = identifiers(p)
            nodes[f"item:{pid}"] = {
                "id": f"item:{pid}",
                "item_id": pid,
                "in_library": True,
                "title": p.get("title"),
                "authors": p.get("authors"),
                "year": p.get("year"),
                "doi": doi,
                "arxiv_id": arx,
                "citation_key": p.get("citation_key"),
            }
        edges = []
        for r in self.store.all_refs():
            if r["item_id"] not in items:
                continue  # item deleted since
            target = self._match(r, index)
            if target:
                tid = f"item:{target}"
            else:
                tid = (
                    f"doi:{r['doi']}"
                    if r["doi"]
                    else f"arxiv:{r['arxiv_id']}"
                    if r["arxiv_id"]
                    else f"openalex:{r['openalex_id']}"
                    if r["openalex_id"]
                    else f"ref:{r['item_id']}:{r['position']}"
                )
                if tid not in nodes:
                    nodes[tid] = {
                        "id": tid,
                        "item_id": None,
                        "in_library": False,
                        "title": r["title"] or r["raw"],
                        "authors": r["authors"],
                        "year": r["year"],
                        "doi": r["doi"],
                        "arxiv_id": r["arxiv_id"],
                    }
            if tid != f"item:{r['item_id']}":
                edges.append({"source": f"item:{r['item_id']}", "target": tid})
        cited: dict[str, int] = {}
        for e in edges:
            cited[e["target"]] = cited.get(e["target"], 0) + 1
        for n in nodes.values():
            n["cited_by_library"] = cited.get(n["id"], 0)
        fetched = self.store.fetched_ids() & set(items)
        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "stats": {
                "items": len(items),
                "items_with_references": len(fetched),
                "edges": len(edges),
                "works": len(nodes),
            },
        }
