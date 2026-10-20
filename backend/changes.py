"""What changed, for the other open windows and devices.

With an iPad and the computer open at the same time, each needs to notice what the other changed.
Every page polls ``GET /api/changes?since=N`` every few seconds and refreshes what the answer
names: ``library`` (items, folders, tags, attachments), ``annotations``, ``notes``, ``tasks``.

The feed is kept in memory: a restart starts a new ``epoch`` and pages simply refresh everything.
Changes are recorded

* when the library database commits (``Database.on_write``): inside a request they are credited to
  the page that made them (its ``X-Synapse-Client`` header), so a page is not told about its own
  edits; background work (metadata and reference fetching) is credited to no one;
* after a successful change to notes through the API;
* when ``library.db`` or ``notes_index.db`` changed on disk without going through this server
  (the MCP server writing a note, for example) — noticed at the next poll.
"""

from __future__ import annotations

import os
import secrets
import threading
from collections import deque
from pathlib import Path

KEEP = 2000  # entries remembered; a page further behind refreshes everything
CLIENT_HEADER = "X-Synapse-Client"


def _mtime(*paths: Path) -> tuple:
    out: list[tuple[int, int] | None] = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


class ChangeFeed:
    def __init__(self, library_db: Path, notes_index: Path):
        self.epoch = secrets.token_hex(4)
        self.seq = 0
        self._log: deque[tuple[int, str | None, frozenset[str]]] = deque(maxlen=KEEP)
        self._lock = threading.Lock()
        self._lib_paths = (Path(library_db), Path(f"{library_db}-wal"))
        self._notes_paths = (Path(notes_index), Path(f"{notes_index}-wal"))
        self._lib_seen = _mtime(*self._lib_paths)
        self._notes_seen = _mtime(*self._notes_paths)

    def record(self, kinds, client: str | None = None) -> int:
        kinds = frozenset(kinds)
        if not kinds:
            return self.seq
        with self._lock:
            self.seq += 1
            self._log.append((self.seq, client or None, kinds))
            # what is on disk now is explained by this change
            if "notes" in kinds:
                self._notes_seen = _mtime(*self._notes_paths)
            if kinds & {"library", "annotations", "tasks"}:
                self._lib_seen = _mtime(*self._lib_paths)
            return self.seq

    def _notice_outside_writes(self) -> None:
        lib = _mtime(*self._lib_paths)
        notes = _mtime(*self._notes_paths)
        kinds = set()
        if lib != self._lib_seen:
            kinds |= {"library", "annotations", "tasks"}
        if notes != self._notes_seen:
            kinds.add("notes")
        if kinds:
            self.record(kinds)

    def since(self, seq: int | None, epoch: str | None, client: str | None) -> dict:
        """What changed after ``seq`` that ``client`` did not do itself."""
        self._notice_outside_writes()
        with self._lock:
            current = self.seq
            if epoch != self.epoch or seq is None or seq > current:
                # a page that just opened (or a server restart): nothing to refresh yet, start here —
                # unless it knew another epoch, then everything may have changed
                full = epoch is not None and epoch != self.epoch
                return {
                    "epoch": self.epoch,
                    "seq": current,
                    "changes": ["library", "annotations", "notes", "tasks"] if full else [],
                    "reset": full,
                }
            oldest = self._log[0][0] if self._log else current + 1
            if seq < oldest - 1:
                return {
                    "epoch": self.epoch,
                    "seq": current,
                    "changes": ["library", "annotations", "notes", "tasks"],
                    "reset": True,
                }
            kinds: set[str] = set()
            for s, who, k in self._log:
                if s > seq and (who is None or who != client):
                    kinds |= k
            return {"epoch": self.epoch, "seq": current, "changes": sorted(kinds), "reset": False}


def kind_of(path: str) -> str:
    """Which part of the app a library-database write through this API path changes."""
    if path.startswith("/api/tasks"):
        return "tasks"
    if path.startswith("/api/annotations") or ("/annotations" in path and path.startswith("/api/attachments/")):
        return "annotations"
    return "library"
