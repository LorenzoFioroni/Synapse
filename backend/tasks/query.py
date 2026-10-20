"""Searching tasks with the same syntax as the Tasks page (frontend/src/views/tasks/taskQuery.ts):
words, "phrases", -exclusions, is:open|done, due:today|overdue|week|none|<DATE|>DATE|DATE,
note:"title", has:body|subtasks."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from datetime import date, timedelta

Test = Callable[[dict], bool]


def _tokens(q: str) -> list[str]:
    try:
        return shlex.split(q)
    except ValueError:  # an unclosed quote
        return q.split()


def _due_test(v: str, today: date) -> Test:
    t = today.isoformat()
    week = (today + timedelta(days=7)).isoformat()
    if v == "none":
        return lambda x: not x.get("due")
    if v == "today":
        return lambda x: x.get("due") == t
    if v == "overdue":
        return lambda x: bool(x.get("due")) and x["due"] < t and x.get("status") != "done"
    if v == "week":
        return lambda x: bool(x.get("due")) and x["due"] <= week
    if v[:1] in "<>":
        d = v[1:]
        return (
            (lambda x: bool(x.get("due")) and x["due"] < d)
            if v[0] == "<"
            else (lambda x: bool(x.get("due")) and x["due"] > d)
        )
    return lambda x: x.get("due") == v


def _negate(test: Test) -> Test:
    return lambda x: not test(x)


def _test(tok: str, refs: dict[str, list[dict]], today: date) -> Test:
    """The test for one search word or filter (lower case)."""
    if tok.startswith("is:"):
        status = tok[3:]
        return lambda x: x.get("status") == status
    if tok.startswith("due:"):
        return _due_test(tok[4:], today)
    if tok.startswith("has:"):
        if tok[4:] == "body":
            return lambda x: bool(x.get("body"))
        return lambda x: bool(x.get("has_subtasks"))
    if tok.startswith("note:"):
        name = tok[5:]

        def in_note(x: dict) -> bool:
            return any(
                name in (r.get("title") or "").lower() or name in r["path"].lower() for r in refs.get(x["id"], [])
            )

        return in_note
    return lambda x: tok in f"{x.get('title', '')}\n{x.get('body', '')}".lower()


def matcher(query: str, *, refs: dict[str, list[dict]] | None = None, today: date | None = None) -> Test:
    """A test for one task (a dict as the store returns it, with ``has_subtasks`` when known)."""
    today = today or date.today()
    refs = refs or {}
    tests: list[Test] = []
    for raw in _tokens(query or ""):
        neg = raw.startswith("-") and len(raw) > 1
        test = _test((raw[1:] if neg else raw).lower(), refs, today)
        tests.append(_negate(test) if neg else test)
    return lambda x: all(t(x) for t in tests)
