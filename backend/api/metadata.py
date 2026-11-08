"""Online metadata lookups."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..online.http import LookupFailed
from ..online.metadata import compute_changes, known_authors, lookup, lookup_for_item
from . import body, error, failure, svc

bp = Blueprint("metadata", __name__, url_prefix="/api")


@bp.post("/items/<item_id>/lookup")
def lookup_for_item_endpoint(item_id):
    """Fetch fresh metadata for an existing item (for the review dialog). Does not save."""
    s = svc()
    try:
        item, fetched = lookup_for_item(s, item_id)
    except LookupFailed as e:
        return failure(e, 404)
    return jsonify(current=item, fetched=fetched, changes=compute_changes(item, fetched))


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
        return failure(e, 404)
    dup = s.papers.find_duplicate(meta)
    if dup and not body().get("allow_duplicate"):
        return jsonify(duplicate_of=dup, metadata=meta), 409
    item = s.papers.create({**meta, "folder_id": body().get("folder_id"), "tags": body().get("tags") or []})
    s.citations.maybe_enqueue(item, body().get("fetch_references"))
    return jsonify(item), 201
