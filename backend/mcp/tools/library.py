"""Tools for finding things: search_library, list_collections."""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import PurePosixPath

from ...library import search as search_mod
from ...library.fields import ITEM_TYPE_LABELS
from .common import ToolsBase, drop_empty, snippet_text

ABSTRACT_CHARS = 400
FILTERS = "folder, tag, author, title, journal, doi, key, type, year, added, has, is"
SIMILAR_FILTERS = {  # what people type for a filter that has another name here
    "venue": "journal:",
    "publication": "journal:",
    "source": "journal:",
    "authors": "author:",
    "creator": "author:",
    "citekey": "key:",
    "cite": "key:",
    "date": "year: or added:",
    "label": "tag:",
    "keyword": "tag:",
    "collection": "folder:",
    "directory": "folder:",
    "kind": "type:",
    "pdf": "has:pdf",
    "note": "has:note",
}


class LibraryToolsMixin(ToolsBase):
    """search_library, list_collections."""

    def search_library(
        self,
        query: str,
        scope: str = "all",
        fulltext: bool = True,
        limit: int = 20,
        offset: int = 0,
        sort: str = "relevance",
        abstracts: bool = False,
        compact: bool = False,
    ):
        query, query_sort = self._sort_in_query(query)
        if query_sort:
            sort = query_sort
        out: dict = {"query": query}
        ctx = {
            "folder_paths": self.s.folders.paths(),
            "type_labels": ITEM_TYPE_LABELS,
            "field_filters": self.s.plugins.fields.search_filters(),  # plugins' fields: citations:>100
            "tag_names": [t["name"] for t in self.s.tags.list()],
        }
        warnings = self._query_warnings(query, ctx)
        if query_sort:
            warnings.append(f'sort:{query_sort} in the query was taken as the sort argument (sort="{query_sort}").')
        if warnings:
            out["warnings"] = warnings
        if scope in ("all", "items"):
            items = self.s.papers.list()
            mode = "fulltext" if fulltext else "all"
            matcher = self.s.note_service.text_matcher()
            ctx["noted_ids"] = matcher.item_ids()  # for has:note
            if scope != "all":  # a note is evidence about an item only when notes are searched too
                matcher = None
            found, hits = search_mod.search(items, query, mode, self.s.fulltext, ctx, notes=matcher)
            parsed = search_mod.parse_query(query, ctx["field_filters"])
            flat = lambda t: search_mod.normalize(t).replace("-", " ")  # "time-crystalline" has "time crystal"
            terms = [flat(t) for t, neg, f in parsed if not f and not neg]
            wanted_authors = [t for t, neg, f in parsed if f == "author" and not neg]

            def rank(p):
                title = flat(p.get("title"))
                return (-sum(t in title for t in terms), -(int(p.get("year") or 0)))

            found.sort(key=rank)
            if sort == "year":
                found.sort(key=lambda p: -(int(p.get("year") or 0)))
            elif sort == "added":
                found.sort(key=lambda p: p.get("date_added") or "", reverse=True)
            out["items_total"] = len(found)
            if offset + limit < len(found):
                out["next_offset"] = offset + limit
            page = found[offset : offset + limit]
            if compact:  # one short line per item: for listing a folder or a long result
                out["items"] = [self._one_line(p) for p in page]
            else:
                out["items"] = [
                    drop_empty(
                        {
                            **self._brief(p, wanted_authors),
                            "added": (p.get("date_added") or "")[:10] if sort == "added" or "added:" in query else None,
                            "abstract": self._abstract(p) if abstracts else None,
                            "match": snippet_text(hits[p["id"]]["fulltext"]) if p["id"] in hits else None,
                        }
                    )
                    for p in page
                ]
        if scope in ("all", "notes"):
            words = " ".join(  # a phrase keeps its quotes: the notes' index matches it as a phrase
                f'"{t}"' if " " in t else t for t, neg, f in search_mod.parse_query(query) if not f and not neg
            )
            rows = self.s.notes.search(words, limit=offset + limit)[offset:] if words else []
            info = {n["path"]: n for n in self.s.notes.list()}
            out["notes"] = [
                drop_empty(
                    {
                        "path": r["path"],
                        "title": r["title"],
                        "link": self._note_link(r["path"], r["title"]),
                        "type": info.get(r["path"], {}).get("type"),
                        "paper": info.get(r["path"], {}).get("paper"),
                        "match": snippet_text(r["snippet"]),
                    }
                )
                for r in rows
            ]
        return out

    @staticmethod
    def _sort_in_query(query: str) -> tuple[str, str | None]:
        """``sort:added`` written inside the query (it is an argument): taken out, and returned."""
        m = re.search(r"(?:^|\s)sort:(relevance|added|year)(?=\s|$)", query or "")
        return (
            (re.sub(r"\s+", " ", query[: m.start()] + " " + query[m.end() :]).strip(), m.group(1))
            if m
            else (query, None)
        )

    @staticmethod
    def _one_line(p: dict) -> str:
        first = re.split(r";|\band\b", str(p.get("authors") or ""))[0].strip()
        more = " et al." if re.search(r";|\band\b", str(p.get("authors") or "")) else ""
        who = f"{first.split(',')[0] if ',' in first else first.split()[-1] if first else ''}{more}"
        key = p.get("citation_key") or f"item:{p['id']}"
        return f"{key} ({p.get('year') or 'n.d.'}) {who}: {p.get('title') or ''}".replace("  ", " ")

    @staticmethod
    def _abstract(p: dict) -> str | None:
        text = " ".join(str(p.get("abstract") or "").split())
        return text if len(text) <= ABSTRACT_CHARS else text[:ABSTRACT_CHARS].rstrip() + "…"

    def _query_warnings(self, query: str, ctx: dict) -> list[str]:
        """Things in the query that did not do what they look like (the search ran regardless)."""
        out = []
        extra = ctx["field_filters"]
        if unknown := search_mod.unknown_filters(query, extra):
            names = [u.rstrip(":") for u in unknown]
            guess = [f"{n}: → {SIMILAR_FILTERS[n.lower()]}" for n in names if n.lower() in SIMILAR_FILTERS]
            out.append(
                f"{', '.join(unknown)} is not a filter, so it was searched as plain text. "
                + (f"Did you mean {', '.join(guess)}? " if guess else "")
                + f"Filters: {FILTERS}{''.join(', ' + k for k in extra)}."
            )
        if re.search(r"(?:^|\s)(?:OR|AND)(?:\s|$)", query):
            out.append("OR and AND are not operators: every term must match. Run one search per alternative.")
        return out + search_mod.filter_problems(query, ctx)

    def _note_row(self, n: dict) -> dict:
        updated = n.get("updated")
        try:
            st = self.s.vault.abs(n["path"]).stat()
            size = st.st_size
            updated = updated or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))
        except OSError:
            size = None
        return drop_empty(
            {
                "path": n["path"],
                "title": n.get("title"),
                "type": n.get("type"),
                "author": n.get("author"),
                "bytes": size,
                "updated": updated,
            }
        )

    def list_collections(self, include_notes: bool = False):
        s = self.s
        if include_notes:  # the folder, tag and type overview is one call away: this is the notes' inventory
            notes = s.notes.list()
            per_folder: dict[str, int] = {}
            for n in notes:
                parent = str(PurePosixPath(n["path"]).parent)
                if parent != ".":
                    per_folder[parent] = per_folder.get(parent, 0) + 1
            return {
                "notes": len(notes),
                "note_folders": [{"path": k, "notes": v} for k, v in sorted(per_folder.items())],
                "note_list": [self._note_row(n) for n in notes],
                "hint": "Call list_collections without include_notes for folders, tags, item types and search syntax.",
            }
        paths = s.folders.paths()
        folders = [
            {"id": f["id"], "path": "/".join(paths.get(f["id"], [f["name"]])), "items": f["count"]}
            for f in s.folders.tree()
        ]
        folders.sort(key=lambda f: f["path"].casefold())
        notes = s.notes.list()
        note_folders: dict[str, int] = {}
        for n in notes:
            folder = str(PurePosixPath(n["path"]).parent)
            if folder != ".":
                note_folders[folder] = note_folders.get(folder, 0) + 1
        library = s.papers.list()
        return {
            "library": {
                "items": len(library),
                "with_pdf": sum(bool(self._pdfs(p)) for p in library),
                "with_references": len(s.citations.store.fetched_ids() & {p["id"] for p in library}),
                "notes": len(notes),
            },
            "folders": folders,
            "item_types": dict(
                Counter(ITEM_TYPE_LABELS.get(p.get("item_type") or "", p.get("item_type")) for p in library)
            ),
            "tags": [{"name": t["name"], "items": t["count"]} for t in s.tags.list()],
            "smart_folders": [{"name": f["name"], "query": f["query"], "scope": f["mode"]} for f in s.smart.list()],
            "note_folders": [{"path": k, "notes": v} for k, v in sorted(note_folders.items())],
            "search_syntax": 'folder:"A/B" (whole names) tag:x year:2018-2022 (also 2020- or >2015) added:2026-10 (also '
            ">2026-10-15, 14d) author:name has:pdf has:note is:review (a guess) type:book; -term excludes; "
            '"quoted phrase"; terms are ANDed (no OR)',
        }
