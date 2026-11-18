"""Locked notes: notes that open only after the password from Settings → Notes or a passkey.

A note is locked by ``locked: true`` in its front matter, so the file stays plain Markdown and the
lock moves with the note. The lock is enforced by the server, not by encryption: everything that
reads a note for someone (the app, embeds, history, search, backlinks, Ask AI, the MCP server)
goes through ``NotesService``, which answers ``NoteLocked`` unless the request comes from a page
that unlocked this note. Who can read the files on the server can still read the note.

Unlocking gives one page of the app (its ``X-Synapse-Client`` id, on one device) access to one
note for ``timeout`` seconds, postponed only by use: a save, or the page reporting that you clicked,
typed, scrolled or touched the note (``touch``). Reading does not postpone it, so a tab left open
and forgotten locks on time; the app reads the note on screen every few seconds. The grants
live in memory: a restart, closing the note's tab or reloading the page locks the note again. The
MCP server runs in its own process and never holds a grant, so AI apps never read a locked note.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from . import frontmatter
from .vault import VaultError

FIELD = "locked"
MAX_GRANTS = 500  # unlocked notes kept at once (the oldest goes first)


class NoteLocked(VaultError):
    status = 423
    readable = True  # the MCP server passes the message on to the AI app

    def __init__(self, path: str):
        super().__init__(
            f"“{path}” is a locked note: only the app shows it, after its password or a passkey. "
            "AI apps cannot read or change it."
        )
        self.path = path


def is_locked_meta(meta: dict) -> bool:
    v = meta.get(FIELD)
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "on", "1"))


def is_locked_text(text: str | None) -> bool:
    return bool(text) and is_locked_meta(frontmatter.read(text or ""))


# Who is asking in this request: (device, page), set by the web app for each request. None outside
# a request of the app (the MCP server, background work): nothing is unlocked there.
_viewer: ContextVar[tuple[str, str] | None] = ContextVar("synapse_note_viewer", default=None)


def set_viewer(device: str, client: str):
    """For a request hook: returns the token ``reset_viewer`` takes. No page id: no viewer."""
    return _viewer.set((device, client) if device and client else None)


def has_viewer() -> bool:
    """Whether this runs for a page of the app (which can hold unlocked notes)."""
    return _viewer.get() is not None


def reset_viewer(token) -> None:
    _viewer.reset(token)


@contextmanager
def no_viewer() -> Iterator[None]:
    """Run without the request's grants (what is sent to an AI app must never contain a locked note)."""
    token = _viewer.set(None)
    try:
        yield
    finally:
        _viewer.reset(token)


class UnlockGrants:
    """Which pages unlocked which notes, and until when."""

    def __init__(self, timeout: Callable[[], float], clock=time.time):
        self.timeout = timeout  # seconds an unlocked note stays open without being read or saved
        self.clock = clock
        self._until: dict[tuple[str, str, str], float] = {}  # (device, page, path) → expiry
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        self._until = {k: t for k, t in self._until.items() if t > now}

    def grant(self, path: str) -> float:
        """Unlock ``path`` for the page making this request. Returns the seconds it stays open."""
        viewer = _viewer.get()
        if viewer is None:
            raise NoteLocked(path)
        now = self.clock()
        span = self.timeout()
        with self._lock:
            self._prune(now)
            while len(self._until) >= MAX_GRANTS:
                del self._until[min(self._until, key=self._until.__getitem__)]
            self._until[(viewer[0], viewer[1], path)] = now + span
        return span

    def allows(self, path: str, *, extend: bool = False) -> bool:
        """Whether this request's page has ``path`` unlocked. ``extend``: the note is being used
        (saved, clicked, scrolled …): it stays open ``timeout`` from now."""
        viewer = _viewer.get()
        if viewer is None:
            return False
        key = (viewer[0], viewer[1], path)
        now = self.clock()
        with self._lock:
            if self._until.get(key, 0) <= now:
                self._until.pop(key, None)
                return False
            if extend:
                self._until[key] = now + self.timeout()
            return True

    def remaining(self, path: str) -> float | None:
        viewer = _viewer.get()
        if viewer is None:
            return None
        with self._lock:
            left = self._until.get((viewer[0], viewer[1], path), 0) - self.clock()
        return left if left > 0 else None

    def revoke(self, paths: list[str] | None = None) -> None:
        """Lock again, for this request's page, ``paths`` (or every note it unlocked)."""
        viewer = _viewer.get()
        if viewer is None:
            return
        with self._lock:
            for k in [k for k in self._until if k[:2] == viewer and (paths is None or k[2] in paths)]:
                del self._until[k]

    def moved(self, moves: dict[str, str]) -> None:
        """Notes were renamed or moved: who had them unlocked keeps them unlocked."""
        with self._lock:
            for k in [k for k in self._until if k[2] in moves]:
                self._until[(k[0], k[1], moves[k[2]])] = self._until.pop(k)

    def forget(self, path: str) -> None:
        """Everyone's grant for ``path`` (its lock was removed, or it is gone)."""
        with self._lock:
            for k in [k for k in self._until if k[2] == path]:
                del self._until[k]
