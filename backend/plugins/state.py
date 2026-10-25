"""``data/plugins.json``: which plugins are on, their settings, whether their tools reach other
computers, and why a plugin was turned off. Both processes that run plugins read it; it is read
fresh before every change, since the other process may have written it."""

from __future__ import annotations

import json
from pathlib import Path

from .files import save_json, transaction


class StateFile:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict[str, dict]:
        """Every plugin's entry, by id."""
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        plugins = data.get("plugins") if isinstance(data, dict) else None
        if not isinstance(plugins, dict):
            return {}
        return {k: v for k, v in plugins.items() if isinstance(v, dict)}

    def of(self, pid: str) -> dict:
        return self.read().get(pid, {})

    def stamp(self) -> tuple | None:
        """Changes when the file does (to notice the other process writing it)."""
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def update(self, pid: str, **changes) -> dict:
        """Change one plugin's entry and return it."""
        with transaction(self.path):
            plugins = self.read()
            cur = dict(plugins.get(pid, {}))
            cur.update(changes)
            plugins[pid] = cur
            save_json(self.path, {"plugins": plugins}, indent=2)
            return cur
