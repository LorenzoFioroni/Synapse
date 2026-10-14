"""Endpoints for the browser extension ("Save to Library" button).

The extension reads citation metadata from the page it is on (DOI, arXiv id,
<meta name="citation_*"> tags, JSON-LD) and posts it here. When the page has a
DOI or arXiv id the metadata is looked up online and the page's own metadata
only fills the gaps. A PDF can be sent along (fetched by the extension with the
user's own browser session, so institutional access works) or downloaded here.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid

import requests
from flask import Blueprint, jsonify, request

from ..formats.common import join_authors, parse_display_name, year_from
from ..online.http import BROWSER_UA, LookupError_
from ..online.metadata import known_authors, lookup
from . import error, svc

log = logging.getLogger(__name__)
bp = Blueprint("connector", __name__, url_prefix="/api/connector")

VERSION = "1.0"
PAGE_FIELDS = ["title", "year", "journal", "volume", "issue", "pages", "publisher", "abstract", "issn", "isbn",
               "doi", "url", "language", "item_type", "place", "short_title"]


@bp.after_request
def allow_extension_origins(resp):
    """The extension normally reaches 127.0.0.1/localhost through its host permissions;
    CORS for extension origins also lets it talk to a library running elsewhere."""
    origin = request.headers.get("Origin", "")
    if origin.startswith(("chrome-extension://", "moz-extension://")):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@bp.get("/ping")
def ping():
    """Lets the extension check the library is running and fill its folder/tag pickers."""
    s = svc()
    return jsonify(app="synapse", version=VERSION, folders=s.folders.tree(),
                   tags=[t["name"] for t in s.tags.list()], counts=s.papers.counts(),
                   # Settings → "When an item is added": the popup shows a checkbox for each "ask"
                   on_add={"fetch_metadata": s.settings.get("add_fetch_metadata"),
                           "find_pdf": s.settings.get("add_find_pdf"),
                           "fetch_references": s.settings.get("add_fetch_references")})


def _payload() -> tuple[dict, object]:
    """Metadata from JSON or from a multipart 'meta' field (+ optional 'file')."""
    if request.files or request.form:
        try:
            meta = json.loads(request.form.get("meta") or "{}")
        except ValueError:
            meta = {}
        return meta, request.files.get("file")
    return request.get_json(silent=True) or {}, None


def _page_item(page: dict) -> dict:
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


def _download_pdf(url: str, pdf_dir) -> str | None:
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": BROWSER_UA})
        if r.ok and r.content[:5] == b"%PDF-":
            name = f"{uuid.uuid4().hex}_connector.pdf"
            (pdf_dir / name).write_bytes(r.content)
            return name
    except requests.RequestException as e:
        log.info("Connector PDF download failed: %s", e)
    return None


@bp.post("/file")
def file_existing():
    """Add an item that is already in the library to a folder and/or tag it (from the
    extension's "already in your library" screen)."""
    s = svc()
    d = request.get_json(silent=True) or {}
    item = s.papers.get(str(d.get("id") or ""))
    if d.get("folder_id") not in (None, ""):
        s.folders.change_items([item["id"]], add=[d["folder_id"]])
    if d.get("tags"):
        s.tags.add([item["id"]], d["tags"])
    return jsonify(item=s.papers.get(item["id"]))


@bp.post("/save")
def save():
    s = svc()
    page, upload = _payload()
    if not (page.get("title") or page.get("doi") or page.get("arxiv") or upload):
        return error("Nothing to save: the page has no title or identifier.")

    # Read the uploaded PDF once; a PDF tab has no page metadata, so look for a DOI inside it.
    pdf_bytes = upload.read() if upload is not None else None
    if pdf_bytes and pdf_bytes[:5] == b"%PDF-" and not (page.get("doi") or page.get("arxiv")):
        import tempfile
        from ..pdf.text import clean_text, extract_text, find_identifier
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            tmp.flush()
            text, _ = extract_text(tmp.name, max_pages=2)
        ident = find_identifier(clean_text(text))
        page = {**page, **ident}
        if ident and page.get("item_type") == "webpage":
            page.pop("item_type")

    item = _page_item(page)
    source = "page"
    if page.get("lookup", True) and (page.get("doi") or page.get("arxiv")):
        try:
            ident = page.get("doi") or f"arXiv:{page['arxiv']}"
            fetched = lookup(identifier=ident, known=known_authors(s.papers.list()),
                             email=s.settings.get("contact_email"))
            # online metadata wins; the page fills whatever is missing
            item = {**item, **{k: v for k, v in fetched.items() if v}}
            if page.get("page_url") and not fetched.get("url"):
                item["url"] = page["page_url"]
            source = "online"
        except LookupError_ as e:
            log.info("Connector lookup failed, using page metadata: %s", e)

    dup = s.papers.find_duplicate(item)
    if dup and not page.get("allow_duplicate"):
        existing = s.papers.get(dup)
        return jsonify(duplicate_of=dup, title=existing.get("title"), folder_ids=existing.get("folder_ids") or []), 409

    folder = page.get("folder_id")
    tags = page.get("tags") or []
    created = s.papers.create({**item, "folder_id": int(folder) if folder not in (None, "") else None, "tags": tags})
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
        name = (os.path.basename(page.get("pdf_name") or "") or "Full Text PDF")
        s.papers.add_attachment(created["id"], name, filename, "pdf")
        s.attachments_changed()
    elif s.settings.wants("add_find_pdf", page.get("find_pdf")) and (item.get("doi") or item.get("title")):
        s.find_pdf_later(created["id"])     # no PDF from the page: look for an open-access copy
        pdf = "searching"

    return jsonify(item=s.papers.get(created["id"]), source=source, pdf=pdf), 201
