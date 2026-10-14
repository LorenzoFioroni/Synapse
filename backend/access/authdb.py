"""``auth.db``: what lets someone in (paired devices, passkeys, access tokens for AI apps).

Each store owns one table in the same SQLite file and creates it when first used, so a data folder
from an older version simply gains the new tables. ``auth.db`` is kept out of ``settings.json`` and
out of backups: restoring a backup must never bring back a device, passkey or token that was
removed.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def hash_secret(secret: str) -> str:
    """SHA-256 of a long random secret (a code or token): enough, since it cannot be guessed."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuthTable:
    """One table of ``auth.db`` with its own connection, opened on first use."""

    SCHEMA = ""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(self.SCHEMA)
        return self._db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
