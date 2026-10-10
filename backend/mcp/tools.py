"""The library tools offered over MCP.

Every tool is a thin layer over the existing backend (search.py, the notes index,
the full-text index, the citation store). Papers are named by citation key (an
item id works too); notes by path or title. Page numbers are PDF page indices
(1 = first page), the same numbers ``[@key, p. N]`` uses in notes, so answers
can cite pages the app opens directly.
"""
from __future__ import annotations

import html
import re
import sqlite3
import time
import unicodedata
from collections import OrderedDict
from pathlib import PurePosixPath

from .. import search as search_mod
from ..fields import FIELDS, ITEM_TYPE_LABELS
from ..notes import frontmatter, parser
from ..notes.service import now_iso, ulid
from ..notes.vault import InvalidPath, NoteExists, slugify
from ..pdftext import extract_pages
from ..references import identifiers
from ..repository import NotFound
from .protocol import Parts, Server, Tool, ToolError

AI_FOLDER = "ai"
MAX_PAGES_PER_READ = 10
MAX_CHARS_PER_READ = 80_000
MAX_NOTE_CHARS = 100_000
MAX_PAPER_NOTE_CHARS = 20_000

INSTRUCTIONS = """\
Tools for the user's research library ("Synapse", a Zotero-like app): papers with metadata and PDFs, \
Markdown notes that cite papers as [@citationkey], and reference lists fetched from Semantic Scholar/OpenAlex/Crossref.

How to work:
- Start from a note (get_note) or a search (search_library). Papers are named by citation key.
- get_paper gives metadata, abstract, the user's paper note and where other notes cite the paper.
- For PDF content, find the relevant pages with search_in_paper, then read_paper those pages. \
Avoid reading whole papers page by page.
- get_references / get_citation_graph follow the citation graph, also to works not in the library \
(they come with DOI/arXiv ids you can look up elsewhere).
- Cite sources in the user's syntax: [@key] or [@key, p. N] with N the PDF page number the tools report. \
Notes are linked with [[Note title]].
- write_note saves a new note (only in the ai/ folder); use it when the user asks to save an answer."""


# ============================================================================ helpers
def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _short_authors(authors: str | None, keep: int = 4) -> str | None:
    if not authors:
        return None
    parts = [a.strip() for a in re.split(r";|\band\b", authors) if a.strip()]
    if len(parts) <= keep + 1:
        return "; ".join(parts)
    return "; ".join(parts[:keep]) + f"; … ({len(parts)} authors)"


def _snippet_text(snip: str) -> str:
    """The app's HTML search snippets as plain text, matches in «»."""
    snip = re.sub(r'<span class="hit-src">(.*?)</span>', r"[\1]", snip or "")
    snip = snip.replace("<mark>", "«").replace("</mark>", "»")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", snip)).split())


def _fold(ch: str) -> str:
    s = unicodedata.normalize("NFKD", ch)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def _folded(text: str) -> tuple[str, list[int]]:
    """Text without case and accents, plus the original index of each folded character
    (so a match in the folded text can be cut from the original)."""
    out, idx = [], []
    for i, ch in enumerate(text):
        f = " " if ch.isspace() else _fold(ch)
        for c in f:
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def parse_pages(spec: str, n: int) -> list[int]:
    """"3", "2-5", "1,4,7-9", "10-" (to the end) -> sorted page numbers (1-based)."""
    pages: set[int] = set()
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", part)
        if not m or part == "-":
            raise ToolError(f"Bad page range '{part}'. Use e.g. \"3\", \"2-5\" or \"1,4,7-9\".")
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else n
        if a < 1 or b < a:
            raise ToolError(f"Bad page range '{part}'.")
        if a > n:
            raise ToolError(f"Page {a} does not exist: the PDF has {n} page{'s' * (n != 1)}.")
        pages.update(range(a, min(b, n) + 1))
    if not pages:
        raise ToolError("No pages given.")
    return sorted(pages)


def _ranges(pages: list[int]) -> str:
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i]) if i == j else f"{pages[i]}-{pages[j]}")
        i = j + 1
    return ",".join(out)


# ============================================================================ tools
class LibraryTools:
    def __init__(self, services):
        self.s = services
        # The MCP process never runs the app's background jobs (attachment sweeps etc.).
        self.s.note_service.on_change = None
        self._page_cache: OrderedDict[tuple, list[str]] = OrderedDict()
        self._last_sync = 0.0

    # ------------------------------------------------------------------ freshness
    def refresh(self) -> None:
        """Pick up notes edited in the app since the last call (cheap: compares file times)."""
        if time.monotonic() - self._last_sync > 2:
            try:
                self.s.notes.sync()
            finally:
                self._last_sync = time.monotonic()

    # ------------------------------------------------------------------ items
    def _items(self) -> dict[str, dict]:
        out = {}
        for view in ("library", "trash"):
            for p in self.s.papers.list(view):
                p["_trashed"] = view == "trash"
                out[p["id"]] = p
        return out

    def _by_key(self, items: dict[str, dict]) -> dict[str, dict]:
        out = {}
        for p in items.values():   # library items win over trashed ones with the same key
            k = p.get("citation_key")
            if k and (k not in out or out[k]["_trashed"]):
                out[k] = p
        return out

    def _paper(self, ref: str, items: dict[str, dict] | None = None) -> dict:
        items = items if items is not None else self._items()
        r = str(ref or "").strip()
        r = re.sub(r"^\[?-?@", "", r).split(",")[0].rstrip("]").strip()
        if r in items:
            return items[r]
        keys = self._by_key(items)
        if r in keys:
            return keys[r]
        folded = {k.casefold(): p for k, p in keys.items()}
        if r.casefold() in folded:
            return folded[r.casefold()]
        raise ToolError(f"No item with citation key or id '{ref}'. Use search_library to find it.")

    def _pdfs(self, p: dict) -> list[dict]:
        out = []
        for a in p.get("attachments") or []:
            path = self.s.papers.attachment_path(a)
            if path and path.suffix.lower() == ".pdf":
                out.append(a)
        return out

    def _brief(self, p: dict) -> dict:
        doi, arx = identifiers(p)
        return _clean({
            "key": p.get("citation_key"), "id": None if p.get("citation_key") else p["id"], "title": p.get("title"),
            "authors": _short_authors(p.get("authors")), "year": p.get("year"),
            "type": ITEM_TYPE_LABELS.get(p.get("item_type") or "", p.get("item_type")),
            "venue": p.get("journal"), "doi": doi, "arxiv": arx,
            "has_pdf": bool(self._pdfs(p)), "in_trash": p.get("_trashed") or None,
        })

    def _label(self, p: dict) -> str:
        return p.get("citation_key") or f"item:{p['id']}"

    # ------------------------------------------------------------------ PDF pages
    def _pages(self, att: dict) -> list[str]:
        path = self.s.papers.attachment_path(att)
        if not path:
            raise ToolError("The PDF file is missing from the library folder.")
        st = path.stat()
        key = (att["id"], st.st_mtime, st.st_size)
        if key in self._page_cache:
            self._page_cache.move_to_end(key)
            return self._page_cache[key]
        pages = extract_pages(path)
        self._page_cache[key] = pages
        while len(self._page_cache) > 16:
            self._page_cache.popitem(last=False)
        return pages

    def _page_count(self, att: dict) -> int | None:
        path = self.s.papers.attachment_path(att)
        if not path:
            return None
        st = path.stat()
        key = (att["id"], st.st_mtime, st.st_size)
        if key in self._page_cache:
            return len(self._page_cache[key])
        try:   # the full-text index already knows it
            with sqlite3.connect(self.s.fulltext.path, timeout=5) as c:
                row = c.execute("SELECT pages, mtime, size FROM documents WHERE attachment_id = ?", (att["id"],)).fetchone()
            if row and row[1] == st.st_mtime and row[2] == st.st_size and row[0]:
                return row[0]
        except sqlite3.Error:
            pass
        try:
            import pypdf
            return len(pypdf.PdfReader(str(path)).pages)
        except Exception:
            return None

    def _pdf_for(self, p: dict, attachment: int | None) -> dict:
        pdfs = self._pdfs(p)
        if attachment is not None:
            for a in pdfs:
                if a["id"] == attachment:
                    return a
            raise ToolError(f"Attachment {attachment} is not a PDF of '{self._label(p)}'. "
                            f"Its PDFs: {[a['id'] for a in pdfs] or 'none'}.")
        if not pdfs:
            doi, arx = identifiers(p)
            where = ", ".join(x for x in (f"DOI {doi}" if doi else "", f"arXiv {arx}" if arx else "") if x)
            raise ToolError(f"'{self._label(p)}' has no PDF in the library."
                            + (f" It can be found online via {where}." if where else "")
                            + " get_paper shows its abstract.")
        return pdfs[0]

    # ------------------------------------------------------------------ notes
    def _note_path(self, ref: str) -> str:
        r = str(ref or "").strip()
        r = re.sub(r"^!?\[\[|\]\]$", "", r).split("|")[0].split("#")[0].strip()
        if not r:
            raise ToolError("Give a note path or title.")
        try:
            rel = self.s.vault.clean_rel(r if r.lower().endswith(".md") else r + ".md")
            if self.s.vault.exists(rel):
                return rel
        except InvalidPath:
            pass
        found = self.s.notes.resolve(r)
        if found:
            return found[0]
        by_title = [n["path"] for n in self.s.notes.list() if (n.get("title") or "").casefold() == r.casefold()]
        if by_title:
            return sorted(by_title, key=len)[0]
        hints = [f"{h['path']} ({h['title']})" for h in self.s.notes.search(r, limit=5)]
        raise ToolError(f"No note '{ref}'." + (" Similar: " + "; ".join(hints) if hints else
                                              " Use search_library with scope 'notes' to find it."))

    # =================================================================== the tools
    def search_library(self, query: str, scope: str = "all", fulltext: bool = True, limit: int = 20):
        out: dict = {"query": query}
        if scope in ("all", "items"):
            items = self.s.papers.list()
            mode = "fulltext" if fulltext else "all"
            matcher = self.s.note_service.text_matcher()
            ctx = {"folder_paths": self.s.folders.paths(), "type_labels": ITEM_TYPE_LABELS}
            found, hits = search_mod.search(items, query, mode, self.s.fulltext, ctx, notes=matcher)
            terms = [search_mod.normalize(t) for t, neg, f in search_mod.parse_query(query) if not f and not neg]

            def rank(p):
                title = search_mod.normalize(p.get("title"))
                return (-sum(t in title for t in terms), -(int(p.get("year") or 0)))
            found.sort(key=rank)
            out["items_total"] = len(found)
            out["items"] = [_clean({**self._brief(p), "match": _snippet_text(hits[p["id"]]["fulltext"])
                                    if p["id"] in hits else None}) for p in found[:limit]]
        if scope in ("all", "notes"):
            words = " ".join(t for t, neg, f in search_mod.parse_query(query) if not f and not neg)
            rows = self.s.notes.search(words, limit=limit) if words else []
            info = {n["path"]: n for n in self.s.notes.list()}
            out["notes"] = [_clean({"path": r["path"], "title": r["title"],
                                    "type": info.get(r["path"], {}).get("type"),
                                    "paper": info.get(r["path"], {}).get("paper"),
                                    "match": _snippet_text(r["snippet"])}) for r in rows]
        return out

    def list_collections(self):
        s = self.s
        paths = s.folders.paths()
        folders = [{"id": f["id"], "path": "/".join(paths.get(f["id"], [f["name"]])), "items": f["count"]}
                   for f in s.folders.tree()]
        folders.sort(key=lambda f: f["path"].casefold())
        notes = s.notes.list()
        note_folders: dict[str, int] = {}
        for n in notes:
            folder = str(PurePosixPath(n["path"]).parent)
            if folder != ".":
                note_folders[folder] = note_folders.get(folder, 0) + 1
        library = s.papers.list()
        return {
            "library": {"items": len(library), "with_pdf": sum(bool(self._pdfs(p)) for p in library),
                        "with_references": len(s.citations.store.fetched_ids() & {p["id"] for p in library}),
                        "notes": len(notes)},
            "folders": folders,
            "tags": [{"name": t["name"], "items": t["count"]} for t in s.tags.list()],
            "smart_folders": [{"name": f["name"], "query": f["query"], "scope": f["mode"]} for f in s.smart.list()],
            "note_folders": [{"path": k, "notes": v} for k, v in sorted(note_folders.items())],
            "search_syntax": "folder:\"A/B\" tag:x year:2018-2022 author:name has:pdf type:book; -term excludes; "
                             "\"quoted phrase\"; terms are ANDed",
        }

    def get_note(self, note: str):
        path = self._note_path(note)
        got = self.s.note_service.get(path)
        info = got["info"] or {}
        links = self.s.note_service.links(path)
        items = self._items()
        keys = self._by_key(items)
        cited = []
        for c in links["cited"]:
            p = keys.get(c["key"])
            cites = [_clean({"line": l["line"], "locator": l.get("locator"), "page": l.get("page"),
                             "context": l.get("context")})
                     for l in links["outgoing"] if l["kind"] == "cite" and l["target"] == c["key"]]
            cited.append(_clean({"key": c["key"], "paper": self._brief(p) if p else None,
                                 "not_in_library": None if p else True, "citations": cites}))
        notes_out = [_clean({"target": l["target"], "resolves_to": l.get("resolved"), "line": l["line"],
                             "embed": l["kind"] == "embed" or None})
                     for l in links["outgoing"] if l["kind"] in ("note", "embed")]
        backlinks = [_clean({"path": b["path"], "title": b.get("source_title"), "line": b["line"],
                             "context": b.get("context")}) for b in links["backlinks"]]
        paper = keys.get(info.get("paper")) if info.get("paper") else None
        mtime = self.s.vault.abs(path).stat().st_mtime
        summary = _clean({
            "path": path, "title": info.get("title"), "type": info.get("type"), "author": info.get("author"),
            "tags": info.get("tags"), "created": info.get("created"),
            "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
            "about_paper": self._brief(paper) if paper else info.get("paper"),
            "outline": [("#" * h["level"]) + " " + h["text"] for h in info.get("headings") or []],
            "cited_papers": cited, "links_to_notes": notes_out, "linked_from": backlinks,
        })
        text = got["text"]
        if len(text) > MAX_NOTE_CHARS:
            text = text[:MAX_NOTE_CHARS] + f"\n\n[… note cut at {MAX_NOTE_CHARS} characters of {len(got['text'])}]"
        return Parts([summary, f"Text of {path}:\n\n{text}"])

    def get_paper(self, paper: str):
        items = self._items()
        p = self._paper(paper, items)
        skip = {"date_modified", "citation_key", "item_type", "doi"}
        meta = {f.key: p.get(f.key) for f in FIELDS if f.key not in skip}
        doi, arx = identifiers(p)
        paths = self.s.folders.paths()
        key = p.get("citation_key") or ""
        out = _clean({
            "key": key or None, "id": p["id"],
            "type": ITEM_TYPE_LABELS.get(p.get("item_type") or "", p.get("item_type")),
            "doi": doi, "arxiv": arx, **_clean(meta),
            "tags": p.get("tags"), "folders": ["/".join(paths.get(f, [])) for f in p.get("folder_ids") or []],
            "in_trash": p.get("_trashed") or None,
            "pdfs": [_clean({"attachment": a["id"], "name": a.get("name"), "pages": self._page_count(a)})
                     for a in self._pdfs(p)],
        })
        note_text = None
        if key:
            pnotes = self.s.notes.paper_notes(key)
            if pnotes:
                note_path = pnotes[0]["path"]
                out["paper_note"] = note_path
                try:
                    note_text = self.s.vault.read(note_path)[0]
                except Exception:
                    note_text = None
            mine = {n["path"] for n in pnotes}
            out["cited_in_notes"] = [_clean({"path": c["path"], "title": c.get("source_title"), "line": c["line"],
                                             "locator": c.get("locator"), "context": c.get("context")})
                                     for c in self.s.notes.citing(key) if c["path"] not in mine]
        try:
            view = self.s.citations.item_view(p["id"])
        except NotFound:
            view = None
        if view:
            f = view["fetch"] or {}
            out["references"] = _clean({
                "stored": len(view["references"]),
                "in_library": sum(1 for r in view["references"] if r.get("library_id")),
                "source": f.get("source"), "fetched_at": f.get("fetched_at"), "error": f.get("error"),
                "never_fetched": True if not f else None})
            out["cited_by_in_library"] = [self._label(items[c["id"]]) for c in view["cited_by"] if c["id"] in items]
        if note_text is None:
            return _clean(out)
        if len(note_text) > MAX_PAPER_NOTE_CHARS:
            note_text = note_text[:MAX_PAPER_NOTE_CHARS] + "\n\n[… cut; read the rest with get_note]"
        return Parts([_clean(out), f"The user's paper note ({out['paper_note']}):\n\n{note_text}"])

    def search_in_paper(self, paper: str, query: str, max_results: int = 8, attachment: int | None = None):
        p = self._paper(paper)
        att = self._pdf_for(p, attachment)
        pages = self._pages(att)
        if not any(t.strip() for t in pages):
            raise ToolError(f"No text could be extracted from the PDF of '{self._label(p)}' (a scan?).")
        terms = [t for t, neg, f in search_mod.parse_query(query) if not neg]
        terms = [search_mod.normalize(t) for t in terms if search_mod.normalize(t)]
        if not terms:
            raise ToolError("Give some words to search for.")
        phrase = search_mod.normalize(query.replace('"', "")) if len(terms) > 1 else None
        scored = []
        for n, text in enumerate(pages, 1):
            folded, idx = _folded(text)
            hits = {t: [m.start() for m in re.finditer(re.escape(t), folded)] for t in terms}
            matched = [t for t in terms if hits[t]]
            if not matched:
                continue
            phrase_hits = [m.start() for m in re.finditer(re.escape(phrase), folded)] if phrase else []
            total = sum(len(v) for v in hits.values())
            scored.append(((len(matched), len(phrase_hits), total), n, matched, phrase_hits, hits, text, idx))
        scored.sort(key=lambda s: (s[0], -s[1]), reverse=True)
        results = []
        for score, n, matched, phrase_hits, hits, text, idx in scored[:max_results]:
            starts = phrase_hits[:1] + [hits[t][0] for t in matched]
            snippets, used = [], []
            for st in starts:
                pos = idx[min(st, len(idx) - 1)]
                if any(abs(pos - u) < 200 for u in used):
                    continue
                used.append(pos)
                a, b = max(0, pos - 160), min(len(text), pos + 220)
                snippets.append(("…" if a else "") + " ".join(text[a:b].split()) + ("…" if b < len(text) else ""))
                if len(snippets) == 3:
                    break
            results.append({"page": n, "matched": matched, "occurrences": score[2], "snippets": snippets})
        out = {"paper": self._label(p), "title": p.get("title"), "attachment": att["id"],
               "pages_total": len(pages), "pages_matching": len(scored), "results": results}
        if not results:
            out["hint"] = "No page contains these words. Try other words or synonyms."
        return out

    def read_paper(self, paper: str, pages: str = "1-2", attachment: int | None = None):
        p = self._paper(paper)
        att = self._pdf_for(p, attachment)
        texts = self._pages(att)
        if not texts:
            raise ToolError(f"No text could be extracted from the PDF of '{self._label(p)}'.")
        want = parse_pages(pages, len(texts))
        given, chars = [], 0
        parts = [f"{self._label(p)} — {p.get('title') or ''} (PDF, {len(texts)} pages). "
                 f"Cite as [@{p.get('citation_key') or '?'}, p. N] with N the page number below."]
        for n in want:
            t = texts[n - 1].strip()
            if len(given) >= MAX_PAGES_PER_READ or (given and chars + len(t) > MAX_CHARS_PER_READ):
                break
            parts.append(f"=== Page {n} ===\n{t or '[no text on this page]'}")
            given.append(n)
            chars += len(t)
        rest = [n for n in want if n not in given]
        if rest:
            parts.append(f"[Not included (at most {MAX_PAGES_PER_READ} pages / {MAX_CHARS_PER_READ} characters "
                         f"per call): pages {_ranges(rest)}. Ask for them in another call if needed.]")
        return "\n\n".join(parts)

    def get_references(self, paper: str, direction: str = "cites", in_library_only: bool = False, limit: int = 200):
        items = self._items()
        p = self._paper(paper, items)
        view = self.s.citations.item_view(p["id"])
        out: dict = {"paper": self._label(p), "title": p.get("title")}
        f = view["fetch"]
        if direction in ("cites", "both"):
            if not f:
                out["references_note"] = ("Its reference list was never fetched. "
                                          "In the app: item menu → Fetch References (needs a DOI or arXiv id)."
                                          if view["can_fetch"] else
                                          "No reference list: the item has no DOI or arXiv id to look it up.")
            elif f.get("error") and not view["references"]:
                out["references_note"] = f"Fetching the reference list failed: {f['error']}"
            refs = []
            for r in view["references"]:
                lib = items.get(r.get("library_id")) if r.get("library_id") else None
                if lib:
                    refs.append({"in_library": True, **self._brief(lib)})
                elif not in_library_only:
                    refs.append(_clean({"in_library": False, "title": r.get("title") or r.get("raw"),
                                        "authors": _short_authors(r.get("authors")), "year": r.get("year"),
                                        "venue": r.get("venue"), "doi": r.get("doi"), "arxiv": r.get("arxiv_id")}))
            out["references_total"] = len(view["references"])
            out["references_in_library"] = sum(1 for r in view["references"] if r.get("library_id"))
            if f:
                out["source"] = f.get("source")
            out["references"] = refs[:limit]
            if len(refs) > limit:
                out["references_cut"] = f"showing {limit} of {len(refs)}"
        if direction in ("cited_by", "both"):
            out["cited_by_in_library"] = [self._brief(items[c["id"]]) for c in view["cited_by"] if c["id"] in items]
            out["cited_by_note"] = "Only items of the library are known to cite it (works outside the library are not tracked)."
        return out

    def get_citation_graph(self, papers: list | None = None, depth: int = 1, limit: int = 40):
        g = self.s.citations.graph()
        items = {p["id"]: p for p in self.s.papers.list()}
        nodes = {n["id"]: n for n in g["nodes"]}

        def label(nid: str) -> str:
            n = nodes[nid]
            return self._label(items[n["item_id"]]) if n["in_library"] and n["item_id"] in items else nid

        def describe(nid: str) -> dict:
            n = nodes[nid]
            if n["in_library"] and n["item_id"] in items:
                return self._brief(items[n["item_id"]])
            return _clean({"id": nid, "title": n.get("title"), "authors": _short_authors(n.get("authors")),
                           "year": n.get("year"), "doi": n.get("doi"), "arxiv": n.get("arxiv_id")})

        citers: dict[str, list[str]] = {}
        for e in g["edges"]:
            citers.setdefault(e["target"], []).append(e["source"])

        if not papers:
            missing = sorted((n for n in nodes.values() if not n["in_library"] and n["cited_by_library"] >= 2),
                             key=lambda n: -n["cited_by_library"])
            cited_lib = sorted((n for n in nodes.values() if n["in_library"] and n["cited_by_library"]),
                               key=lambda n: -n["cited_by_library"])
            return {
                "stats": g["stats"],
                "most_cited_not_in_library": [{**describe(n["id"]), "cited_by": [label(c) for c in citers.get(n["id"], [])]}
                                              for n in missing[:limit]],
                "most_cited_in_library": [{**describe(n["id"]), "cited_by_library": n["cited_by_library"]}
                                          for n in cited_lib[:limit]],
                "note": "Only items whose reference lists were fetched contribute edges.",
            }

        all_items = self._items()
        seeds = []
        for ref in papers:
            p = self._paper(ref, all_items)
            seeds.append(f"item:{p['id']}")
        adj: dict[str, set[str]] = {}
        for e in g["edges"]:
            adj.setdefault(e["source"], set()).add(e["target"])
            adj.setdefault(e["target"], set()).add(e["source"])
        dist = {s: 0 for s in seeds if s in nodes}
        frontier = list(dist)
        for d in range(1, depth + 1):
            nxt = []
            for nid in frontier:
                for m in adj.get(nid, ()):
                    if m not in dist:
                        dist[m] = d
                        nxt.append(m)
            frontier = nxt
        cap = 400
        keep = sorted(dist, key=lambda n: (dist[n], not nodes[n]["in_library"], n))[:cap]
        kept = set(keep)
        edges = [(e["source"], e["target"]) for e in g["edges"] if e["source"] in kept and e["target"] in kept]
        in_sub_citers: dict[str, set[str]] = {}
        for a, b in edges:
            in_sub_citers.setdefault(b, set()).add(a)
        shared = sorted(((n, c) for n, c in in_sub_citers.items() if len(c) >= 2 and n not in seeds),
                        key=lambda x: -len(x[1]))
        no_refs = [label(s) for s in seeds if s in nodes and s not in self.s.citations.store.fetched_ids()
                   and not any(e[0] == s for e in edges)]
        return _clean({
            "seeds": [label(s) for s in seeds],
            "depth": depth,
            "nodes": [{**describe(n), "node": label(n), "distance": dist[n]} for n in keep],
            "edges": [f"{label(a)} -> {label(b)}" for a, b in edges][:1000],
            "edges_meaning": "a -> b: a cites b",
            "cited_by_several": [{"node": label(n), "title": nodes[n].get("title"), "in_library": nodes[n]["in_library"],
                                  "cited_by": sorted(label(c) for c in cs)} for n, cs in shared[:limit]],
            "truncated": f"kept {cap} of {len(dist)} nodes" if len(dist) > cap else None,
            "seeds_without_reference_list": no_refs or None,
        })

    def write_note(self, title: str, text: str, folder: str = "", tags: list | None = None):
        title = " ".join(str(title).split())
        sub = "/".join(slugify(x) for x in re.split(r"[/\\]+", folder or "") if x.strip() and x.strip() not in (".", ".."))
        base = f"{AI_FOLDER}/{sub}" if sub else AI_FOLDER
        name = slugify(title, fallback="AI note")
        path = f"{base}/{name}.md"
        n = 2
        while self.s.vault.exists(path):
            path = f"{base}/{name} {n}.md"
            n += 1
        body = text if text.endswith("\n") else text + "\n"
        _, fm_body, _ = frontmatter.split(body)
        if not re.search(r"^\s*#\s", fm_body):
            body = f"# {title}\n\n" + body
        meta = {"id": ulid(), "title": title, "type": "note", "author": "ai", "created": now_iso()}
        if tags:
            meta["tags"] = [str(t) for t in tags]
        content = frontmatter.set_fields(body, meta)
        try:
            self.s.note_service.save(path, content, None)
        except NoteExists:
            raise ToolError(f"A note already exists at {path}; try another title.")
        parsed = parser.parse(content, fallback_title=title)
        keys = self._by_key(self._items())
        unknown = sorted({l.target for l in parsed.links if l.kind == "cite" and l.target not in keys})
        unresolved = sorted({l.target for l in parsed.links if l.kind in ("note", "embed")
                             and not self.s.notes.resolve(l.target, from_path=path)})
        return _clean({"saved": path, "link": f"[[{PurePosixPath(path).stem}]]",
                       "citations": len([l for l in parsed.links if l.kind == "cite"]),
                       "unknown_citation_keys": unknown or None,
                       "unresolved_note_links": unresolved or None})


# ============================================================================ schemas
def build_server(services, version: str = "0.1.0") -> Server:
    t = LibraryTools(services)
    server = Server(name="synapse", version=version, instructions=INSTRUCTIONS, before_call=t.refresh)
    paper_arg = {"type": "string", "description": "Citation key of the item (e.g. \"smith2021\"; \"[@smith2021]\" also works) or its item id."}
    att_arg = {"type": "integer", "description": "Attachment id when the item has several PDFs (see get_paper). Default: the first PDF."}

    server.add(Tool(
        "search_library", "Search the library",
        "Search the user's library: items (metadata, PDF full text and what notes say about them) and notes. "
        "Supports filters: folder:\"Physics/Transport\" tag:x year:2018-2022 author:name has:pdf type:book, "
        "-term to exclude, \"quoted phrases\"; all terms must match. Returns items with citation keys and "
        "a matching snippet, and notes with paths. Use list_collections to see folder and tag names.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "Words and/or filters."},
            "scope": {"type": "string", "enum": ["all", "items", "notes"], "description": "What to search (default: all)."},
            "fulltext": {"type": "boolean", "description": "Also search the text of PDFs (default true)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Maximum results per kind (default 20)."},
        }, "required": ["query"]}, t.search_library))
    server.add(Tool(
        "list_collections", "List folders, tags and notes folders",
        "The library's folders (with item counts), tags, smart folders (saved searches) and notes folders, "
        "plus overall counts. Use the names in search_library filters.",
        {"type": "object", "properties": {}}, t.list_collections))
    server.add(Tool(
        "get_note", "Read a note",
        "Read a Markdown note by path (\"projects/crispr.md\") or title. Returns its text, the papers it cites "
        "(resolved to library items, with the line and page locator of each citation), its links to other notes "
        "and the notes linking to it. In notes, [@key] cites a paper and [[Title]] links a note.",
        {"type": "object", "properties": {
            "note": {"type": "string", "description": "Path in the notes folder, file name, or title."},
        }, "required": ["note"]}, t.get_note))
    server.add(Tool(
        "get_paper", "Get a paper's details",
        "Metadata and abstract of a library item, its PDFs (with page counts), the user's paper note about it, "
        "the lines of other notes that cite it, and a summary of its stored reference list.",
        {"type": "object", "properties": {"paper": paper_arg}, "required": ["paper"]}, t.get_paper))
    server.add(Tool(
        "search_in_paper", "Search inside a paper's PDF",
        "Find the pages of a paper's PDF that contain some words (case and accents ignored), best pages first, "
        "with snippets. Use it to locate the relevant pages, then read them with read_paper.",
        {"type": "object", "properties": {
            "paper": paper_arg,
            "query": {"type": "string", "description": "Words to look for; \"quoted phrases\" stay together."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Maximum pages to return (default 8)."},
            "attachment": att_arg,
        }, "required": ["paper", "query"]}, t.search_in_paper))
    server.add(Tool(
        "read_paper", "Read pages of a paper",
        f"The text of some pages of a paper's PDF, marked by page number (at most {MAX_PAGES_PER_READ} pages per call). "
        "Page numbers are PDF pages counted from 1, the numbers to use in [@key, p. N].",
        {"type": "object", "properties": {
            "paper": paper_arg,
            "pages": {"type": "string", "description": "Pages to read: \"3\", \"2-5\", \"1,4,7-9\" or \"10-\" (to the end). Default \"1-2\"."},
            "attachment": att_arg,
        }, "required": ["paper"]}, t.read_paper))
    server.add(Tool(
        "get_references", "Get a paper's references",
        "The works a paper cites (its fetched reference list; library items are marked and come with citation keys, "
        "others with DOI/arXiv ids) and/or the library items that cite it.",
        {"type": "object", "properties": {
            "paper": paper_arg,
            "direction": {"type": "string", "enum": ["cites", "cited_by", "both"],
                          "description": "\"cites\": its references (default); \"cited_by\": library items citing it; \"both\"."},
            "in_library_only": {"type": "boolean", "description": "Only list references that are in the library (default false)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Maximum references listed (default 200)."},
        }, "required": ["paper"]}, t.get_references))
    server.add(Tool(
        "get_citation_graph", "Explore the citation graph",
        "With papers: the citation neighbourhood around them (1 or 2 steps, both directions), with the works "
        "cited by several papers in it (shared foundations, possibly missing from the library). Without papers: "
        "a library-wide overview, the most cited works not in the library and the most cited library items.",
        {"type": "object", "properties": {
            "papers": {"type": "array", "items": {"type": "string"}, "description": "Citation keys to start from (optional)."},
            "depth": {"type": "integer", "minimum": 1, "maximum": 2, "description": "Steps from the starting papers (default 1)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Length of the ranked lists (default 40)."},
        }}, t.get_citation_graph))
    server.add(Tool(
        "write_note", "Save a new note",
        "Save a new Markdown note in the notes folder \"ai/\" (marked author: ai). Never overwrites: a free file "
        "name is chosen. Cite papers as [@key] or [@key, p. N] and link notes as [[Title]]; they become clickable "
        "in the app. Returns the path and any citation keys that are not in the library. Use only when the user "
        "asks to save something.",
        {"type": "object", "properties": {
            "title": {"type": "string", "description": "Title of the note (also its file name)."},
            "text": {"type": "string", "description": "Markdown body. A \"# Title\" heading is added if there is none."},
            "folder": {"type": "string", "description": "Optional subfolder inside ai/ (e.g. \"crispr\")."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags."},
        }, "required": ["title", "text"]}, t.write_note, read_only=False))
    return server
