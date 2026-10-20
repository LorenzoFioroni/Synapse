"""Tasks: records in ``library.db``, each with a title, a status (open or done), a due date, a
Markdown body and sub-tasks (a tree through ``parent_id``).

A note shows a task with ``{{task:<id>}}`` (see ``backend/notes/parser.py``); the task itself lives
here, so ticking it or editing its body never changes the note. Deleting moves a task (with its
sub-tasks) to the trash, from which it can be restored until the retention runs out. Every change
is recorded in ``task_history`` with the values before and after, which gives Undo and tells the
app which changes Claude made.
"""

from __future__ import annotations

import builtins
import json
import re
import secrets
import sqlite3
from datetime import date, datetime, timezone

from ..library.db import Database, utcnow

STATUSES = ("open", "done")
ID_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: ids are read and typed by people
ID_LEN = 8
MAX_TITLE = 500
MAX_BODY = 200_000
HISTORY_KEPT = 5000
EDITABLE = ("title", "body", "status", "due")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    position REAL NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    due TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    done_at TEXT,
    created_by TEXT NOT NULL DEFAULT 'human',
    updated_by TEXT NOT NULL DEFAULT 'human',
    deleted_at TEXT,
    deleted_batch TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_deleted ON tasks(deleted_at);
CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    at TEXT NOT NULL,
    by TEXT NOT NULL,
    action TEXT NOT NULL,
    before TEXT,
    after TEXT,
    undone INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id);
"""


class TaskError(ValueError):
    """A change that cannot be made; the message can be shown to the user."""


class TaskMissing(TaskError):
    """No task with this id (or it is in the trash when that matters)."""


def apply_schema(c: sqlite3.Connection) -> None:
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            c.execute(stmt)


def new_id() -> str:
    first = secrets.choice(ID_CHARS[:23])  # a letter first: never mistaken for a number
    return first + "".join(secrets.choice(ID_CHARS) for _ in range(ID_LEN - 1))


def _row(r: sqlite3.Row) -> dict:
    return dict(r)


def clean_title(v) -> str:
    t = " ".join(str(v or "").split())  # one line: a title is shown on one line in notes
    if len(t) > MAX_TITLE:
        raise TaskError(f"A task title can have at most {MAX_TITLE} characters.")
    return t


def clean_due(v) -> str | None:
    if v in (None, ""):
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            pass
    raise TaskError(f"'{s}' is not a date. Use YYYY-MM-DD, e.g. 2026-10-27.")


def _clean(field: str, v):
    if field == "title":
        return clean_title(v)
    if field == "body":
        b = str(v or "").replace("\r\n", "\n").replace("\r", "\n")
        if len(b) > MAX_BODY:
            raise TaskError("The task's details are too long.")
        return b
    if field == "status":
        s = str(v or "").strip().lower()
        if s not in STATUSES:
            raise TaskError(f"Unknown status '{v}': use {' or '.join(STATUSES)}.")
        return s
    if field == "due":
        return clean_due(v)
    raise TaskError(f"Unknown field '{field}'.")


def _snapshot(t: dict) -> dict:
    return {k: t.get(k) for k in (*EDITABLE, "parent_id", "position", "done_at", "deleted_at")}


class TaskStore:
    def __init__(self, db: Database):
        self.db = db
        db.add_schema(apply_schema)
        with db.session() as c:
            apply_schema(c)

    # ------------------------------------------------------------------ reads
    def list(self, *, trashed: bool = False) -> builtins.list[dict]:
        """Every task (or every trashed one), in tree order is up to the caller: parent, position."""
        cond = "deleted_at IS NOT NULL" if trashed else "deleted_at IS NULL"
        with self.db.session() as c:
            return [_row(r) for r in c.execute(f"SELECT * FROM tasks WHERE {cond} ORDER BY position, created_at")]

    def get(self, task_id: str, *, trashed_ok: bool = True) -> dict:
        with self.db.session() as c:
            r = c.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
        if not r or (not trashed_ok and r["deleted_at"]):
            raise TaskMissing("This task no longer exists." if not r else "This task is in the trash.")
        return _row(r)

    def get_many(self, ids) -> dict[str, dict]:
        ids = [str(i) for i in ids]
        if not ids:
            return {}
        with self.db.session() as c:
            q = ",".join("?" * len(ids))
            return {r["id"]: _row(r) for r in c.execute(f"SELECT * FROM tasks WHERE id IN ({q})", ids)}

    def children(self, task_id: str | None, c: sqlite3.Connection | None = None) -> builtins.list[dict]:
        def run(conn):
            if task_id is None:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE parent_id IS NULL AND deleted_at IS NULL ORDER BY position"
                )
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE parent_id = ? AND deleted_at IS NULL ORDER BY position", (task_id,)
                )
            return [_row(r) for r in rows]

        if c is not None:
            return run(c)
        with self.db.session() as conn:
            return run(conn)

    def _subtree(self, c: sqlite3.Connection, task_id: str, *, include_deleted: bool = False) -> builtins.list[str]:
        """``task_id`` and every task below it."""
        out, todo = [task_id], [task_id]
        while todo:
            q = ",".join("?" * len(todo))
            extra = "" if include_deleted else " AND deleted_at IS NULL"
            todo = [r["id"] for r in c.execute(f"SELECT id FROM tasks WHERE parent_id IN ({q}){extra}", todo)]
            out.extend(todo)
        return out

    def bodies(self) -> builtins.list[tuple[str, str]]:
        """(task id, body) for every task with a body, trashed ones too (their images stay in use)."""
        with self.db.session() as c:
            return [(r["id"], r["body"]) for r in c.execute("SELECT id, body FROM tasks WHERE body != ''")]

    def history(self, task_id: str, limit: int = 50) -> builtins.list[dict]:
        with self.db.session() as c:
            rows = c.execute(
                "SELECT * FROM task_history WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task_id, limit)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["before"] = json.loads(d["before"]) if d["before"] else None
            d["after"] = json.loads(d["after"]) if d["after"] else None
            d["undone"] = bool(d["undone"])
            out.append(d)
        return out

    # ------------------------------------------------------------------ helpers
    def _log(self, c, task_id: str, by: str, action: str, before: dict | None, after: dict | None) -> int:
        cur = c.execute(
            "INSERT INTO task_history (task_id, at, by, action, before, after) VALUES (?,?,?,?,?,?)",
            (
                task_id,
                utcnow(),
                by,
                action,
                json.dumps(before) if before is not None else None,
                json.dumps(after) if after is not None else None,
            ),
        )
        return int(cur.lastrowid or 0)

    def _prune_history(self, c) -> None:
        c.execute(
            "DELETE FROM task_history WHERE id <= (SELECT id FROM task_history ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (HISTORY_KEPT,),
        )

    def _check_parent(self, c, task_id: str | None, parent_id: str | None) -> None:
        if parent_id is None:
            return
        r = c.execute("SELECT id, deleted_at FROM tasks WHERE id = ?", (parent_id,)).fetchone()
        if not r or r["deleted_at"]:
            raise TaskMissing("The parent task no longer exists.")
        if task_id and parent_id in self._subtree(c, task_id):
            raise TaskError("A task cannot be moved under itself or one of its sub-tasks.")

    def _position(self, c, parent_id: str | None, before: str | None = None, after: str | None = None) -> float:
        """A position among ``parent_id``'s children: before/after a sibling, else at the end."""
        sibs = [
            (r["id"], r["position"])
            for r in c.execute(
                "SELECT id, position FROM tasks WHERE deleted_at IS NULL AND "
                + ("parent_id IS NULL" if parent_id is None else "parent_id = ?")
                + " ORDER BY position",
                () if parent_id is None else (parent_id,),
            )
        ]
        ids = [s[0] for s in sibs]
        anchor = before or after
        if not anchor or anchor not in ids:
            return (sibs[-1][1] + 1) if sibs else 1.0
        i = ids.index(anchor)
        if after:
            lo = sibs[i][1]
            hi = sibs[i + 1][1] if i + 1 < len(sibs) else lo + 2
        else:
            hi = sibs[i][1]
            lo = sibs[i - 1][1] if i > 0 else hi - 2
        if hi - lo < 1e-9:  # renumber when positions got too close
            for n, sid in enumerate(ids):
                c.execute("UPDATE tasks SET position = ? WHERE id = ?", (float(n + 1), sid))
            return self._position(c, parent_id, before, after)
        return (lo + hi) / 2

    # ------------------------------------------------------------------ writes
    def create(
        self,
        title: str,
        *,
        body: str = "",
        parent_id: str | None = None,
        due=None,
        status: str = "open",
        before: str | None = None,
        after: str | None = None,
        by: str = "human",
    ) -> dict:
        title = clean_title(title)
        if not title:
            raise TaskError("A task needs a title.")
        values = {
            "body": _clean("body", body),
            "due": clean_due(due),
            "status": _clean("status", status),
        }
        now = utcnow()
        parent_id = str(parent_id) if parent_id else None
        with self.db.session() as c:
            self._check_parent(c, None, parent_id)
            tid = new_id()
            while c.execute("SELECT 1 FROM tasks WHERE id = ?", (tid,)).fetchone():
                tid = new_id()
            pos = self._position(c, parent_id, before, after)
            c.execute(
                "INSERT INTO tasks (id, parent_id, position, title, body, status, due, created_at, "
                "updated_at, done_at, created_by, updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tid,
                    parent_id,
                    pos,
                    title,
                    values["body"],
                    values["status"],
                    values["due"],
                    now,
                    now,
                    now if values["status"] == "done" else None,
                    by,
                    by,
                ),
            )
            task = _row(c.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())
            self._log(c, tid, by, "create", None, _snapshot(task))
            self._prune_history(c)
        return task

    def update(self, task_id: str, changes: dict, *, by: str = "human") -> dict:
        """Change any of title, body, status, due. Returns the task."""
        unknown = set(changes) - set(EDITABLE)
        if unknown:
            raise TaskError(f"Unknown field(s): {', '.join(sorted(unknown))}.")
        clean = {k: _clean(k, v) for k, v in changes.items()}
        if "title" in clean and not clean["title"]:
            raise TaskError("A task needs a title.")
        with self.db.session() as c:
            r = c.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
            if not r or r["deleted_at"]:
                raise TaskMissing("This task no longer exists." if not r else "This task is in the trash.")
            before = _row(r)
            sets = {k: v for k, v in clean.items() if before.get(k) != v}
            if not sets:
                return before
            if "status" in sets:
                sets["done_at"] = utcnow() if sets["status"] == "done" else None
            sets["updated_at"] = utcnow()
            sets["updated_by"] = by
            c.execute(
                f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                (*sets.values(), before["id"]),
            )
            after = _row(c.execute("SELECT * FROM tasks WHERE id = ?", (before["id"],)).fetchone())
            self._log(c, before["id"], by, "update", _snapshot(before), _snapshot(after))
            self._prune_history(c)
        return after

    def set_status(
        self, task_id: str, status: str, *, with_subtasks: bool = False, by: str = "human"
    ) -> builtins.list[dict]:
        """Open or close a task; with ``with_subtasks`` its sub-tasks (all levels) get the same status.
        Returns every task that changed."""
        status = _clean("status", status)
        with self.db.session() as c:
            ids = self._subtree(c, str(task_id)) if with_subtasks else [str(task_id)]
        changed = []
        for i in ids:
            t = self.get(i)
            if t["deleted_at"]:
                if i == str(task_id):
                    raise TaskMissing("This task is in the trash.")
                continue
            if t["status"] != status:
                changed.append(self.update(i, {"status": status}, by=by))
        return changed

    def move(
        self,
        task_id: str,
        *,
        parent_id: str | None = None,
        before: str | None = None,
        after: str | None = None,
        by: str = "human",
    ) -> dict:
        """Put a task under ``parent_id`` (None: top level), before or after one of its siblings."""
        parent_id = str(parent_id) if parent_id else None
        with self.db.session() as c:
            r = c.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
            if not r or r["deleted_at"]:
                raise TaskMissing("This task no longer exists.")
            prev = _row(r)
            self._check_parent(c, prev["id"], parent_id)
            pos = self._position(
                c, parent_id, before if before != prev["id"] else None, after if after != prev["id"] else None
            )
            c.execute(
                "UPDATE tasks SET parent_id = ?, position = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (parent_id, pos, utcnow(), by, prev["id"]),
            )
            now = _row(c.execute("SELECT * FROM tasks WHERE id = ?", (prev["id"],)).fetchone())
            self._log(c, prev["id"], by, "move", _snapshot(prev), _snapshot(now))
        return now

    def delete(self, ids, *, by: str = "human") -> dict:
        """Move tasks and their sub-tasks to the trash, as one batch (one Undo restores it all)."""
        batch = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)
        now = utcnow()
        moved: builtins.list[str] = []
        with self.db.session() as c:
            for tid in [str(i) for i in ids]:
                r = c.execute("SELECT deleted_at FROM tasks WHERE id = ?", (tid,)).fetchone()
                if not r:
                    raise TaskMissing("This task no longer exists.")
                if r["deleted_at"]:
                    continue
                for sid in self._subtree(c, tid):
                    if sid in moved:
                        continue
                    c.execute(
                        "UPDATE tasks SET deleted_at = ?, deleted_batch = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                        (now, batch, now, by, sid),
                    )
                    moved.append(sid)
                self._log(c, tid, by, "delete", None, {"batch": batch})
        return {"batch": batch, "ids": moved}

    def restore(self, *, batch: str | None = None, task_id: str | None = None, by: str = "human") -> builtins.list[str]:
        """Bring back a trash batch, or one task with the sub-tasks deleted along with it.
        A task whose parent is gone for good comes back at the top level."""
        with self.db.session() as c:
            if batch:
                ids = [r["id"] for r in c.execute("SELECT id FROM tasks WHERE deleted_batch = ?", (batch,))]
            elif task_id:
                r = c.execute("SELECT deleted_batch FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
                if not r:
                    raise TaskMissing("This task no longer exists.")
                below: set[str] = set(self._subtree(c, str(task_id), include_deleted=True))
                ids = [
                    x["id"]
                    for x in c.execute("SELECT id, deleted_batch FROM tasks WHERE deleted_at IS NOT NULL")
                    if x["id"] in below and x["deleted_batch"] == r["deleted_batch"]
                ]
            else:
                raise TaskError("Nothing to restore.")
            for tid in ids:
                c.execute(
                    "UPDATE tasks SET deleted_at = NULL, deleted_batch = NULL, updated_by = ? WHERE id = ?", (by, tid)
                )
            restored = set(ids)
            for tid in ids:
                p = c.execute("SELECT parent_id FROM tasks WHERE id = ?", (tid,)).fetchone()["parent_id"]
                if p and p not in restored:
                    pr = c.execute("SELECT deleted_at FROM tasks WHERE id = ?", (p,)).fetchone()
                    if not pr or pr["deleted_at"]:
                        c.execute("UPDATE tasks SET parent_id = NULL WHERE id = ?", (tid,))
                self._log(c, tid, by, "restore", None, None)
        return ids

    def rewrite_texts(self, change, *, by: str = "human") -> int:
        """Apply ``change(text) -> text`` to every task's title and body (trashed ones too), each
        changed task recorded in its history. Returns how many tasks changed."""
        n = 0
        with self.db.session() as c:
            for r in c.execute("SELECT * FROM tasks").fetchall():
                before = _row(r)
                title, body = clean_title(change(before["title"])), change(before["body"])
                if title == before["title"] and body == before["body"]:
                    continue
                c.execute(
                    "UPDATE tasks SET title = ?, body = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                    (title, body, utcnow(), by, before["id"]),
                )
                after = _row(c.execute("SELECT * FROM tasks WHERE id = ?", (before["id"],)).fetchone())
                self._log(c, before["id"], by, "update", _snapshot(before), _snapshot(after))
                n += 1
        return n

    def purge(self, older_than: str) -> int:
        """Delete trashed tasks deleted before ``older_than`` (UTC ISO time) for good."""
        with self.db.session() as c:
            ids = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM tasks WHERE deleted_at IS NOT NULL AND deleted_at < ?", (older_than,)
                )
            ]
            for tid in ids:
                c.execute("DELETE FROM tasks WHERE id = ?", (tid,))
                c.execute("DELETE FROM task_history WHERE task_id = ?", (tid,))
            # sub-tasks still alive under a parent deleted for good move to the top level
            c.execute(
                "UPDATE tasks SET parent_id = NULL WHERE parent_id IS NOT NULL AND parent_id NOT IN (SELECT id FROM tasks)"
            )
        return len(ids)

    def undo(self, history_id: int, *, by: str = "human") -> dict:
        """Put back the values a recorded update or move replaced, if the task still has the values
        that change left (otherwise someone changed it since, and nothing is overwritten)."""
        with self.db.session() as c:
            h = c.execute("SELECT * FROM task_history WHERE id = ?", (int(history_id),)).fetchone()
        if not h:
            raise TaskError("This change is no longer recorded.")
        if h["undone"]:
            raise TaskError("This change was already undone.")
        if h["action"] == "create":
            self.delete([h["task_id"]], by=by)
        elif h["action"] == "delete":
            self.restore(batch=json.loads(h["after"])["batch"], by=by)
        elif h["action"] in ("update", "move"):
            before, after = json.loads(h["before"]), json.loads(h["after"])
            cur = _snapshot(self.get(h["task_id"], trashed_ok=False))
            if any(cur.get(k) != after.get(k) for k in (*EDITABLE, "parent_id")):
                raise TaskError("The task changed again since; nothing was undone.")
            fields = {k: before[k] for k in EDITABLE if before.get(k) != after.get(k)}
            if fields:
                self.update(h["task_id"], fields, by=by)
            if before.get("parent_id") != after.get("parent_id") or before.get("position") != after.get("position"):
                with self.db.session() as c:
                    c.execute(
                        "UPDATE tasks SET parent_id = ?, position = ? WHERE id = ?",
                        (before.get("parent_id"), before.get("position"), h["task_id"]),
                    )
        else:
            raise TaskError("This change cannot be undone.")
        with self.db.session() as c:
            c.execute("UPDATE task_history SET undone = 1 WHERE id = ?", (int(history_id),))
        return self.get(h["task_id"])
