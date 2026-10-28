"""Public task records and note-integrity operations, guarded by task/note permissions."""

from __future__ import annotations

from ..changes import CLIENT_HEADER
from ..notes.vault import VaultError as NoteError
from ..tasks import TaskError, TaskMissing
from ..tasks.query import matcher
from ..tasks.service import token
from .errors import PluginError

__all__ = ["CLIENT_HEADER", "NoteError", "TaskError", "TaskMissing", "matcher", "token", "TaskAccess"]


class TaskRecords:
    def __init__(self, access):
        self._access = access

    def _call(self, method, permission, *args, **kwargs):
        self._access._need(permission)
        return getattr(self._access._s.task_store, method)(*args, **kwargs)

    def list(self, *args, **kwargs):
        return self._call("list", "read", *args, **kwargs)

    def get(self, *args, **kwargs):
        return self._call("get", "read", *args, **kwargs)

    def history(self, *args, **kwargs):
        return self._call("history", "read", *args, **kwargs)

    def create(self, *args, **kwargs):
        return self._call("create", "edit", *args, **kwargs)

    def update(self, *args, **kwargs):
        return self._call("update", "edit", *args, **kwargs)

    def move(self, *args, **kwargs):
        return self._call("move", "edit", *args, **kwargs)

    def set_status(self, *args, **kwargs):
        return self._call("set_status", "edit", *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._call("delete", "edit", *args, **kwargs)

    def restore(self, *args, **kwargs):
        return self._call("restore", "edit", *args, **kwargs)

    def undo(self, *args, **kwargs):
        return self._call("undo", "edit", *args, **kwargs)


class TaskAccess:
    def __init__(self, services, manifest):
        self._s, self._man = services, manifest
        self.records = TaskRecords(self)

    def _need(self, permission):
        if permission not in self._man.tasks:
            raise PluginError(f'{self._man.name} needs "tasks": ["{permission}"] in its manifest')

    def _notes_edit(self):
        if "edit" not in self._man.notes:
            raise PluginError(f'{self._man.name} needs "notes": ["edit"] in its manifest')

    def _notes_read(self):
        if "read" not in self._man.notes:
            raise PluginError(f'{self._man.name} needs "notes": ["read"] in its manifest')

    def overview(self):
        self._need("read")
        self._notes_read()
        return self._s.tasks.overview()

    def refs(self):
        self._need("read")
        self._notes_read()
        return self._s.tasks.refs()

    def task(self, task_id):
        self._need("read")
        self._notes_read()
        return self._s.tasks.task(task_id)

    def create(self, data, **kwargs):
        self._need("edit")
        if data.get("note"):
            self._notes_edit()
        return self._s.tasks.create(data, **kwargs)

    def add_to_note(self, *args, **kwargs):
        self._need("edit")
        self._notes_edit()
        return self._s.tasks.add_to_note(*args, **kwargs)

    def remove_from_note(self, *args, **kwargs):
        self._need("edit")
        self._notes_edit()
        return self._s.tasks.remove_from_note(*args, **kwargs)

    def convert(self, *args, **kwargs):
        self._need("edit")
        return self._s.tasks.convert(*args, **kwargs)

    def notes_changed(self, client_id=None):
        self._notes_edit()
        self._s.changes.record({"notes"}, client_id)

    def add_image(self, filename, data):
        self._need("edit")
        self._notes_edit()
        try:
            return self._s.note_service.add_image(filename, data)
        except ValueError as error:
            raise PluginError(str(error)) from error
