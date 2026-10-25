"""Earlier versions of notes changed by an AI app (the MCP server's edit_note), so every such change
can be seen and undone in the app.

One JSON file per change in ``<vault>/.history/`` (hidden like ``.trash``, included in backups):

    {"id", "path", "at", "by", "agent", "summary", "before", "before_hash", "after_hash"}

``by`` is "ai"; ``agent`` names the AI app (Claude, Codex …; missing in changes recorded before it
was kept). ``before`` is the whole note as it was; ``after_hash`` identifies the version the change
produced, so the app can tell whether the note still is exactly what the AI app left (then Undo is a plain
switch back) or was edited since. The MCP server and the app run in different processes; each
change is its own file with a unique name, so neither can overwrite the other's entries.
"""

from __future__ import annotations

import builtins
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

HISTORY = ".history"
KEEP = 1000  # changes kept in all
MAX_AGE = 365 * 86400  # … and none older than a year
_ID = re.compile(r"^[0-9A-Z]{26}$")


class NoteHistory:
    def __init__(self, vault_root: Path | str):
        self.dir = Path(vault_root) / HISTORY
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ writing
    def record(
        self,
        path: str,
        *,
        before: str,
        before_hash: str,
        after_hash: str,
        by: str = "ai",
        agent: str | None = None,
        summary: str = "",
    ) -> dict:
        from .service import now_iso, ulid

        entry = {
            "id": ulid(),
            "path": path,
            "at": now_iso(),
            "by": by,
            **({"agent": agent} if agent else {}),
            "summary": summary[:300],
            "before": before,
            "before_hash": before_hash,
            "after_hash": after_hash,
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / f"{entry['id']}.json"
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), "utf-8")
        os.replace(tmp, p)
        self.prune()
        return entry

    def moved(self, pairs: builtins.list[tuple[str, str]]) -> None:
        """Notes were renamed or moved: their history follows them."""
        new_of = dict(pairs)
        if not new_of:
            return
        with self._lock:
            for f, e in self._all():
                if e.get("path") in new_of:
                    e["path"] = new_of[e["path"]]
                    try:
                        tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
                        tmp.write_text(json.dumps(e, ensure_ascii=False), "utf-8")
                        os.replace(tmp, f)
                    except OSError:
                        log.exception("Could not update %s", f)

    def prune(self) -> None:
        files = sorted(self.dir.glob("*.json")) if self.dir.is_dir() else []
        cutoff = time.time() - MAX_AGE
        for k, f in enumerate(files):
            try:
                if k < len(files) - KEEP or f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ reading
    def _all(self):
        if not self.dir.is_dir():
            return
        for f in sorted(self.dir.glob("*.json")):
            try:
                yield f, json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                continue

    def list(self, path: str | None = None, limit: int = 50) -> builtins.list[dict]:
        """Newest first, without the stored text."""
        out = [
            {k: v for k, v in e.items() if k != "before"}
            for _, e in self._all()
            if path is None or e.get("path") == path
        ]
        return out[::-1][:limit]

    def get(self, entry_id: str) -> dict | None:
        if not _ID.match(entry_id or ""):
            return None
        f = self.dir / f"{entry_id}.json"
        try:
            return json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def latest(self, path: str) -> dict | None:
        found = self.list(path, limit=1)
        return self.get(found[0]["id"]) if found else None
