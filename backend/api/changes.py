"""The change feed: what changed since a page last asked (see backend/changes.py)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..changes import CLIENT_HEADER
from . import svc

bp = Blueprint("changes", __name__, url_prefix="/api")


@bp.get("/changes")
def changes():
    try:
        since = int(request.args["since"]) if request.args.get("since") not in (None, "") else None
    except ValueError:
        since = None
    return jsonify(svc().changes.since(since, request.args.get("epoch") or None, request.headers.get(CLIENT_HEADER)))
