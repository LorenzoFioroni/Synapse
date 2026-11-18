"""Tools that change the library: add_paper, update_paper, organize_papers, trash_papers,
fetch_references, find_pdf, add_highlight; and get_annotations, which reads.

The changing ones are offered only while Settings → AI Access → "AI apps may change the library" is on
(``LIBRARY_EDITS``, off by default). Nothing here deletes for good: items go to the Trash, and a tag or
folder taken off an item can be put back.
"""

from __future__ import annotations

import difflib

from ...library.annotations import AnnotationError
from ...library.fields import EDITABLE_KEYS, FIELD_MAP
from ...library.organize import OrganizeError
from ...library.search import normalize
from ...pdf import regions as pdfregions
from ...pdf import render as pdfrender
from ..protocol import ToolError
from .common import ToolsBase, check_page, drop_empty, sentence

LIBRARY_EDITS = "ai_library_edits"  # the setting that offers the tools changing the library
# The ones a companion's AI tool may get, when its companion allows it too (docs/companion.md)
COMPANION_EDITS = frozenset({"add_paper", "find_pdf", "organize_papers", "add_highlight", "trash_papers"})
MAX_PAPERS_PER_CHANGE = 200
# A changed key is rewritten in every note that cites it: left to the user, in the app.
FIELDS_FOR_AI = [k for k in EDITABLE_KEYS if k != "citation_key"]


class EditToolsMixin(ToolsBase):
    """add_paper, update_paper, organize_papers, trash_papers, fetch_references, find_pdf, get_annotations,
    add_highlight."""

    def _several(self, papers: list, *, trashed: bool = False) -> list[dict]:
        """The library items named by ``papers`` (``trashed``: the ones in the Trash instead)."""
        if not papers:
            raise ToolError("Give papers: a list of citation keys, ids, DOIs or arXiv ids.")
        if len(papers) > MAX_PAPERS_PER_CHANGE:
            raise ToolError(f"At most {MAX_PAPERS_PER_CHANGE} papers per call ({len(papers)} given).")
        items = self._items()
        found: list[dict] = []
        wrong: list[str] = []
        for ref in papers:
            p = self._paper(str(ref), items)
            if bool(p["_trashed"]) != trashed:
                wrong.append(self._label(p))
            elif p["id"] not in {x["id"] for x in found}:
                found.append(p)
        if wrong:
            raise ToolError(
                f"{', '.join(wrong)} {'is' if len(wrong) == 1 else 'are'} "
                + ("not in the Trash." if trashed else "in the Trash: restore first (trash_papers with restore=true).")
                + " Nothing was changed."
            )
        return found

    def _folder_id(self, path: str, create: bool = False) -> tuple[int, bool]:
        """(id, created) of the folder at ``path`` ("Physics/Transport"; a name alone when only one folder has it)."""
        want = [normalize(x) for x in str(path).split("/") if x.strip()]
        if not want:
            raise ToolError("Give the folder's path, e.g. 'Physics/Transport'.")
        paths = self.s.folders.paths()
        exact = [fid for fid, names in paths.items() if [normalize(n) for n in names] == want]
        if exact:
            return exact[0], False
        if len(want) == 1:
            named = [fid for fid, names in paths.items() if normalize(names[-1]) == want[0]]
            if len(named) == 1:
                return named[0], False
            if len(named) > 1:
                raise ToolError(
                    f"{len(named)} folders are named '{path}'; give the whole path: "
                    + "; ".join("/".join(paths[f]) for f in named)
                )
        if not create:
            known = ["/".join(names) for names in paths.values()]
            close = difflib.get_close_matches(str(path), known, n=3, cutoff=0.5)
            raise ToolError(
                f"No folder '{path}'."
                + (f" Did you mean {', '.join(close)}?" if close else "")
                + " list_collections shows the folders; create_folders=true makes a missing one."
            )
        parent: int | None = None
        made = False
        parts = [x.strip() for x in str(path).split("/") if x.strip()]
        for depth in range(1, len(parts) + 1):
            have = [fid for fid, names in paths.items() if [normalize(n) for n in names] == want[:depth]]
            if have:
                parent = have[0]
                continue
            try:
                parent = self.s.folders.create(parts[depth - 1], parent)["id"]
            except OrganizeError as e:
                raise ToolError(str(e)) from e
            made = True
            paths = self.s.folders.paths()
        assert parent is not None
        return parent, made

    # ------------------------------------------------------------------ items
    def add_paper(
        self,
        identifier: str = "",
        fields: dict | None = None,
        folder: str = "",
        tags: list | None = None,
        fetch_references: bool | None = None,
    ):
        identifier = str(identifier or "").strip()
        fields = dict(fields or {})
        if not identifier and not fields:
            raise ToolError("Give identifier (a DOI, arXiv id or ISBN) or fields ({title, authors, year, …}).")
        unknown = sorted(set(fields) - set(FIELDS_FOR_AI))
        if unknown:
            raise ToolError(f"Unknown fields: {', '.join(unknown)}. Fields: {', '.join(FIELDS_FOR_AI)}.")
        folder_id = self._folder_id(folder)[0] if folder else None
        tags = [str(t) for t in tags or []]
        if identifier:
            res = self.s.add_by_identifier(
                identifier, folder_id=folder_id, tags=tags, fetch_references=fetch_references
            )
            if res["status"] == "failed":
                raise ToolError(
                    f"'{identifier}' could not be looked up: {sentence(res['message'])} Nothing was added. "
                    "Check the identifier, or add the item by hand with fields={title, authors, year, …}."
                )
            if res["status"] == "duplicate":
                return {
                    "already_in_library": self._brief({**self.s.papers.get(res["item_id"]), "_trashed": False}),
                    "note": "Nothing was added: the library has this work.",
                }
            item = res["item"]
            if extra := {k: v for k, v in fields.items() if not item.get(k)}:  # the lookup's values win
                item = self.s.papers.update(item["id"], extra)
        else:
            if not str(fields.get("title") or "").strip():
                raise ToolError("An item added by hand needs at least a title.")
            if dup := self.s.papers.find_duplicate(fields):
                return {
                    "already_in_library": self._brief({**self.s.papers.get(dup), "_trashed": False}),
                    "note": "Nothing was added: the library has this work.",
                }
            item = self.s.papers.create({**fields, "folder_id": folder_id, "tags": tags})
            self.s.citations.maybe_enqueue(item, fetch_references)
        return drop_empty(
            {
                "added": self._brief({**item, "_trashed": False}),
                "folder": folder or None,
                "tags": item.get("tags"),
                "next": "find_pdf looks for an open-access PDF of it; update_paper corrects its fields.",
            }
        )

    def update_paper(self, paper: str, fields: dict):
        p = self._several([paper])[0]
        if not isinstance(fields, dict) or not fields:
            raise ToolError(f"Give fields: an object of the fields to change. Fields: {', '.join(FIELDS_FOR_AI)}.")
        if "citation_key" in fields:
            raise ToolError(
                "The citation key is changed in the app (Edit → citation key): every note citing it is rewritten."
            )
        unknown = sorted(set(fields) - set(FIELDS_FOR_AI))
        if unknown:
            close = {k: difflib.get_close_matches(k, FIELDS_FOR_AI, n=1) for k in unknown}
            raise ToolError(
                "Unknown fields: "
                + ", ".join(f"{k} (did you mean {c[0]}?)" if c else k for k, c in close.items())
                + f". Fields: {', '.join(FIELDS_FOR_AI)}. Tags and folders are changed with organize_papers."
            )
        after = self.s.papers.update(p["id"], fields)
        changed = {
            k: {"before": p.get(k), "after": after.get(k)} for k in fields if (p.get(k) or "") != (after.get(k) or "")
        }
        if not changed:
            return {"paper": self._label(after), "note": "Nothing changed: the fields already had these values."}
        return {
            "paper": self._label(after),
            "changed": {FIELD_MAP[k].label if k in FIELD_MAP else k: v for k, v in changed.items()},
            "undo": "The app keeps no history of an item's fields: to take this back, call update_paper with the "
            "'before' values.",
        }

    def organize_papers(
        self,
        papers: list,
        add_tags: list | None = None,
        remove_tags: list | None = None,
        add_folders: list | None = None,
        remove_folders: list | None = None,
        create_folders: bool = False,
    ):
        found = self._several(papers)
        if not (add_tags or remove_tags or add_folders or remove_folders):
            raise ToolError("Nothing to change: give add_tags, remove_tags, add_folders or remove_folders.")
        ids = [p["id"] for p in found]
        created: list[str] = []
        add_ids = []
        for path in add_folders or []:
            fid, made = self._folder_id(str(path), create=create_folders)
            add_ids.append(fid)
            if made:
                created.append(str(path))
        remove_ids = [self._folder_id(str(path))[0] for path in remove_folders or []]
        try:
            self.s.tags.add(ids, [str(t) for t in add_tags or []])
            self.s.tags.remove(ids, [str(t) for t in remove_tags or []])
            self.s.folders.change_items(ids, add=add_ids, remove=remove_ids)
        except OrganizeError as e:
            raise ToolError(str(e)) from e
        return drop_empty(
            {
                "papers": [self._label(p) for p in found],
                "tags_added": add_tags,
                "tags_removed": remove_tags,
                "added_to_folders": add_folders,
                "removed_from_folders": remove_folders,
                "folders_created": created,
                "note": "An item can be in several folders; taking it out of one never deletes it.",
            }
        )

    def trash_papers(self, papers: list, restore: bool = False):
        found = self._several(papers, trashed=restore)
        ids = [p["id"] for p in found]
        if restore:
            self.s.papers.restore(ids)
            return {"restored": [self._label(p) for p in found]}
        self.s.papers.trash(ids)
        return {
            "moved_to_trash": [self._label(p) for p in found],
            "undo": "They stay in the Trash until the user empties it: trash_papers with restore=true brings them back.",
        }

    # ------------------------------------------------------------------ online
    def fetch_references(self, paper: str):
        p = self._several([paper])[0]
        res = self.s.fetch_references_now(p["id"])
        if res["status"] != "ok":
            raise ToolError(f"The reference list of '{self._label(p)}' was not fetched: {sentence(res['message'])}")
        view = res["view"]
        return drop_empty(
            {
                "paper": self._label(p),
                "references": len(view["references"]),
                "in_library": sum(1 for r in view["references"] if r.get("library_id")),
                "source": (view["fetch"] or {}).get("source"),
                "next": "get_references lists them.",
            }
        )

    def find_pdf(self, paper: str):
        p = self._several([paper])[0]
        if self._pdfs(p):
            return {"paper": self._label(p), "note": "It already has a PDF; nothing was searched."}
        try:
            res = self.s.find_pdf(p["id"])
        except Exception as e:  # network trouble: its text can hold request URLs
            raise ToolError(f"The search for a PDF failed ({type(e).__name__}). Try again later.") from e
        if res.get("status") != "success":
            return drop_empty(
                {"paper": self._label(p), "found": False, "why": res.get("message") or "No open-access PDF was found."}
            )
        att = res.get("attachment") or {}
        return drop_empty(
            {"paper": self._label(p), "found": True, "attachment": att.get("id"), "pages": self._page_count(att)}
        )

    # ------------------------------------------------------------------ annotations
    def get_annotations(self, paper: str, attachment: int | None = None):
        if str(paper or "").strip().lower().split("#")[0].rstrip("]").endswith(".pdf"):  # a PDF in the notes folders
            src = self._source(paper, attachment)
            rows = [
                drop_empty(
                    {
                        "id": a["id"],
                        "page": a["page"],
                        "type": a["type"],
                        "text": " ".join(str(a.get("text") or "").split()) or None,
                        "comment": a.get("comment"),
                        "color": a.get("color"),
                    }
                )
                for a in self.s.annotations.for_note_pdf(src.note_pdf_id)
            ]
            return drop_empty(
                {
                    "paper": src.label,
                    "annotations_total": len(rows),
                    "annotations": rows,
                    "note": None if rows else "No highlights, comments or drawings on this PDF.",
                    "cite": f"Cite a highlighted passage as {src.cite_page()} with its page.",
                }
            )
        p = self._paper(paper)
        pdfs = [self._pdf_for(p, attachment)] if attachment is not None else self._pdfs(p)
        several = len(pdfs) > 1
        rows = []
        for att in pdfs:
            for a in self.s.annotations.for_attachment(att["id"]):
                rows.append(
                    drop_empty(
                        {
                            "id": a["id"],
                            "attachment": att["id"] if several else None,
                            "page": a["page"],
                            "type": a["type"],
                            "text": " ".join(str(a.get("text") or "").split()) or None,
                            "comment": a.get("comment"),
                            "color": a.get("color"),
                        }
                    )
                )
        return drop_empty(
            {
                "paper": self._label(p),
                "annotations_total": len(rows),
                "annotations": rows,
                "note": None
                if rows
                else "No highlights, comments or drawings on " + ("its PDF." if pdfs else "it: it has no PDF."),
                "cite": f"Cite a highlighted passage as [@{p.get('citation_key') or '?'}, p. N] with its page.",
            }
        )

    def add_highlight(
        self, paper: str, page: int, quote: str, comment: str = "", color: str = "", attachment: int | None = None
    ):
        if not pdfrender.available():
            raise ToolError(pdfrender.MISSING)
        if str(paper or "").strip().lower().split("#")[0].rstrip("]").endswith(".pdf"):
            src = self._source(paper, attachment)
        else:
            p = self._several([paper])[0]
            src = self._src(self._pdf_for(p, attachment), p)
        path = src.path
        n = self._page_count(src) or pdfrender.page_count(path)
        check_page(page, n)
        quote = " ".join(str(quote or "").split())
        if len(quote) < 8:
            raise ToolError("Give the words to highlight: a passage copied from read_paper.")
        rects = pdfregions.quote_rects(path, page, quote)
        if not rects:
            raise ToolError(
                f"These words are not on page {page} of '{src.label}'. Copy one passage exactly from "
                "read_paper (no '…', nothing left out), from a single page."
            )
        data = {
            "type": "highlight",
            "page": page,
            "rects": rects,
            "text": quote,
            "color": color or self.s.settings.get("annotation_color_highlight"),  # the user's own colour
            "comment": str(comment or ""),
        }
        try:
            ann = (
                self.s.annotations.create_on_note_pdf(src.note_pdf_id, data)
                if src.note_pdf_id
                else self.s.annotations.create(src.attachment_id, data)
            )
        except AnnotationError as e:
            raise ToolError(str(e)) from e
        return drop_empty(
            {
                "highlighted": {"id": ann["id"], "paper": src.label, "page": page, "lines": len(rects)},
                "comment": comment or None,
                "note": "The user sees it on the PDF in the app and can remove it there.",
            }
        )
