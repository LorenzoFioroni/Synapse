"""Synapse's reference writers, for plugins: ``from backend.plugins import formats`` (public API).

These are the writers Synapse's own import and citation copying are paired with, so a file a plugin
exports reads back the same way through File → Import. Items are dicts in the library's field
format (what an export writer receives, or ``plugin.library`` returns); the options are those an
export writer receives (``unicode``, ``abstract`` and the citation-key ``pattern``)."""

from __future__ import annotations

from dataclasses import dataclass

from .. import formats as _formats
from .errors import PluginError


@dataclass(frozen=True)
class Writer:
    key: str  # "bibtex", "ris", "endnote", "endnote_xml", "csljson"
    title: str
    extension: str
    mimetype: str

    def dump(self, items: list[dict], **options) -> str:
        return _formats.dump(items, self.key, **options)


def writers() -> list[Writer]:
    """Every writer, in the order Synapse lists them."""
    return [Writer(f.key, f.label, f.extension, f.mimetype) for f in _formats.FORMATS.values()]


def dump(items: list[dict], key: str, **options) -> str:
    """``items`` written in the format ``key`` (one of ``writers()``)."""
    if key not in _formats.FORMATS:
        raise PluginError(f"Unknown reference format: {key}")
    return _formats.dump(items, key, **options)
