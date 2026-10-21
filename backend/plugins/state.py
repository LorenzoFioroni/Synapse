"""``data/plugins.json``: which plugins are on, their settings, whether their tools reach other
computers, and why a plugin was turned off. Both processes that run plugins read it; it is read
fresh before every change, since the other process may have written it."""

from __future__ import annotations

import json
import threading
from pathlib import Path


class StateFile:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

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
        with self._lock:
            plugins = self.read()
            cur = dict(plugins.get(pid, {}))
            cur.update(changes)
            plugins[pid] = cur
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"plugins": plugins}, indent=2, ensure_ascii=False), "utf-8")
            tmp.replace(self.path)
            return cur
