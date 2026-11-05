"""Scoped access tokens for AI apps (``/mcp``) and browser saves (``/api/connector/``).

Claude Code on another computer cannot pair like a browser, so it sends a token with every
request instead: ``Authorization: Bearer syn_…``. Tokens are made on the computer running Synapse
(Settings → AI Access), shown once, and stored as SHA-256 hashes in ``auth.db`` (table
``access_tokens``). Removing one locks its app out at the next request.
"""

from __future__ import annotations

import builtins
import secrets
import sqlite3
import time
from pathlib import Path

from .authdb import AuthTable, hash_secret, now_iso

PREFIX = "syn_"  # tells a Synapse token apart in a config file
NAME_MAX = 60
TOUCH_EVERY = 60  # seconds between "last used" updates
SCOPES = {"mcp", "connector"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS access_tokens (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created TEXT NOT NULL,
    last_used TEXT,
    last_addr TEXT,
    scope TEXT NOT NULL DEFAULT 'mcp'
);
"""


class TokenError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def bearer(header: str | None) -> str | None:
    """The token in an ``Authorization: Bearer …`` header."""
    scheme, _, token = (header or "").strip().partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


def public(row: dict) -> dict:
    """A token as the API shows it (never its hash)."""
    return {k: row.get(k) for k in ("id", "name", "created", "last_used", "last_addr", "scope")}


class AccessTokens(AuthTable):
    SCHEMA = SCHEMA

    def __init__(self, path: Path, clock=time.time):
        super().__init__(path)
        self.clock = clock
        self._touched: dict[str, float] = {}

    def _conn(self) -> sqlite3.Connection:
        opening = self._db is None
        db = super()._conn()
        if opening:
            try:
                # Serialize first-open migrations across the app and other processes.
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    columns = {row["name"] for row in db.execute("PRAGMA table_info(access_tokens)")}
                    if "scope" not in columns:
                        db.execute("ALTER TABLE access_tokens ADD COLUMN scope TEXT NOT NULL DEFAULT 'mcp'")
            except Exception:
                db.close()
                self._db = None
                raise
        return db

    def create(self, name: str, *, scope: str = "mcp") -> tuple[dict, str]:
        """A new token. Returns (its entry, the token itself, which is not kept)."""
        name = (name or "").strip()[:NAME_MAX]
        if not name:
            raise TokenError("Give the token a name, such as the computer or app that will use it.")
        if scope not in SCOPES:
            raise TokenError("Choose an AI app or browser extension token.")
        token = PREFIX + ("connector_" if scope == "connector" else "") + secrets.token_urlsafe(32)
        tok_id = secrets.token_hex(8)
        with self._lock:
            db = self._conn()
            db.execute(
                "INSERT INTO access_tokens (id, name, token_hash, created, scope) VALUES (?, ?, ?, ?, ?)",
                (tok_id, name, hash_secret(token), now_iso(), scope),
            )
            db.commit()
            row = db.execute("SELECT * FROM access_tokens WHERE id = ?", (tok_id,)).fetchone()
        return public(dict(row)), token

    def check(self, token: str | None, addr: str | None = None, *, scope: str = "mcp") -> dict | None:
        """The entry of this token (noting that it was used), or None."""
        if not token:
            return None
        with self._lock:
            db = self._conn()
            row = db.execute("SELECT * FROM access_tokens WHERE token_hash = ?", (hash_secret(token),)).fetchone()
            now = self.clock()
            if row is None or row["scope"] != scope:
                return None
            if now - self._touched.get(row["id"], 0) >= TOUCH_EVERY:
                self._touched[row["id"]] = now
                db.execute(
                    "UPDATE access_tokens SET last_used = ?, last_addr = ? WHERE id = ?",
                    (now_iso(), addr or row["last_addr"], row["id"]),
                )
                db.commit()
            return public(dict(row))

    def list(self) -> builtins.list[dict]:
        with self._lock:
            rows = self._conn().execute("SELECT * FROM access_tokens ORDER BY created").fetchall()
        return [public(dict(r)) for r in rows]

    def revoke(self, tok_id: str) -> bool:
        with self._lock:
            cur = self._conn().execute("DELETE FROM access_tokens WHERE id = ?", (tok_id,))
            self._conn().commit()
            self._touched.pop(tok_id, None)
            return cur.rowcount > 0
