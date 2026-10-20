"""Markdown notes: a folder of .md files (the vault) plus a disposable index.

The files under ``data/notes/`` are the source of truth. Everything derived from them
(titles, links, citations, tags, full-text) lives in ``data/notes_index.db`` and can be
deleted and rebuilt from the files at any time.

Modules:
    vault        safe file access (path checks, atomic writes, conflict detection, trash)
    frontmatter  read and minimally edit the YAML-like header of a note
    parser       the ONLY place that decides what a note links to or cites
    gallery      reading ```gallery blocks (images and captions), for the MCP tools
    index        SQLite index of notes, links and tags, plus full-text search
"""

from .index import NotesIndex
from .vault import Conflict, InvalidPath, NoteExists, NoteMissing, Vault

__all__ = ["NotesIndex", "Vault", "Conflict", "InvalidPath", "NoteExists", "NoteMissing"]
