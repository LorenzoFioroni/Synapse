"""The companion: a small program on the user's own computer that connects this server to local
services (the user's AI command-line tool, a browser extension folder). See docs/companion.md."""

from .hub import Companions

__all__ = ["Companions"]
