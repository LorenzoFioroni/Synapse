"""Online metadata lookups."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..library import citekeys
from ..library.identifiers import arxiv_id_of
from ..online.http import LookupFailed
from ..online.metadata import known_authors, lookup
from . import body, error, svc

bp = Blueprint("metadata", __name__, url_prefix="/api")


@bp.post("/items/<item_id>/lookup")
def lookup_for_item(item_id):
    """Fetch fresh metadata for an existing item (for the review dialog). Does not save."""
    s = svc()
    item = s.papers.get(item_id)
    others = [p for p in s.papers.list() if p["id"] != item_id]
    try:
        fetched = lookup(
            sources=s.plugins.lookup_metadata,
            doi=item.get("doi"),
            title=item.get("title"),
            arxiv_id=arxiv_id_of(item),
            known=known_authors(others),
            email=s.settings.get("contact_email"),
        )
    except LookupFailed as e:
        return error(str(e), 404)
    merged = {**item, **fetched}
    taken = s.papers.all_citation_keys(exclude_id=item_id)
    fetched["citation_key"] = citekeys.generate(s.settings.get("citekey_pattern"), merged, taken)
    return jsonify(current=item, fetched=fetched)


@bp.post("/items/from_identifier")
def create_from_identifier():
    s = svc()
    ident = (body().get("identifier") or "").strip()
    if not ident:
        return error("Enter a DOI, arXiv ID or ISBN.")
    try:
        meta = lookup(
            sources=s.plugins.lookup_metadata,
            identifier=ident,
            known=known_authors(s.papers.list()),
            email=s.settings.get("contact_email"),
        )
    except LookupFailed as e:
        return error(str(e), 404)
    dup = s.papers.find_duplicate(meta)
    if dup and not body().get("allow_duplicate"):
        return jsonify(duplicate_of=dup, metadata=meta), 409
    item = s.papers.create({**meta, "folder_id": body().get("folder_id"), "tags": body().get("tags") or []})
    s.citations.maybe_enqueue(item, body().get("fetch_references"))
    return jsonify(item), 201
