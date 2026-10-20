"""Tasks (records in library.db) and the {{task:id}} lines that show them in notes."""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request

from ..changes import CLIENT_HEADER
from ..notes.vault import VaultError
from ..tasks import TaskError, TaskMissing
from . import body, error, svc

bp = Blueprint("tasks", __name__, url_prefix="/api")


def _notes_changed() -> None:
    """A {{task:…}} line was written into or taken out of a note."""
    svc().changes.record({"notes"}, request.headers.get(CLIENT_HEADER))


def guarded(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except TaskMissing as e:
            return error(str(e), 404)
        except TaskError as e:
            return error(str(e))
        except VaultError as e:
            return error(str(e) if e.args else type(e).__name__, e.status)

    return wrapper


@bp.get("/tasks")
@guarded
def overview():
    return jsonify(svc().tasks.overview())


@bp.get("/tasks/trash")
@guarded
def trash():
    return jsonify(tasks=svc().task_store.list(trashed=True))


@bp.get("/tasks/<task_id>")
@guarded
def get_task(task_id):
    return jsonify(task=svc().tasks.task(task_id))


@bp.post("/tasks")
@guarded
def create():
    out = svc().tasks.create(body())
    if out.get("ref"):
        _notes_changed()
    return jsonify(out), 201


@bp.patch("/tasks/<task_id>")
@guarded
def update(task_id):
    """Fields (title, body, status, due, priority, tags); ``with_subtasks`` with a status; a move
    with ``parent_id`` (null: top level) and ``before`` or ``after`` a sibling."""
    d = body()
    store = svc().task_store
    changed: list[dict] = []
    if "parent_id" in d or "before" in d or "after" in d:
        changed.append(store.move(task_id, parent_id=d.get("parent_id"), before=d.get("before"), after=d.get("after")))
    fields = {k: d[k] for k in ("title", "body", "due", "priority", "tags") if k in d}
    if fields:
        changed.append(store.update(task_id, fields))
    if "status" in d:
        changed.extend(store.set_status(task_id, d["status"], with_subtasks=bool(d.get("with_subtasks"))))
    return jsonify(task=store.get(task_id), changed=changed)


@bp.post("/tasks/delete")
@guarded
def delete():
    d = body()
    ids = [str(i) for i in (d.get("ids") or ([d["id"]] if d.get("id") else []))]
    if not ids:
        return error("No tasks given.")
    return jsonify(svc().task_store.delete(ids))


@bp.post("/tasks/restore")
@guarded
def restore():
    d = body()
    return jsonify(ids=svc().task_store.restore(batch=d.get("batch"), task_id=d.get("id")))


@bp.post("/tasks/<task_id>/notes")
@guarded
def add_to_note(task_id):
    d = body()
    if not d.get("path"):
        return error("Which note?")
    ref = svc().tasks.add_to_note(task_id, d["path"], after_line=d.get("after_line"))
    _notes_changed()
    return jsonify(ref=ref)


@bp.post("/tasks/<task_id>/notes/remove")
@guarded
def remove_from_note(task_id):
    d = body()
    if not d.get("path"):
        return error("Which note?")
    out = svc().tasks.remove_from_note(task_id, d["path"])
    _notes_changed()
    return jsonify(out)


@bp.post("/tasks/convert")
@guarded
def convert():
    """Checkbox lines (from the editor) to tasks; the editor puts the returned text in their place."""
    return jsonify(svc().tasks.convert(str(body().get("text") or "")))


@bp.get("/tasks/<task_id>/history")
@guarded
def history(task_id):
    return jsonify(history=svc().task_store.history(task_id))


@bp.post("/tasks/undo")
@guarded
def undo():
    hid = body().get("history_id")
    if hid is None:
        return error("Which change?")
    return jsonify(task=svc().task_store.undo(int(hid)))
