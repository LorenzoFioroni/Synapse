"""Tasks and the notes that show them.

A note shows a task with a ``{{task:<id>}}`` line. This module finds where tasks are shown (from
the notes index), writes such lines into notes and takes them out again (hash-checked, retried
when the note is saved meanwhile), and turns a Markdown checkbox list into tasks.
"""

from __future__ import annotations

import re

from ..notes import parser
from ..notes.vault import Conflict
from .store import TaskError, TaskStore

TOKEN = "{{task:%s}}"
_TOKEN_RE = re.compile(r"\{\{task:([a-z0-9]{4,32})\}\}")
# A line that only shows a task, perhaps indented, in a list item or a quote.
_REF_LINE = re.compile(
    r"^(?P<prefix>(?:[ \t]*>)*[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?)\{\{task:(?P<id>[a-z0-9]{4,32})\}\}[ \t]*$"
)
_CHECKBOX = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+]|\d+[.)])[ \t]+\[(?P<box>[ xX])\](?:[ \t]+(?P<text>.*))?$")
_ITEM = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+]|\d+[.)])[ \t]+")
SAVE_TRIES = 3


def token(task_id: str) -> str:
    return TOKEN % task_id


def _width(s: str) -> int:
    return len(s.expandtabs(4))


def insert_ref(text: str, task_id: str, after_line: int | None = None) -> tuple[str, int]:
    """``text`` with a line showing the task, and that line's number (1-based).

    After ``after_line`` when given; else after the last line that shows a task (with the same
    indentation and list marker); else at the end of the note, after a blank line."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    trailing = lines and lines[-1] == ""
    if trailing:
        lines.pop()
    tok = token(task_id)
    if after_line is not None:
        at = max(0, min(int(after_line), len(lines)))
        lines.insert(at, tok)
        line = at + 1
    else:
        refs = [l for l in parser.parse(text).links if l.kind == "task"]
        last = refs[-1].line if refs else None
        if last is not None and _REF_LINE.match(lines[last - 1]):
            prefix = _REF_LINE.match(lines[last - 1]).group("prefix")  # type: ignore[union-attr]
            lines.insert(last, prefix + tok)
            line = last + 1
        else:
            while lines and not lines[-1].strip():
                lines.pop()
            if lines and lines[-1].strip() and not _REF_LINE.match(lines[-1]):
                lines.append("")
            lines.append(tok)
            line = len(lines)
    return nl.join(lines) + nl, line


def remove_refs(text: str, task_id: str) -> tuple[str, int]:
    """``text`` without the task: a line that only shows it goes away, an inline mention is cut
    out. Never inside code, math or comments. Returns the new text and how many were removed."""
    nl = "\r\n" if "\r\n" in text else "\n"
    src = text.replace("\r\n", "\n")
    lines = src.split("\n")
    hits = sorted({l.line for l in parser.parse(src).links if l.kind == "task" and l.target == task_id})
    removed = 0
    tok = token(task_id)
    for n in reversed(hits):
        i = n - 1
        m = _REF_LINE.match(lines[i])
        if m and m.group("id") == task_id:
            del lines[i]
            removed += 1
        else:
            count = lines[i].count(tok)
            lines[i] = re.sub(r"[ \t]{2,}", " ", lines[i].replace(tok, "")).rstrip()
            removed += count
    return nl.join(lines), removed


def rewrite_key(text: str, old: str, new: str) -> str:
    """``text`` with its citations of ``old`` pointing at ``new``."""
    if f"@{old}" not in text:
        return text
    # a leading newline: a body that starts with --- is not front matter
    src = "\n" + text
    edits = [(s["start"], s["end"], new) for s in parser.link_spans(src) if s["kind"] == "cite" and s["target"] == old]
    return parser.replace_spans(src, edits)[1:] if edits else text


def ref_ids(text: str) -> list[str]:
    """The tasks a note shows, in order (each once)."""
    out: list[str] = []
    for l in parser.parse(text).links:
        if l.kind == "task" and l.target not in out:
            out.append(l.target)
    return out


class TaskService:
    def __init__(self, store: TaskStore, note_service):
        self.store = store
        self.notes = note_service

    # ------------------------------------------------------------------ reads
    def refs(self) -> dict[str, list[dict]]:
        """task id -> where notes show it: path, note title, line, heading above it."""
        out: dict[str, list[dict]] = {}
        for r in self.notes.index.task_refs():
            out.setdefault(r["task"], []).append(
                {"path": r["path"], "title": r["note_title"], "line": r["line"], "heading": r["heading"]}
            )
        return out

    def overview(self) -> dict:
        return {
            "tasks": self.store.list(),
            "refs": self.refs(),
            "trash": len(self.store.list(trashed=True)),
        }

    def task(self, task_id: str) -> dict:
        t = self.store.get(task_id)
        return {**t, "refs": self.refs().get(t["id"], []), "subtasks": self.store.children(t["id"])}

    # ------------------------------------------------------------------ notes
    def _rewrite(self, path: str, change, *, by: str, summary: str):
        """Apply ``change(text) -> (new_text, result)`` to a note, hash-checked, retried when the
        note is saved meanwhile. Claude's changes go to the note's history (undoable in the app)."""
        last: Conflict | None = None
        for _ in range(SAVE_TRIES):
            got = self.notes.get(path)
            new, result = change(got["text"])
            if new == got["text"]:
                return got["path"], result
            try:
                if by == "ai":
                    self.notes.edit(got["path"], new, got["hash"], by="ai", summary=summary)
                else:
                    self.notes.save(got["path"], new, got["hash"])
                return got["path"], result
            except Conflict as e:
                last = e
        raise TaskError("The note kept changing while the task was being added; try again.") from last

    def add_to_note(self, task_id: str, path: str, *, after_line: int | None = None, by: str = "human") -> dict:
        t = self.store.get(task_id, trashed_ok=False)
        path, line = self._rewrite(
            path, lambda text: insert_ref(text, t["id"], after_line), by=by, summary=f"Added the task “{t['title']}”"
        )
        return {"path": path, "line": line}

    def remove_from_note(self, task_id: str, path: str, *, by: str = "human") -> dict:
        path, n = self._rewrite(path, lambda text: remove_refs(text, task_id), by=by, summary="Removed a task")
        return {"path": path, "removed": n}

    def create(self, data: dict, *, by: str = "human") -> dict:
        """Create a task, and show it in a note when ``data["note"]`` names one."""
        t = self.store.create(
            data.get("title") or "",
            body=data.get("body") or "",
            parent_id=data.get("parent_id"),
            due=data.get("due"),
            status=data.get("status") or "open",
            before=data.get("before"),
            after=data.get("after"),
            by=by,
        )
        out: dict = {"task": t}
        note = data.get("note")
        if note:
            try:
                out["ref"] = self.add_to_note(t["id"], note, after_line=data.get("after_line"), by=by)
            except Exception:
                self.store.delete([t["id"]], by=by)  # no stray task when the note could not take it
                raise
        return out

    # ------------------------------------------------------------------ citation keys
    def rename_key(self, old: str, new: str) -> int:
        """A citation key changed: ``[@old]`` becomes ``[@new]`` in every task's title and body
        (trashed tasks too, so a restored task still cites the right paper). Same rule as the notes
        (``NotesService.rename_key``): only real citations, never inside code or math."""
        return self.store.rewrite_texts(lambda text: rewrite_key(text, old, new), by="human")

    # ------------------------------------------------------------------ checkbox lists
    def convert(self, text: str, *, by: str = "human") -> dict:
        """Turn the checkbox items in ``text`` into tasks: nested items become sub-tasks, the
        lines indented under an item its body. Returns the text with each top-level item replaced
        by its ``{{task:…}}`` line, and the tasks made. Other lines stay as they are."""
        src = text.replace("\r\n", "\n")
        lines = src.split("\n")
        masked = parser.mask(src).split("\n")
        out: list[str] = []
        stack: list[dict] = []  # open items: indent, content column, task id, body lines
        made: list[dict] = []

        def close_to(indent: int) -> None:
            while stack and stack[-1]["indent"] >= indent:
                item = stack.pop()
                body = item["body"]
                while body and not body[-1].strip():
                    body.pop()
                if body:
                    self.store.update(item["id"], {"body": "\n".join(body)}, by=by)

        blanks = 0  # blank lines seen inside an item: its body's if more of it follows, else the note's
        for raw, m in zip(lines, masked):
            if stack and not raw.strip():
                blanks += 1
                continue
            cb = _CHECKBOX.match(m)
            if cb and cb.group("text") is not None and raw[cb.start("text") : cb.end("text")].strip():
                indent = _width(cb.group("indent"))
                close_to(indent)
                parent = stack[-1]["id"] if stack else None
                if parent is None:
                    out.extend([""] * blanks)
                blanks = 0
                title = raw[cb.start("text") : cb.end("text")]
                t = self.store.create(
                    title, parent_id=parent, status="done" if cb.group("box") in "xX" else "open", by=by
                )
                made.append(t)
                if parent is None:
                    out.append(token(t["id"]))
                col = _width(raw[: cb.start("box") - 1])
                stack.append({"indent": indent, "col": col, "id": t["id"], "body": []})
                continue
            if stack and _width(raw) - _width(raw.lstrip()) > stack[-1]["indent"]:
                item = stack[-1]
                item["body"].extend([""] * blanks)
                blanks = 0
                cut = 0
                while cut < len(raw) and cut < item["col"] and raw[cut] in " \t":
                    cut += 1
                item["body"].append(raw[cut:])
                continue
            close_to(-1)
            out.extend([""] * blanks)
            blanks = 0
            out.append(raw)
        close_to(-1)
        out.extend([""] * blanks)
        result = "\n".join(out)
        return {"text": result, "tasks": [self.store.get(t["id"]) for t in made]}
