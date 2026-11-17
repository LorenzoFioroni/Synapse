"""A PDF the tools can read, wherever it lives: an attachment of a library item, or a PDF kept in a
notes folder. The tools (reading pages, searching, the outline, page images, highlights) take one of
these instead of an attachment, so both kinds go through the same code."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PdfSource:
    key: str  # "att:12" or "npdf:7": what caches are keyed by
    path: Path
    label: str  # how the answers name it: the citation key, or the PDF's path in the notes folders
    title: str  # the item's title, or the file name
    cite: str  # how a page is cited: "[@key, p. {page}]" or "[[folder/file.pdf#p={page}]]"
    attachment_id: int | None = None
    note_pdf_id: int | None = None

    def cite_page(self, page: int | str = "N") -> str:
        return self.cite.format(page=page)
