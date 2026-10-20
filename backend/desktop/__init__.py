"""Running Synapse like a desktop program: its own window (``window.py``), a server that stops
with its last window (``presence.py``) and the desktop shortcut that starts both (``shortcut.py``).
See docs/desktop.md."""

from .presence import Presence

__all__ = ["Presence"]
