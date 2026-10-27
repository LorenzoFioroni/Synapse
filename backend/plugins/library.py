"""``plugin.library``: what a plugin may read from the library. Plain dicts, the same shape the
JSON API answers with, so plugins never hold the app's own objects."""

from __future__ import annotations

from ..library import search as search_mod
from ..library.fields import ITEM_TYPE_LABELS
from ..library.identifiers import identifiers, norm_arxiv, norm_doi
from ..library.repository import NotFound


class LibraryReader:
    """Built on the app's components (``services``: papers, folders, tags, fulltext)."""

    def __init__(self, services):
        self._s = services

    def get(self, item_id: str) -> dict | None:
        """One item with its attachments, tags and folder ids, or None."""
        try:
            return self._s.papers.get(str(item_id))
        except NotFound:
            return None

    def items(self, ids: list[str] | None = None) -> list[dict]:
        """Items in the library (not in the Trash); only those in ``ids`` when given."""
        items = self._s.papers.list()
        if ids is None:
            return items
        wanted = set(ids)
        return [p for p in items if p["id"] in wanted]

    def search(self, query: str, *, limit: int = 50, fulltext: bool = False) -> list[dict]:
        """Items matching ``query`` in the library's search syntax (words, "phrases", -word,
        tag:, folder:, year:, author:, has:pdf …); with ``fulltext`` also the text of PDFs."""
        ctx = {
            "folder_paths": self._s.folders.paths(),
            "type_labels": ITEM_TYPE_LABELS,
            "field_filters": self._s.plugins.fields.search_filters(),
        }
        found, _ = search_mod.search(
            self._s.papers.list(), query, "fulltext" if fulltext else "all", self._s.fulltext, ctx
        )
        return found[: max(1, int(limit))]

    def find(self, *, doi: str | None = None, arxiv: str | None = None) -> dict | None:
        """The item with this DOI or arXiv id (in any of the forms the app accepts), or None."""
        want_doi, want_arxiv = norm_doi(doi), norm_arxiv(arxiv)
        if not want_doi and not want_arxiv:
            return None
        for p in self._s.papers.list():
            d, a = identifiers(p)
            if (want_doi and norm_doi(d) == want_doi) or (want_arxiv and norm_arxiv(a) == want_arxiv):
                return p
        return None

    def citation_graph(self) -> dict:
        """Stored citation graph, with ``fetched`` item ids (including empty reference lists).

        No network requests are made; nodes, edges and stats use the citation graph's JSON
        contract. Trash is excluded, as it is from ``items()``.
        """
        graph = self._s.citations.graph()
        ids = {p["id"] for p in self.items()}
        return {**graph, "fetched": sorted(self._s.citations.store.fetched_ids() & ids)}

    def folders(self) -> list[dict]:
        """The folder tree: each folder with its id, name and children."""
        return self._s.folders.tree()

    def tags(self) -> list[dict]:
        return self._s.tags.list()
