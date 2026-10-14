"""Saving what the browser extension sends ("Save to Library").

The extension reads citation metadata from the page it is on (DOI, arXiv id, <meta
name="citation_*"> tags, JSON-LD) and posts it to ``backend/api/connector.py``, which passes it
here. When the page has a DOI or arXiv id the metadata is looked up online and the page's own
metadata only fills the gaps. A PDF can come along (fetched by the extension with the user's own
browser session, so institutional access works) or is downloaded here from the page's PDF link,
through ``online/remote_pdf.py``: only from public addresses, capped in size, PDFs only, because
the link comes from the browser.

This module sits with ``services.py`` in the composition layer: it combines the library, the
online lookups, PDF text, the citation queue and the background PDF search.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import uuid
from typing import TYPE_CHECKING

from .formats.common import join_authors, parse_display_name, year_from
from .online import remote_pdf
from .online.http import BROWSER_UA, LookupFailed
from .online.metadata import known_authors, lookup
from .pdf.text import clean_text, extract_text, find_identifier

if TYPE_CHECKING:
    from .services import Services

log = logging.getLogger(__name__)

# Item fields the extension may send as they are.
PAGE_FIELDS = [
    "title",
    "year",
    "journal",
    "volume",
    "issue",
    "pages",
    "publisher",
    "abstract",
    "issn",
    "isbn",
    "doi",
    "url",
    "language",
    "item_type",
    "place",
    "short_title",
]


class BadRequest(ValueError):
    """The request cannot be saved as it is (the endpoint answers 400)."""


class NothingToSave(BadRequest):
    """The page has no title, no identifier and no PDF."""


class Duplicate(Exception):
    """The item is already in the library (and the user did not ask to save it anyway)."""

    def __init__(self, item: dict):
        super().__init__(item["id"])
        self.item = item


def page_item(page: dict) -> dict:
    """Normalise what the extension scraped from the page into item fields."""
    item = {k: str(page[k]).strip() for k in PAGE_FIELDS if page.get(k)}
    authors = page.get("authors") or []
    if isinstance(authors, str):
        authors = [a for a in re.split(r";|\n", authors) if a.strip()]
    if authors:
        item["authors"] = join_authors(parse_display_name(a) for a in authors)
    if item.get("year"):
        item["year"] = year_from(item["year"])
    if page.get("arxiv"):
        item.setdefault("archive", "arXiv")
        item.setdefault("archive_id", f"arXiv:{page['arxiv']}")
    item.setdefault("url", page.get("page_url") or "")
    item.setdefault("item_type", "webpage")
    return {k: v for k, v in item.items() if v}


def folder_id(value) -> int | None:
    """The folder a request names, as an id (None for none). The popup sends it as a number or a
    string. Raises :class:`BadRequest` for anything else."""
    if value is None or value == "":
        return None
    if not isinstance(value, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    raise BadRequest(f"Invalid folder_id: {value!r}")


def _identifier_in_pdf(pdf_bytes: bytes) -> dict:
    """A DOI or arXiv id from the first pages of a PDF ({} if none)."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        text, _ = extract_text(tmp.name, max_pages=2)
    return find_identifier(clean_text(text))


def _download_pdf(url: str, pdf_dir) -> str | None:
    """Download the page's PDF link into ``pdf_dir``; the file name, or None if that failed."""
    try:
        data = remote_pdf.fetch_pdf(url, BROWSER_UA)
    except remote_pdf.RemoteError as e:
        log.info("Connector PDF download failed: %s", e)
        return None
    name = f"{uuid.uuid4().hex}_connector.pdf"
    (pdf_dir / name).write_bytes(data)
    return name


def save(s: Services, page: dict, pdf_bytes: bytes | None) -> dict:
    """Save the page as a new item. Returns ``{item, source, pdf}``: where the metadata came from
    (``online`` or ``page``) and what happened to the PDF (``attached``, ``downloaded``,
    ``failed``, ``not-a-pdf``, ``searching`` or ``none``). Raises :class:`NothingToSave` and
    :class:`Duplicate`, and :class:`BadRequest` for a malformed ``folder_id``."""
    folder = folder_id(page.get("folder_id"))
    if not (page.get("title") or page.get("doi") or page.get("arxiv") or pdf_bytes is not None):
        raise NothingToSave("Nothing to save: the page has no title or identifier.")

    # A PDF tab has no page metadata, so look for a DOI inside the PDF.
    if pdf_bytes and pdf_bytes[:5] == b"%PDF-" and not (page.get("doi") or page.get("arxiv")):
        ident = _identifier_in_pdf(pdf_bytes)
        page = {**page, **ident}
        if ident and page.get("item_type") == "webpage":
            page.pop("item_type")

    item = page_item(page)
    source = "page"
    if page.get("lookup", True) and (page.get("doi") or page.get("arxiv")):
        try:
            identifier = page.get("doi") or f"arXiv:{page['arxiv']}"
            fetched = lookup(
                identifier=identifier, known=known_authors(s.papers.list()), email=s.settings.get("contact_email")
            )
            # online metadata wins; the page fills whatever is missing
            item = {**item, **{k: v for k, v in fetched.items() if v}}
            if page.get("page_url") and not fetched.get("url"):
                item["url"] = page["page_url"]
            source = "online"
        except LookupFailed as e:
            log.info("Connector lookup failed, using page metadata: %s", e)

    dup = s.papers.find_duplicate(item)
    if dup and not page.get("allow_duplicate"):
        raise Duplicate(s.papers.get(dup))

    tags = page.get("tags") or []
    created = s.papers.create({**item, "folder_id": folder, "tags": tags})
    if source == "online":
        s.citations.maybe_enqueue(created, page.get("fetch_references"))

    pdf = "none"
    filename = None
    if pdf_bytes is not None:
        if pdf_bytes[:5] == b"%PDF-":
            filename = f"{uuid.uuid4().hex}_connector.pdf"
            (s.config.pdf_dir / filename).write_bytes(pdf_bytes)
            pdf = "attached"
        else:
            pdf = "not-a-pdf"
    if filename is None and page.get("pdf_url") and page.get("attach_pdf", True):
        filename = _download_pdf(page["pdf_url"], s.config.pdf_dir)
        pdf = "downloaded" if filename else "failed"
    if filename:
        name = os.path.basename(page.get("pdf_name") or "") or "Full Text PDF"
        s.papers.add_attachment(created["id"], name, filename, "pdf")
        s.attachments_changed()
    elif s.settings.wants("add_find_pdf", page.get("find_pdf")) and (item.get("doi") or item.get("title")):
        s.find_pdf_later(created["id"])  # no PDF from the page: look for an open-access copy
        pdf = "searching"

    return {"item": s.papers.get(created["id"]), "source": source, "pdf": pdf}


def file_existing(s: Services, item_id: str, folder, tags) -> dict:
    """Add an item that is already in the library to a folder and/or tag it (the extension's
    "already in your library" screen). Returns the item. Raises :class:`BadRequest` for a
    malformed ``folder_id``."""
    folder = folder_id(folder)
    item = s.papers.get(item_id)
    if folder is not None:
        s.folders.change_items([item["id"]], add=[folder])
    if tags:
        s.tags.add([item["id"]], tags)
    return s.papers.get(item["id"])
