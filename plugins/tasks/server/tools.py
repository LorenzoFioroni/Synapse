"""Tools for tasks: list_tasks, get_task, create_task, update_task, delete_task.

Tasks are records (title, status, due date, a Markdown body, sub-tasks); a note
shows one with a ``{{task:<id>}}`` line. Changes made here are marked as Claude's in the task's
history, and a task added to a note goes through the note's history, so the user can undo both.
"""

from __future__ import annotations

from backend.mcp.protocol import ToolError
from backend.mcp.tools.common import ToolsBase, drop_empty
from backend.tasks import TaskError
from backend.tasks.query import matcher
from backend.tasks.service import token

MAX_LISTED = 200


class TaskToolsMixin(ToolsBase):
    def _task_ref(self, task: str) -> str:
        t = str(task or "").strip()
        if t.startswith("{{task:") and t.endswith("}}"):
            t = t[7:-2]
        if not t:
            raise ToolError("Which task? Give its id (list_tasks shows them).")
        return t

    def _task_out(self, t: dict, refs: dict, children: dict, *, full: bool = False) -> dict:
        kids = children.get(t["id"], [])
        out = {
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "due": t.get("due"),
            "parent": t.get("parent_id"),
            "subtasks": f"{sum(k['status'] == 'done' for k in kids)}/{len(kids)} done" if kids else None,
            "has_details": bool(t.get("body")) or None,
            "in_notes": [
                drop_empty({"note": r["path"], "line": r["line"], "heading": r.get("heading")})
                for r in refs.get(t["id"], [])
            ]
            or None,
            "by_claude": t.get("created_by") == "ai" or None,
        }
        if full:
            out["details"] = t.get("body") or None
            out["subtasks"] = [
                {"id": k["id"], "title": k["title"], "status": k["status"], "due": k.get("due")} for k in kids
            ] or None
            out["created"] = t.get("created_at")
            out["done_at"] = t.get("done_at")
        return drop_empty(out)

    def _children(self, tasks: list[dict]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for t in tasks:
            if t.get("parent_id"):
                out.setdefault(t["parent_id"], []).append(t)
        return out

    def list_tasks(self, query: str = "", include_done: bool = False, limit: int = 100):
        tasks = self.s.task_store.list()
        refs = self.s.tasks.refs()
        children = self._children(tasks)
        test = matcher(query or "", refs=refs)
        rows = []
        for t in tasks:
            t["has_subtasks"] = t["id"] in children
            if not include_done and t["status"] == "done" and "is:done" not in (query or ""):
                continue
            if test(t):
                rows.append(t)
        rows.sort(key=lambda t: (t.get("due") or "9999", t.get("parent_id") or "", t["position"]))
        limit = max(1, min(int(limit or 100), MAX_LISTED))
        return drop_empty(
            {
                "count": len(rows),
                "tasks": [self._task_out(t, refs, children) for t in rows[:limit]],
                "more": len(rows) - limit if len(rows) > limit else None,
                "hint": "In a note a task is the line {{task:ID}}; get_task shows one with its details.",
            }
        )

    def get_task(self, task: str):
        tid = self._task_ref(task)
        try:
            t = self.s.task_store.get(tid)
        except TaskError as e:
            raise ToolError(str(e)) from e
        tasks = self.s.task_store.list()
        out = self._task_out(t, self.s.tasks.refs(), self._children(tasks), full=True)
        if t.get("deleted_at"):
            out["in_trash"] = True
        chain: list[dict] = []
        p = t.get("parent_id")
        while p:
            pt = self.s.task_store.get(p)
            chain.insert(0, {"id": pt["id"], "title": pt["title"]})
            p = pt.get("parent_id")
        if chain:
            out["parents"] = chain
        return out

    def create_task(
        self,
        title: str,
        details: str = "",
        due: str | None = None,
        parent: str | None = None,
        note: str | None = None,
        after_line: int | None = None,
    ):
        data: dict = {
            "title": title,
            "body": details or "",
            "due": due,
            "parent_id": self._task_ref(parent) if parent else None,
        }
        if note:
            data["note"] = self._note_path(note)
            data["after_line"] = after_line
        try:
            out = self.s.tasks.create(data, by="ai")
        except TaskError as e:
            raise ToolError(str(e)) from e
        t = out["task"]
        return drop_empty(
            {
                "created": t["id"],
                "line": token(t["id"]),
                "in_note": out.get("ref"),
                "hint": None if note else "Not shown in any note; pass note= to add it to one, or write its line.",
            }
        )

    def update_task(
        self,
        task: str,
        title: str | None = None,
        details: str | None = None,
        status: str | None = None,
        due: str | None = None,
        parent: str | None = None,
        with_subtasks: bool = False,
    ):
        tid = self._task_ref(task)
        store = self.s.task_store
        fields: dict = {}
        for k, v in (("title", title), ("body", details), ("due", due)):
            if v is not None:
                fields[k] = None if v == "" and k == "due" else v
        try:
            if parent is not None:
                store.move(tid, parent_id=self._task_ref(parent) if parent else None, by="ai")
            if fields:
                store.update(tid, fields, by="ai")
            changed = store.set_status(tid, status, with_subtasks=with_subtasks, by="ai") if status else []
        except TaskError as e:
            raise ToolError(str(e)) from e
        t = store.get(tid)
        return drop_empty(
            {
                "updated": tid,
                "title": t["title"],
                "status": t["status"],
                "also_changed": [c["id"] for c in changed if c["id"] != tid] or None,
            }
        )

    def delete_task(self, task: str):
        tid = self._task_ref(task)
        try:
            out = self.s.task_store.delete([tid], by="ai")
        except TaskError as e:
            raise ToolError(str(e)) from e
        return {
            "deleted": out["ids"],
            "note": "Moved to the trash with its sub-tasks; the user can restore it. Lines showing it in notes stay "
            "and show it as deleted.",
        }
