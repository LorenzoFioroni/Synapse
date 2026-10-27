"""Permission-gated read access to note listings and resolved note/citation links."""

from __future__ import annotations

import builtins


class NotesReader:
    def __init__(self, services):
        self._s = services

    def list(self) -> builtins.list[dict]:
        """Notes with paths, front-matter metadata and timestamps; no document writes."""
        return self._s.notes.list()

    def links(self) -> builtins.list[dict]:
        """Resolved links: source path, target (path or citation key), and kind (note/cite)."""
        return self._s.notes.link_graph()
