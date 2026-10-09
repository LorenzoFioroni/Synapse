"""Markdown notes. Notes are addressed by their path inside the vault, e.g. ``papers/smith2021.md``."""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request, send_from_directory

from ..notes.vault import ATTACHMENTS, Conflict, NoteExists, VaultError
from . import body, error, svc

bp = Blueprint("notes", __name__, url_prefix="/api")


def guarded(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Conflict as e:
            return jsonify(error=str(e), conflict=True, current_hash=e.current_hash), 409
        except NoteExists as e:
            return jsonify(error=f"A note already exists at {e}", exists=True, path=str(e)), 409
        except VaultError as e:
            return error(str(e) if e.args else type(e).__name__, e.status)
        except ValueError as e:
            return error(str(e))
    return wrapper


def _path_arg() -> str:
    return request.args.get("path") or body().get("path") or ""


@bp.get("/notes")
@guarded
def overview():
    return jsonify(svc().note_service.overview())


@bp.get("/notes/note")
@guarded
def get_note():
    return jsonify(svc().note_service.get(_path_arg()))


@bp.post("/notes/note")
@guarded
def create_note():
    d = body()
    res = svc().note_service.create(path=d.get("path") or None, folder=d.get("folder") or "",
                                    title=(d.get("title") or "").strip(), item_id=d.get("item_id") or None,
                                    text=d.get("text"))
    return jsonify(res), 201


@bp.put("/notes/note")
@guarded
def save_note():
    d = body()
    if not isinstance(d.get("text"), str):
        return error("text is required")
    if d.get("create"):  # write exactly this text to a path that must not exist (e.g. deleted elsewhere)
        return jsonify(svc().note_service.save(d.get("path") or "", d["text"], None))
    if not d.get("hash"):
        return error("hash is required")
    return jsonify(svc().note_service.save(d.get("path") or "", d["text"], d["hash"]))


@bp.post("/notes/move")
@guarded
def move_note():
    d = body()
    return jsonify(svc().note_service.move(d.get("path") or "", d.get("to") or "", d.get("hash") or None))


@bp.post("/notes/folder")
@guarded
def make_folder():
    return jsonify(svc().note_service.make_folder(body().get("path") or "")), 201


@bp.post("/notes/trash")
@guarded
def trash_note():
    return jsonify(svc().note_service.trash(_path_arg()))


@bp.get("/notes/trash")
@guarded
def trashed_notes():
    return jsonify(trash=svc().vault.trashed())


@bp.post("/notes/restore")
@guarded
def restore_note():
    d = body()
    return jsonify(svc().note_service.restore(d.get("trash_id") or "", d.get("path") or ""))


@bp.get("/notes/search")
@guarded
def search_notes():
    return jsonify(results=svc().note_service.search(request.args.get("q") or ""))


@bp.get("/notes/links")
@guarded
def note_links():
    return jsonify(svc().note_service.links(_path_arg()))


@bp.get("/notes/rename_plan")
@guarded
def rename_plan():
    """What changing a citation key would change in the notes (nothing is written)."""
    return jsonify(svc().note_service.rename_plan(request.args.get("old") or "", request.args.get("new") or ""))


@bp.get("/notes/cite_index")
@guarded
def cite_index():
    return jsonify(items=svc().note_service.cite_index())


@bp.get("/items/<item_id>/notes")
@guarded
def item_notes(item_id):
    return jsonify(svc().note_service.item_notes(item_id))


@bp.post("/notes/append")
@guarded
def append_to_paper_note():
    d = body()
    return jsonify(svc().note_service.append_to_paper_note(d.get("item_id") or "", d.get("markdown") or ""))


@bp.post("/notes/attachments")
@guarded
def upload_image():
    f = request.files.get("file")
    if not f:
        return error("No file uploaded")
    return jsonify(svc().note_service.add_image(f.filename or "", f.read())), 201


@bp.get("/notes/attachments/<path:name>")
@guarded
def get_attachment(name):
    v = svc().vault
    rel = v.clean_rel(f"{ATTACHMENTS}/{name}", suffix=None)
    resp = send_from_directory(v.root, rel)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"  # names are never reused
    return resp


@bp.post("/notes/rescan")
@guarded
def rescan():
    return jsonify(svc().note_service.rescan())
