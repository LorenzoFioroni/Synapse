"""Items: list/search, create, edit, trash."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import search as search_mod
from ..fields import schema_payload
from . import body, error, ids_from_body, svc

bp = Blueprint("items", __name__, url_prefix="/api")


@bp.get("/schema")
def schema():
    return jsonify(schema_payload())


@bp.get("/items")
def list_items():
    s = svc()
    view = request.args.get("view", "library")
    q = request.args.get("q", "").strip()
    mode = request.args.get("mode") or s.settings.get("default_search_mode")
    items = s.papers.list(view)
    hits = {}
    if q:
        items, hits = search_mod.search(items, q, mode, s.fulltext)
    return jsonify(items=items, hits=hits, counts=s.papers.counts(), mode=mode, query=q)


@bp.post("/items")
def create_item():
    data = body()
    item = svc().papers.create(data)
    return jsonify(item), 201


@bp.get("/items/<item_id>")
def get_item(item_id):
    return jsonify(svc().papers.get(item_id))


@bp.patch("/items/<item_id>")
def update_item(item_id):
    return jsonify(svc().papers.update(item_id, body()))


@bp.post("/items/trash")
def trash():
    ids = ids_from_body()
    svc().papers.trash(ids)
    return jsonify(ok=True, count=len(ids))


@bp.post("/items/restore")
def restore():
    ids = ids_from_body()
    svc().papers.restore(ids)
    return jsonify(ok=True, count=len(ids))


@bp.post("/items/delete")
def delete_permanently():
    s = svc()
    ids = ids_from_body()
    removed = s.papers.delete_permanently(ids)
    s.fulltext.remove(removed)
    return jsonify(ok=True, count=len(ids))


@bp.post("/trash/empty")
def empty_trash():
    s = svc()
    ids = s.papers.trashed_ids()
    removed = s.papers.delete_permanently(ids)
    s.fulltext.remove(removed)
    return jsonify(ok=True, count=len(ids))


@bp.post("/items/citekeys")
def regenerate_citekeys():
    ids = ids_from_body()
    if not ids:
        return error("No items given")
    return jsonify(keys=svc().papers.regenerate_citekeys(ids))


@bp.get("/stats")
def stats():
    s = svc()
    atts = s.papers.all_attachments()
    return jsonify(
        counts=s.papers.counts(),
        attachments=len(atts),
        fulltext=s.fulltext.stats(),
        indexer=s.indexer.status,
        data_dir=str(s.config.data_dir),
    )


@bp.post("/fulltext/rebuild")
def rebuild_fulltext():
    svc().indexer.rebuild()
    return jsonify(ok=True)


@bp.post("/fulltext/sync")
def sync_fulltext():
    svc().indexer.sync()
    return jsonify(ok=True)
