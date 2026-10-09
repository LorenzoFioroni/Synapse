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


def search_context(s) -> dict:
    from ..fields import ITEM_TYPE_LABELS
    return {"folder_paths": s.folders.paths(), "type_labels": ITEM_TYPE_LABELS}


def items_for_view(s, view: str, library: list[dict]) -> tuple[list[dict], str | None, str | None]:
    """Resolve a view name to its items. Returns (items, smart_query, smart_mode)."""
    if view == "library":
        return library, None, None
    if view.startswith("folder:"):
        fid = int(view.split(":", 1)[1])
        s.folders.get(fid)
        ids = s.folders.descendants(fid) if s.settings.get("show_subfolder_items") else {fid}
        return [p for p in library if ids & set(p.get("folder_ids") or [])], None, None
    if view.startswith("smart:"):
        sf = s.smart.get(int(view.split(":", 1)[1]))
        return library, sf["query"], sf["mode"]
    if view == "unfiled":
        return [p for p in library if not p.get("folder_ids")], None, None
    return s.papers.list(view), None, None


def sidebar(s) -> dict:
    return {"folders": s.folders.tree(), "tags": s.tags.list(), "smart": s.smart.list()}


@bp.get("/items")
def list_items():
    s = svc()
    view = request.args.get("view", "library")
    q = request.args.get("q", "").strip()
    mode = request.args.get("mode") or s.settings.get("default_search_mode")
    tag_filter = [t for t in request.args.get("tags", "").split(",") if t.strip()]
    library = s.papers.list()
    items, smart_q, smart_mode = items_for_view(s, view, library)
    ctx = search_context(s)
    hits = {}
    notes = s.note_service.text_matcher() if (q or smart_q) and s.settings.get("notes_enabled") else None
    if smart_q:
        items, hits = search_mod.search(items, smart_q, smart_mode, s.fulltext, ctx, notes=notes)
    if tag_filter:
        want = {search_mod.normalize(t) for t in tag_filter}
        items = [p for p in items if want <= {search_mod.normalize(t) for t in p.get("tags") or []}]
    if q:
        items, more = search_mod.search(items, q, mode, s.fulltext, ctx, notes=notes)
        hits = {**{k: v for k, v in hits.items() if k in {p["id"] for p in items}}, **more}
    return jsonify(items=items, hits=hits, counts=s.papers.counts(library), mode=mode, query=q,
                   sidebar=sidebar(s))


@bp.get("/search/preview")
def search_preview():
    """Count matches for a query (used by the smart-folder editor)."""
    s = svc()
    q = request.args.get("q", "").strip()
    mode = request.args.get("mode", "all")
    items, _ = search_mod.search(s.papers.list(), q, mode, s.fulltext, search_context(s)) if q else ([], {})
    return jsonify(count=len(items), sample=[{"id": p["id"], "title": p.get("title"), "year": p.get("year"),
                                              "authors": p.get("authors")} for p in items[:6]])


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
    s = svc()
    data = body()
    s.papers.get(item_id)
    if "tags" in data:
        s.tags.set_for_item(item_id, data.pop("tags") or [])
    if "folder_ids" in data:   # replaces the item's folder memberships
        s.folders.set_item_folders(item_id, data.pop("folder_ids") or [])
    return jsonify(s.papers.update(item_id, data))


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
