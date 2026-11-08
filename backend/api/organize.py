"""Folders, tags and smart folders."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..library.organize import OrganizeError
from . import body, error, failure, ids_from_body, svc

bp = Blueprint("organize", __name__, url_prefix="/api")


def _guard(fn):
    try:
        return fn()
    except OrganizeError as e:
        return failure(e)


def _int_or_none(v):
    return None if v in (None, "", "null") else int(v)


# ---------------------------------------------------------------- folders
@bp.get("/folders")
def list_folders():
    return jsonify(folders=svc().folders.tree())


@bp.post("/folders")
def create_folder():
    d = body()
    return _guard(lambda: (jsonify(svc().folders.create(d.get("name"), _int_or_none(d.get("parent_id")))), 201))


@bp.patch("/folders/<int:fid>")
def update_folder(fid):
    d = body()
    kwargs = {}
    if "name" in d:
        kwargs["name"] = d["name"]
    if "parent_id" in d:
        kwargs["parent_id"] = _int_or_none(d["parent_id"])
    return _guard(lambda: jsonify(svc().folders.update(fid, **kwargs)))


@bp.delete("/folders/<int:fid>")
def delete_folder(fid):
    return jsonify(ok=True, unfiled=svc().folders.delete(fid))


@bp.post("/items/folders")
def change_item_folders():
    """{ids, add: [folder ids], remove: [folder ids]}. An item can be in many folders;
    "moving" is add + remove. Removing never deletes the item."""
    ids = ids_from_body()
    d = body()
    if not ids:
        return error("No items given")
    as_list = lambda v: v if isinstance(v, list) else ([] if v in (None, "") else [v])
    svc().folders.change_items(ids, add=as_list(d.get("add")), remove=as_list(d.get("remove")))
    return jsonify(ok=True, count=len(ids))


# ------------------------------------------------------------------- tags
@bp.get("/tags")
def list_tags():
    return jsonify(tags=svc().tags.list())


@bp.post("/items/tags")
def tag_items():
    """{ids, add: [names], remove: [names]}"""
    ids = ids_from_body()
    d = body()
    if not ids:
        return error("No items given")
    s = svc()
    s.tags.add(ids, d.get("add") or [])
    s.tags.remove(ids, d.get("remove") or [])
    return jsonify(ok=True, tags=s.tags.list())


@bp.patch("/tags/<int:tid>")
def update_tag(tid):
    d = body()
    kwargs = {}
    if "name" in d:
        kwargs["name"] = d["name"]
    if "color" in d:
        kwargs["color"] = d["color"]
    return _guard(lambda: jsonify(svc().tags.update(tid, **kwargs)))


@bp.delete("/tags/<int:tid>")
def delete_tag(tid):
    svc().tags.delete(tid)
    return jsonify(ok=True)


# ---------------------------------------------------------- smart folders
@bp.get("/smart")
def list_smart():
    return jsonify(smart=svc().smart.list())


@bp.post("/smart")
def create_smart():
    d = body()
    return _guard(lambda: (jsonify(svc().smart.create(d.get("name"), d.get("query"), d.get("mode", "all"))), 201))


@bp.patch("/smart/<int:sid>")
def update_smart(sid):
    return _guard(lambda: jsonify(svc().smart.update(sid, body())))


@bp.delete("/smart/<int:sid>")
def delete_smart(sid):
    svc().smart.delete(sid)
    return jsonify(ok=True)
