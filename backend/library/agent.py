"""Which AI app (or which of the user's devices) is making the current change, so notes and tasks
record who did it.

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


# The device the user is working on (the web app sets it for each request: "This computer", or a
# paired iPad's name), so the notes history can tell edits made on different devices apart.
THIS_COMPUTER = "This computer"
_device: ContextVar[str | None] = ContextVar("synapse_device", default=None)


def current_device() -> str:
    return _device.get() or THIS_COMPUTER


@contextmanager
def on_device(name: str | None) -> Iterator[None]:
    token = _device.set(name or None)
    try:
        yield
    finally:
        _device.reset(token)


def set_device(name: str | None):
    """For a request hook: returns the token ``reset_device`` takes."""
    return _device.set(name or None)


def reset_device(token) -> None:
    _device.reset(token)
