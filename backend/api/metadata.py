"""Online metadata lookups."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..online.http import LookupFailed
from ..online.metadata import compute_changes, lookup_for_item
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
    data = body()
    ident = (data.get("identifier") or "").strip()
    if not ident:
        return error("Enter a DOI, arXiv ID or ISBN.")
    res = s.add_by_identifier(
        ident,
        folder_id=data.get("folder_id"),
        tags=data.get("tags") or [],
        allow_duplicate=bool(data.get("allow_duplicate")),
        fetch_references=data.get("fetch_references"),
    )
    if res["status"] == "failed":
        return error(res["message"], 404)
    if res["status"] == "duplicate":
        return jsonify(duplicate_of=res["item_id"], metadata=res["metadata"]), 409
    return jsonify(res["item"]), 201
