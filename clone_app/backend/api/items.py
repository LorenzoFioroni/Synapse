"""Items: list/search, create, edit, trash."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

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
    items = s.papers.list(view)
    return jsonify(items=items, hits={}, counts=s.papers.counts())


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
    s.papers.delete_permanently(ids)
    return jsonify(ok=True, count=len(ids))


@bp.post("/trash/empty")
def empty_trash():
    s = svc()
    ids = s.papers.trashed_ids()
    s.papers.delete_permanently(ids)
    return jsonify(ok=True, count=len(ids))


@bp.post("/items/citekeys")
def regenerate_citekeys():
    ids = ids_from_body()
    if not ids:
        return error("No items given")
    return jsonify(keys=svc().papers.regenerate_citekeys(ids))
