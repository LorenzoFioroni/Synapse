"""Permission-gated read access to note listings and resolved note/citation links."""

from __future__ import annotations

import builtins


class NotesReader:
    def __init__(self, services):
        self._s = services

    def list(self) -> builtins.list[dict]:
        """Notes with paths, front-matter metadata and timestamps; no document writes."""
        return self._s.notes.list()

    def resolve(self, ref: str) -> str:
        """Resolve a note path, wiki link or title without editing it."""
        import re

        from ..notes.vault import InvalidPath
        from .errors import PluginError

        name = re.sub(r"^!?\[\[|\]\]$", "", str(ref or "").strip()).split("|")[0].split("#")[0].strip()
        if not name:
            raise PluginError("Give a note path or title.")
        try:
            path = self._s.vault.clean_rel(name if name.lower().endswith(".md") else name + ".md")
            if self._s.vault.exists(path):
                return path
        except InvalidPath:
            pass
        found = self._s.notes.resolve(name)
        if found:
            return found[0]
        matches = [n["path"] for n in self.list() if (n.get("title") or "").casefold() == name.casefold()]
        if matches:
            return sorted(matches, key=len)[0]
        hints = [f"{h['path']} ({h['title']})" for h in self._s.notes.search(name, limit=5)]
        raise PluginError(
            f"No note '{ref}'."
            + (" Similar: " + "; ".join(hints) if hints else " Use search_library with scope 'notes' to find it.")
        )

    def links(self) -> builtins.list[dict]:
        """Resolved links: source path, target (path or citation key), and kind (note/cite)."""
        return self._s.notes.link_graph()
