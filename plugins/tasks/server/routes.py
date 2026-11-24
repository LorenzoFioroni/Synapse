"""The Tasks plugin's HTTP interface; records and note integrity remain in backend.tasks."""

from __future__ import annotations

from functools import wraps

from flask import jsonify, request

from backend.plugins import Plugin, replay
from backend.plugins import tasks as task_api


def register(plugin: Plugin) -> None:
    def body():
        return request.get_json(silent=True) or {}

    def error(message, status=400):
        return jsonify(error=message), status

    def failure(e: Exception, status: int = 400):
        # Task and note errors are raised with one sentence for the user; nothing else is shown.
        args = e.args
        shown = args[0] if len(args) == 1 and isinstance(args[0], str) and args[0].strip() else type(e).__name__
        return error(shown, status)

    def _notes_changed() -> None:
        """A {{task:…}} line was written into or taken out of a note."""
        plugin.tasks.notes_changed(request.headers.get(task_api.CLIENT_HEADER))

    def _alive(task_id) -> bool:
        """The task exists and is not in the trash."""
        try:
            return not plugin.tasks.records.get(str(task_id)).get("deleted_at")
        except task_api.TaskMissing:
            return False

    def guarded(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except task_api.TaskMissing as e:
                return failure(e, 404)
            except task_api.TaskError as e:
                return failure(e)
            except task_api.NoteError as e:
                return failure(e, e.status)

        return wrapper

    @plugin.route("/", methods=["GET"])
    @guarded
    def overview():
        return jsonify(plugin.tasks.overview())

    @plugin.route("/trash", methods=["GET"])
    @guarded
    def trash():
        return jsonify(tasks=plugin.tasks.records.list(trashed=True))

    @plugin.route("/<task_id>", methods=["GET"])
    @guarded
    def get_task(task_id):
        return jsonify(task=plugin.tasks.task(task_id))

    @plugin.route("/", methods=["POST"])
    @guarded
    def create():
        d = body()
        try:
            out = plugin.tasks.create(d)
        except task_api.TaskExists:
            if replay.replayed_at() is None:
                raise
            return jsonify(task=plugin.tasks.records.get(d["id"]))  # created offline, already sent once
        if out.get("ref"):
            _notes_changed()
        return jsonify(out), 201

    @plugin.route("/<task_id>", methods=["PATCH"])
    @guarded
    def update(task_id):
        """Fields (title, body, status, due); ``with_subtasks`` with a status; a move
        with ``parent_id`` (null: top level) and ``before`` or ``after`` a sibling."""
        d = body()
        store = plugin.tasks.records
        changed: list[dict] = []
        base = d.pop("base", None)
        clashes = {}
        if replay.replayed_at() is not None and isinstance(base, dict) and not replay.forced():
            # changed offline: fields changed here meanwhile (to something else) ask; the rest merge
            clashes = store.offline_clashes(task_id, d, base)
            d = {k: v for k, v in d.items() if k not in clashes}
        if replay.replayed_at() is not None and d.get("parent_id") and not _alive(d["parent_id"]):
            # moved offline under a task deleted meanwhile: it stays where it is
            d = {k: v for k, v in d.items() if k not in ("parent_id", "before", "after")}
        if "parent_id" in d or "before" in d or "after" in d:
            changed.append(
                store.move(task_id, parent_id=d.get("parent_id"), before=d.get("before"), after=d.get("after"))
            )
        fields = {k: d[k] for k in ("title", "body", "due") if k in d}
        if fields:
            changed.append(store.update(task_id, fields))
        if "status" in d:
            changed.extend(store.set_status(task_id, d["status"], with_subtasks=bool(d.get("with_subtasks"))))
        if clashes:
            names = {"title": "title", "body": "details", "due": "due date", "status": "done or not"}
            title = store.get(task_id)["title"]
            what = ", ".join(names[k] for k in clashes)
            return replay.conflict(
                f"“{title}” was also changed in Synapse while you were offline ({what}).",
                retry={k: v["mine"] for k, v in clashes.items()},
                fields=clashes,
            )
        return jsonify(task=store.get(task_id), changed=changed)

    @plugin.route("/delete", methods=["POST"])
    @guarded
    def delete():
        d = body()
        ids = [str(i) for i in (d.get("ids") or ([d["id"]] if d.get("id") else []))]
        batch = d.get("batch") if isinstance(d.get("batch"), str) else None
        if replay.replayed_at() is not None:
            ids = [i for i in ids if _alive(i)]  # deleted offline, and meanwhile here: done already
            if not ids:
                return jsonify(batch=batch, ids=[])
        if not ids:
            return error("No tasks given.")
        return jsonify(plugin.tasks.records.delete(ids, batch=batch))

    @plugin.route("/restore", methods=["POST"])
    @guarded
    def restore():
        d = body()
        return jsonify(ids=plugin.tasks.records.restore(batch=d.get("batch"), task_id=d.get("id")))

    @plugin.route("/<task_id>/notes", methods=["POST"])
    @guarded
    def add_to_note(task_id):
        d = body()
        if not d.get("path"):
            return error("Which note?")
        ref = plugin.tasks.add_to_note(task_id, d["path"], after_line=d.get("after_line"))
        _notes_changed()
        return jsonify(ref=ref)

    @plugin.route("/<task_id>/notes/remove", methods=["POST"])
    @guarded
    def remove_from_note(task_id):
        d = body()
        if not d.get("path"):
            return error("Which note?")
        out = plugin.tasks.remove_from_note(task_id, d["path"])
        _notes_changed()
        return jsonify(out)

    @plugin.route("/convert", methods=["POST"])
    @guarded
    def convert():
        """Checkbox lines (from the editor) to tasks; the editor puts the returned text in their place."""
        return jsonify(plugin.tasks.convert(str(body().get("text") or "")))

    @plugin.route("/<task_id>/history", methods=["GET"])
    @guarded
    def history(task_id):
        return jsonify(history=plugin.tasks.records.history(task_id))

    @plugin.route("/undo", methods=["POST"])
    @guarded
    def undo():
        hid = body().get("history_id")
        if hid is None:
            return error("Which change?")
        return jsonify(task=plugin.tasks.records.undo(int(hid)))

    @plugin.route("/images", methods=["POST"])
    @guarded
    def upload_image():
        image = request.files.get("file")
        if not image:
            return error("No file uploaded")
        return jsonify(plugin.tasks.add_image(image.filename or "", image.read())), 201
