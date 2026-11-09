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

import base64
import json
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


def _wants_lookup(s: Services, page: dict) -> bool:
    """Look the identifier up online? ``lookup`` is the user's answer in the popup ("Complete
    metadata online"). A request without it (no checkbox, or another client) follows Settings →
    "Retrieve metadata": anything but "never" looks up, as saving always did before the setting."""
    asked = page.get("lookup")
    if asked is not None:
        return bool(asked)
    return s.settings.get("add_fetch_metadata") != "never"


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
    if _wants_lookup(s, page) and (page.get("doi") or page.get("arxiv")):
        try:
            identifier = page.get("doi") or f"arXiv:{page['arxiv']}"
            fetched = lookup(
                sources=s.plugins.lookup_metadata,
                identifier=identifier,
                known=known_authors(s.papers.list()),
                email=s.settings.get("contact_email"),
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
    # whether or not it was looked up: the reference list only needs the DOI or arXiv id
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


MISSING_PDFS_MAX = 200
_ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CURSOR_MAX = 512


def _missing_cursor(boundary: tuple[str, str]) -> str:
    data = json.dumps([1, *boundary], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _cursor_boundary(cursor: str | None) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        if len(cursor) > _CURSOR_MAX or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
            raise ValueError
        data = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if (
            not isinstance(data, list)
            or len(data) != 3
            or type(data[0]) is not int
            or data[0] != 1
            or not isinstance(data[1], str)
            or len(data[1]) > 128
            or any(ord(c) < 32 for c in data[1])
            or not isinstance(data[2], str)
            or not _ITEM_ID.fullmatch(data[2])
        ):
            raise ValueError
        return data[1], data[2]
    except (ValueError, UnicodeError):
        raise BadRequest("Invalid missing-PDF cursor.") from None


def _missing_ids(ids: str | None) -> set[str] | None:
    if ids is None:
        return None
    if len(ids) > MISSING_PDFS_MAX * 129:
        raise BadRequest("ids must contain 1 to 200 item IDs.")
    values = ids.split(",")
    if not 1 <= len(values) <= MISSING_PDFS_MAX or any(not _ITEM_ID.fullmatch(v) for v in values):
        raise BadRequest("ids must contain 1 to 200 nonempty item IDs separated by commas.")
    return set(values)


def missing_pdfs(s: Services, folder=None, limit: int = 50, cursor: str | None = None, ids: str | None = None) -> dict:
    """Publisher DOI items without PDFs, descending by (date_added, id).

    Opaque item cursors resume after a completed attempt, even if that item was removed or
    acquired a PDF. ``total`` counts eligible items within folder/ids before the cursor.
    """
    from .library.identifiers import clean_doi

    folder = folder_id(folder)
    boundary = _cursor_boundary(cursor)
    selected = _missing_ids(ids)
    limit = max(1, min(int(limit), MISSING_PDFS_MAX))
    inside = s.folders.descendants(folder) if folder is not None else None
    out = []
    for p in s.papers.list():
        doi = clean_doi(p.get("doi"))
        if not doi or doi.lower().startswith("10.48550/") or _has_pdf(p):
            continue
        if selected is not None and p["id"] not in selected:
            continue
        if inside is not None and not inside.intersection(p.get("folder_ids") or []):
            continue
        out.append(
            {
                "id": p["id"],
                "title": p.get("title") or "",
                "doi": doi,
                "year": p.get("year"),
                "_key": (p.get("date_added") or "", p["id"]),
            }
        )
    total = len(out)
    out.sort(key=lambda r: r["_key"], reverse=True)
    remaining = [p for p in out if boundary is None or p["_key"] < boundary]
    page = remaining[:limit]
    for p in page:
        p["cursor"] = _missing_cursor(p.pop("_key"))
    return {
        "items": page,
        "total": total,
        "next_cursor": page[-1]["cursor"] if len(remaining) > limit else None,
    }


def _has_pdf(item: dict) -> bool:
    return any(
        a.get("type") == "pdf" or str(a.get("filename") or "").lower().endswith(".pdf")
        for a in item.get("attachments") or []
    )


def request_browser_pdfs(s: Services, ids) -> bool:
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= MISSING_PDFS_MAX
        or any(not isinstance(i, str) or not _ITEM_ID.fullmatch(i) for i in ids)
    ):
        raise BadRequest("ids must contain 1 to 200 item IDs.")
    selected = missing_pdfs(s, limit=MISSING_PDFS_MAX, ids=",".join(ids))
    return s.browser_pdfs.request([p["id"] for p in selected["items"]])


def attach_pdf(s: Services, item_id: str, pdf_bytes: bytes | None) -> dict:
    """Attach a PDF the extension fetched for an existing item, if it is that paper (the check
    Find Available PDF makes) and the item has no PDF yet. Returns ``{status, item}`` with status
    ``attached``, ``not-a-pdf``, ``wrong-paper``, ``unverified`` or ``has-pdf``. Raises :class:`BadRequest`
    without an item id."""
    from .library.identifiers import identifiers
    from .online.pdf_finder import pdf_verification

    if not item_id:
        raise BadRequest("Which item? The request has no id.")
    item = s.papers.get(item_id)
    if _has_pdf(item):
        return {"status": "has-pdf", "item": item}
    if not pdf_bytes or pdf_bytes[:5] != b"%PDF-":
        return {"status": "not-a-pdf", "item": item}
    _, arxiv_id = identifiers(item)
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        verification = pdf_verification(tmp.name, item.get("title"), item.get("doi"), arxiv_id)
        if verification["status"] != "verified":
            return {**verification, "item": item}
    filename = f"{uuid.uuid4().hex}_connector.pdf"
    (s.config.pdf_dir / filename).write_bytes(pdf_bytes)
    s.papers.add_attachment(item["id"], "Full Text PDF", filename, "pdf")
    s.attachments_changed()
    return {"status": "attached", "item": s.papers.get(item["id"])}


def find_pdf(s: Services, item_id: str) -> dict:
    """Queue the configured PDF sources for an existing item after a browser attempt."""
    if not item_id:
        raise BadRequest("Which item? The request has no id.")
    item = s.papers.get(item_id)
    if _has_pdf(item):
        return {"status": "has-pdf"}
    s.find_pdf_later(item["id"])
    return {"status": "searching"}


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
