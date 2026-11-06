"""Telling a PDF file from anything else.

Attachments are served from the app's own origin, so a file that only claims to be a PDF (an HTML
page or a script renamed to .pdf) must never be stored or served as one: a browser would run it with
the signed-in user's session.
"""

from __future__ import annotations

from pathlib import Path

# PDF readers accept the "%PDF-" header after a little junk, and so do downloads (online.remote_pdf).
HEADER_WINDOW = 1024


def looks_like_pdf(head: bytes) -> bool:
    """Whether these first bytes of a file start a PDF."""
    return b"%PDF-" in head[:HEADER_WINDOW]


def file_is_pdf(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            return looks_like_pdf(f.read(HEADER_WINDOW))
    except OSError:
        return False
