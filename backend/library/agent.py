"""Which AI app is making the current change, so notes and tasks record who did it.

The MCP server sets it around each tool call (``acting_as``), from the name the app gave when it
connected (Claude, Codex, Cursor …). The stores read it when they record a change made by ``"ai"``.
It is a context variable, so a web request handled meanwhile in another thread is not affected.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[str | None] = ContextVar("synapse_agent", default=None)


def current_agent() -> str | None:
    """The AI app making the change now, or None (the user in the app, or unknown)."""
    return _current.get()


@contextmanager
def acting_as(name: str | None) -> Iterator[None]:
    token = _current.set(name or None)
    try:
        yield
    finally:
        _current.reset(token)
