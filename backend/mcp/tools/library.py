"""Tools for finding things: search_library, list_collections."""
from __future__ import annotations

from pathlib import PurePosixPath

from ...library import search as search_mod
from ...library.fields import ITEM_TYPE_LABELS
from .common import drop_empty, snippet_text


class LibraryToolsMixin:
    """search_library, list_collections."""

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
            out["items"] = [drop_empty({**self._brief(p), "match": snippet_text(hits[p["id"]]["fulltext"])
                                    if p["id"] in hits else None}) for p in found[:limit]]
        if scope in ("all", "notes"):
            words = " ".join(t for t, neg, f in search_mod.parse_query(query) if not f and not neg)
            rows = self.s.notes.search(words, limit=limit) if words else []
            info = {n["path"]: n for n in self.s.notes.list()}
            out["notes"] = [drop_empty({"path": r["path"], "title": r["title"],
                                    "link": self._note_link(r["path"], r["title"]),
                                    "type": info.get(r["path"], {}).get("type"),
                                    "paper": info.get(r["path"], {}).get("paper"),
                                    "match": snippet_text(r["snippet"])}) for r in rows]
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
