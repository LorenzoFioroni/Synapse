"""Markdown notes. Notes are addressed by their path inside the vault, e.g. ``papers/smith2021.md``."""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request, send_from_directory

from ..changes import CLIENT_HEADER
from ..notes.history import GitError
from ..notes.vault import ATTACHMENTS, Conflict, NoteExists, NoteMoved, VaultError
from . import body, error, failure, message_of, svc

bp = Blueprint("notes", __name__, url_prefix="/api")


@bp.after_request
def _record_change(resp):
    """Tell the other windows and devices that notes changed (see backend/changes.py)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and resp.status_code < 400:
        # recording the history changes no note (its commits announce themselves), except a milestone restored
        if request.path.startswith("/api/notes/history/") and not request.path.endswith("/restore"):
            return resp
        svc().changes.record({"notes"}, request.headers.get(CLIENT_HEADER))
    return resp


def guarded(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Conflict as e:
            return jsonify(error=f"{e.path} was changed elsewhere", conflict=True, current_hash=e.current_hash), 409
        except NoteExists as e:
            return jsonify(error=f"A note already exists at {e.path}", exists=True, path=e.path), 409
        except NoteMoved as e:
            return jsonify(error=f"{e.path} was moved to {e.to}", moved_to=e.to), 404
        except VaultError as e:
            return error(message_of(e) if e.args else type(e).__name__, e.status)
        except ValueError as e:
            return failure(e)

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
    return jsonify(svc().note_service.get(_path_arg(), unless=request.args.get("unless") or None))


@bp.post("/notes/note")
@guarded
def create_note():
    d = body()
    res = svc().note_service.create(
        path=d.get("path") or None,
        folder=d.get("folder") or "",
        title=(d.get("title") or "").strip(),
        item_id=d.get("item_id") or None,
        text=d.get("text"),
    )
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


@bp.post("/notes/folder/move")
@guarded
def move_folder():
    d = body()
    return jsonify(svc().note_service.move_folder(d.get("path") or "", d.get("to") or ""))


@bp.put("/notes/folder/color")
@guarded
def folder_color():
    """``{path, color}``: a notes folder's colour (``#rrggbb``; null or "" for none)."""
    d = body()
    return jsonify(svc().note_service.set_folder_color(d.get("path") or "", d.get("color")))


@bp.post("/notes/trash")
@guarded
def trash_note():
    d = body()
    if "paths" in d or "folders" in d:  # several notes and/or folders, restored together by one undo
        return jsonify(svc().note_service.trash_many(list(d.get("paths") or []), list(d.get("folders") or [])))
    return jsonify(svc().note_service.trash(_path_arg()))


@bp.get("/notes/trash")
@guarded
def trashed_notes():
    """Deleted notes, with when they will be deleted for good (Settings → notes trash)."""
    return jsonify(
        trash=svc().attachments.with_deletion_dates(svc().vault.trashed()),
        retention=svc().settings.get("notes_trash_retention"),
    )


@bp.post("/notes/restore")
@guarded
def restore_note():
    d = body()
    if not d.get("path"):  # everything deleted together
        return jsonify(svc().note_service.restore_batch(d.get("trash_id") or ""))
    return jsonify(svc().note_service.restore(d.get("trash_id") or "", d.get("path") or ""))


@bp.get("/notes/history")
@guarded
def note_history():
    """A note's versions, newest first (without their text)."""
    try:
        limit = int(request.args.get("limit") or 200)
    except ValueError:
        return error("limit must be a number")
    return jsonify(svc().note_service.history_of(_path_arg(), limit))


@bp.get("/notes/history/version")
@guarded
def note_history_version():
    """One version: the note's text after the change and before it."""
    a = request.args
    try:
        return jsonify(svc().note_service.history_version(a.get("id") or "", a.get("path"), a.get("from")))
    except KeyError:
        return error("No such version", 404)


@bp.get("/notes/history/status")
@guarded
def history_status():
    """Whether the notes history is on (Git found), for Settings."""
    return jsonify(svc().note_service.history_status())


@bp.get("/notes/history/log")
@guarded
def history_log():
    """Every recorded change to the notes, newest first, and the milestones."""
    a = request.args
    try:
        limit, skip = int(a.get("limit") or 100), int(a.get("skip") or 0)
    except ValueError:
        return error("limit and skip must be numbers")
    return jsonify(svc().note_service.history_log(limit, skip))


@bp.get("/notes/history/diff")
@guarded
def history_diff():
    """The notes that differ between two states: ``from``/``to`` are commit ids, ``<id>^`` (the
    state before a commit), milestone ids or ``now``."""
    a = request.args
    try:
        return jsonify(svc().note_service.history_diff(a.get("from") or "", a.get("to") or "now"))
    except GitError:
        return error("No such version", 404)


@bp.post("/notes/history/checkpoint")
@guarded
def history_checkpoint():
    """``{path?, restored_from?}``: record what is waiting now (a note was closed, or an earlier
    version was put back)."""
    d = body()
    return jsonify(svc().note_service.checkpoint(d.get("path") or None, d.get("restored_from") or None))


@bp.post("/notes/history/milestones")
@guarded
def add_milestone():
    return jsonify(svc().note_service.add_milestone(body().get("label") or "")), 201


@bp.put("/notes/history/milestones/<mid>")
@guarded
def rename_milestone(mid: str):
    try:
        return jsonify(svc().note_service.rename_milestone(mid, body().get("label") or ""))
    except GitError:
        return error("No such milestone", 404)


@bp.delete("/notes/history/milestones/<mid>")
@guarded
def delete_milestone(mid: str):
    try:
        return jsonify(svc().note_service.delete_milestone(mid))
    except GitError:
        return error("No such milestone", 404)


@bp.post("/notes/history/milestones/<mid>/restore")
@guarded
def restore_milestone(mid: str):
    """Put every note back as it was at the milestone (one change in the history)."""
    return jsonify(svc().note_service.restore_milestone(mid))


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


@bp.get("/notes/keys")
@guarded
def key_summary():
    """Which citation keys have a paper note / are cited in notes (for the item list)."""
    return jsonify(keys=svc().note_service.key_summary())


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


@bp.get("/notes/attachments")
@guarded
def list_attachments():
    """Every attachment with the notes that show it, and the attachment trash."""
    return jsonify(svc().attachments.overview())


@bp.post("/notes/attachments/restore")
@guarded
def restore_attachment():
    d = body()
    try:
        return jsonify(path=svc().attachments.restore(d.get("trash_id") or "", d.get("name") or ""))
    except FileNotFoundError:
        return error("That attachment is no longer in the trash", 404)


@bp.post("/notes/attachments/delete")
@guarded
def delete_attachment():
    """Delete a trashed attachment for good."""
    d = body()
    try:
        svc().attachments.delete(d.get("trash_id") or "", d.get("name") or "")
    except FileNotFoundError:
        return error("That attachment is no longer in the trash", 404)
    return jsonify(ok=True)


@bp.post("/notes/attachments/clean")
@guarded
def clean_attachments():
    """Move unused attachments to their trash and empty old trash now (it also runs hourly)."""
    a = svc().attachments
    return jsonify(**a.sweep(), purged=a.purge())


@bp.get("/notes/attachments/trash/<trash_id>/<path:name>")
@guarded
def get_trashed_attachment(trash_id, name):
    try:
        p = svc().attachments.trashed_path(trash_id, name)
    except FileNotFoundError:
        return error("Not found", 404)
    resp = send_from_directory(p.parent, p.name)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


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
