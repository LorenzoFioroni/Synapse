"""Reference lists (the citation graph)."""
from __future__ import annotations

from flask import Blueprint, jsonify

from ..online.http import LookupFailed
from ..references.sources import FetchFailed
from ..library.repository import NotFound
from . import body, error, svc

bp = Blueprint("references", __name__, url_prefix="/api")


@bp.get("/items/<item_id>/references")
def item_references(item_id):
    """What the item cites (marked when in the library) and which library items cite it."""
    return jsonify(svc().citations.item_view(item_id))


@bp.post("/items/<item_id>/references")
def fetch_references(item_id):
    """Fetch the reference list now (replaces the stored one)."""
    try:
        return jsonify(svc().citations.fetch_now(item_id))
    except NotFound:
        raise
    except FetchFailed as e:   # the services failed: try again later
        return error(str(e), 502)
    except LookupFailed as e:
        return error(str(e), 404)
    except Exception as e:   # network trouble
        return error(f"Could not fetch references: {e}", 502)


@bp.post("/references/queue")
def queue_references():
    """Fetch the reference lists of several items in the background."""
    ids = [i for i in (body().get("ids") or []) if isinstance(i, str)]
    return jsonify(queued=svc().citations.enqueue(ids), status=svc().citations.status())


@bp.get("/references/status")
def references_status():
    return jsonify(svc().citations.status())


@bp.get("/graph/citations")
def citation_graph():
    """Every library item and every work they cite, with "cites" edges (for analysis / AI)."""
    return jsonify(svc().citations.graph())


@bp.get("/graph")
def knowledge_graph():
    """Papers, the works they cite and notes, with their links (the Graph view)."""
    from ..references.graph import build_graph
    return jsonify(build_graph(svc()))
