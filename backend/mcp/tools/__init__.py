"""The library tools offered over MCP.

Every tool is a thin layer over the existing backend (library/search.py, the notes index,
the full-text index, the citation store). Papers are named by citation key (an
item id works too); notes by path or title. Page numbers are PDF page indices
(1 = first page), the same numbers ``[@key, p. N]`` uses in notes, so answers
can cite pages the app opens directly.

One mixin per family of tools; ``ToolsBase`` (``common.py``) has the lookups they share.
``backend/mcp/server.py`` registers the tools with their descriptions and argument schemas.
"""
from __future__ import annotations

from .citations import CitationToolsMixin
from .common import ToolsBase
from .images import ImageToolsMixin
from .library import LibraryToolsMixin
from .notes import NoteToolsMixin
from .papers import PaperToolsMixin


class LibraryTools(LibraryToolsMixin, NoteToolsMixin, ImageToolsMixin, PaperToolsMixin, CitationToolsMixin, ToolsBase):
    """All the tools, as methods; ``build_server`` exposes them over MCP."""
