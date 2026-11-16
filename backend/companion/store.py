"""``ai.db``: the conversations of "Ask AI", which survive a restart of the server.

The hub (``hub.py``) is in memory; this keeps what the user wants to come back to: each
conversation with its messages, the computer and provider it is pinned to, and the model and
effort of its last question. Bounded: the newest ``MAX_CONVERSATIONS`` conversations are kept
(older ones are deleted with their messages), each with at most ``MAX_MESSAGES`` messages. The
file is part of backups, like ``fields.db``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_CONVERSATIONS = 200
MAX_MESSAGES = 400
TITLE_MAX = 80
CONTEXT_TEXT = 300  # a selection or quote is kept this long, to show what the question was about

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    effort TEXT,
    companion_id TEXT NOT NULL,
    companion_name TEXT NOT NULL,
    context TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    job TEXT,
    text TEXT NOT NULL,
    tools TEXT,
    usage TEXT,
    state TEXT,
    error TEXT,
    created TEXT NOT NULL,
    context TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_messages ON messages(conversation, id);
CREATE INDEX IF NOT EXISTS idx_ai_conversations ON conversations(updated);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def clean_contexts(context) -> list[dict]:
    """``clean_context`` of each part: a question may come with several notes, papers and passages."""
    items = context if isinstance(context, list) else [context]
    return [c for c in map(clean_context, items) if c]


def clean_context(context) -> dict | None:
    """The part of a question's context worth keeping: where it came from, not the whole text."""
    if not isinstance(context, dict):
        return None
    if context.get("kind") == "note":
        keep = {"kind": "note", "path": str(context.get("path") or "")[:500]}
        if context.get("selection"):
            keep["selection"] = str(context["selection"])[:CONTEXT_TEXT]
        return keep
    if context.get("kind") == "paper":
        keep = {"kind": "paper", "item": str(context.get("item") or "")[:100]}
        for key in ("attachment", "page"):
            if isinstance(context.get(key), (int, str)) and not isinstance(context.get(key), bool):
                keep[key] = context[key]
        if context.get("quote"):
            keep["quote"] = str(context["quote"])[:CONTEXT_TEXT]
        return keep
    return None


class Conversations:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.executescript(SCHEMA)
            # databases made before a question kept its own context
            if "context" not in {r["name"] for r in self._db.execute("PRAGMA table_info(messages)")}:
                self._db.execute("ALTER TABLE messages ADD COLUMN context TEXT")
                self._db.commit()
        return self._db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    # ------------------------------------------------------------------ writes
    def record_ask(
        self,
        reply: dict,
        question: str,
        *,
        context,
        model: str | None,
        effort: str | None,
        job: str,
    ) -> None:
        """The user's question, as the hub accepted it (``reply`` is the hub's answer to ``ask``)."""
        now, conv = now_iso(), reply["conversation"]
        kept = clean_contexts(context)
        ctx = json.dumps(kept) if kept else None  # the question's own, a list
        conv_ctx = json.dumps(kept[0] if len(kept) == 1 else kept) if kept else None  # one stays a dict, as before
        with self._lock:
            db = self._conn()
            row = db.execute("SELECT id FROM conversations WHERE id = ?", (conv,)).fetchone()
            if row is None:
                title = " ".join(question.split())[:TITLE_MAX] or "Question"
                db.execute(
                    "INSERT INTO conversations (id, title, created, updated, provider, model, effort, companion_id,"
                    " companion_name, context) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        conv,
                        title,
                        now,
                        now,
                        reply["provider"],
                        model,
                        effort,
                        reply["companion"]["id"],
                        reply["companion"]["name"],
                        conv_ctx,
                    ),
                )
            else:  # the provider and computer stay; model, effort and context follow the latest question
                db.execute(
                    "UPDATE conversations SET updated = ?, model = ?, effort = ?, context = ?, companion_name = ? "
                    "WHERE id = ?",
                    (now, model, effort, conv_ctx, reply["companion"]["name"], conv),
                )
            db.execute(
                "INSERT INTO messages (conversation, role, job, text, created, context) VALUES (?, 'user', ?, ?, ?, ?)",
                (conv, job, question, now, ctx),
            )
            self._prune(db, conv)
            db.commit()

    def record_answer(self, summary: dict) -> None:
        """How a job ended (the hub's summary): the answer, or what arrived before it stopped."""
        now = now_iso()
        with self._lock:
            db = self._conn()
            if db.execute("SELECT 1 FROM conversations WHERE id = ?", (summary["conversation"],)).fetchone() is None:
                return
            db.execute(
                "INSERT INTO messages (conversation, role, job, text, tools, usage, state, error, created)"
                " VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?, ?)",
                (
                    summary["conversation"],
                    summary["job"],
                    summary["text"],
                    json.dumps(summary["tools"]),
                    json.dumps(summary["usage"]) if summary["usage"] else None,
                    summary["state"],
                    summary["error"],
                    now,
                ),
            )
            db.execute("UPDATE conversations SET updated = ? WHERE id = ?", (now, summary["conversation"]))
            self._prune(db, summary["conversation"])
            db.commit()

    @staticmethod
    def _prune(db: sqlite3.Connection, conv: str) -> None:
        db.execute(
            "DELETE FROM conversations WHERE id NOT IN (SELECT id FROM conversations ORDER BY updated DESC, rowid DESC"
            " LIMIT ?)",
            (MAX_CONVERSATIONS,),
        )
        db.execute(
            "DELETE FROM messages WHERE conversation = ? AND id NOT IN (SELECT id FROM messages WHERE conversation = ?"
            " ORDER BY id DESC LIMIT ?)",
            (conv, conv, MAX_MESSAGES),
        )

    def rename(self, conv: str, title: str) -> bool:
        title = " ".join(title.split())[:TITLE_MAX]
        if not title:
            raise ValueError("Give the conversation a title.")
        with self._lock:
            db = self._conn()
            changed = db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv)).rowcount
            db.commit()
        return bool(changed)

    def delete(self, conv: str) -> bool:
        with self._lock:
            db = self._conn()
            changed = db.execute("DELETE FROM conversations WHERE id = ?", (conv,)).rowcount
            db.commit()
        return bool(changed)

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "title": row["title"],
            "created": row["created"],
            "updated": row["updated"],
            "provider": row["provider"],
            "model": row["model"],
            "effort": row["effort"],
            "companion": {"id": row["companion_id"], "name": row["companion_name"]},
            "context": json.loads(row["context"]) if row["context"] else None,
        }

    def list(self, limit: int = 50, query: str = "") -> list[dict]:
        limit = max(1, min(limit, MAX_CONVERSATIONS))
        like = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._lock:
            rows = (
                self._conn()
                .execute(
                    "SELECT * FROM conversations WHERE (? = '%%' OR title LIKE ? ESCAPE '\\' OR id IN"
                    " (SELECT conversation FROM messages WHERE text LIKE ? ESCAPE '\\'))"
                    " ORDER BY updated DESC, rowid DESC LIMIT ?",
                    (like, like, like, limit),
                )
                .fetchall()
            )
        return [self._row(r) for r in rows]

    def get(self, conv: str) -> dict | None:
        with self._lock:
            db = self._conn()
            row = db.execute("SELECT * FROM conversations WHERE id = ?", (conv,)).fetchone()
            if row is None:
                return None
            messages = db.execute("SELECT * FROM messages WHERE conversation = ? ORDER BY id", (conv,)).fetchall()
        out = self._row(row)
        out["messages"] = [
            {
                "role": m["role"],
                "text": m["text"],
                "tools": json.loads(m["tools"]) if m["tools"] else [],
                **({"usage": json.loads(m["usage"])} if m["usage"] else {}),
                **({"state": m["state"]} if m["state"] else {}),
                **({"error": m["error"]} if m["error"] else {}),
                **({"context": json.loads(m["context"])} if m["context"] else {}),
                "created": m["created"],
            }
            for m in messages
        ]
        return out
