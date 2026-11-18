"""The password that unlocks locked notes (Settings → Notes; see backend/notes/locks.py).

Kept in ``auth.db`` as a salted scrypt hash, never in ``settings.json``, which goes into backups and
is sent to every page. Wrong guesses are slowed down: after ``FREE_TRIES`` failures in a row each
device waits longer before it may try again (in memory; a restart forgets it).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from .authdb import AuthTable, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS note_password (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    salt BLOB NOT NULL,
    hash BLOB NOT NULL,
    n INTEGER NOT NULL, r INTEGER NOT NULL, p INTEGER NOT NULL,
    changed TEXT NOT NULL
);
"""

MIN_LENGTH = 4
MAX_LENGTH = 1024
SCRYPT = {"n": 2**14, "r": 8, "p": 1}  # ~16 MB and a few tens of ms per check
FREE_TRIES = 5
MAX_WAIT = 15 * 60  # seconds


class PasswordError(ValueError):
    status = 400

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


class NotePassword(AuthTable):
    SCHEMA = SCHEMA

    def __init__(self, path, clock=time.time):
        super().__init__(path)
        self.clock = clock
        self._fails: dict[str, tuple[int, float]] = {}  # device → (failures in a row, may try again at)
        self._fails_lock = threading.Lock()

    def is_set(self) -> bool:
        with self._lock:
            return self._conn().execute("SELECT 1 FROM note_password WHERE id = 1").fetchone() is not None

    def set(self, password: str) -> None:
        if not isinstance(password, str) or len(password) < MIN_LENGTH:
            raise PasswordError(f"The password needs at least {MIN_LENGTH} characters.")
        if len(password) > MAX_LENGTH:
            raise PasswordError("The password is too long.")
        salt = secrets.token_bytes(16)
        digest = _derive(password, salt, **SCRYPT)
        with self._lock:
            db = self._conn()
            db.execute(
                "INSERT OR REPLACE INTO note_password (id, salt, hash, n, r, p, changed) VALUES (1, ?, ?, ?, ?, ?, ?)",
                (salt, digest, SCRYPT["n"], SCRYPT["r"], SCRYPT["p"], now_iso()),
            )
            db.commit()
        with self._fails_lock:
            self._fails.clear()

    def check(self, password: str, device: str) -> bool:
        """Whether ``password`` is the password. Raises PasswordError (429) while ``device`` must wait."""
        now = self.clock()
        with self._fails_lock:
            n, wait_until = self._fails.get(device, (0, 0.0))
        if wait_until > now:
            raise PasswordError(f"Too many wrong passwords. Try again in {int(wait_until - now) + 1} s.", 429)
        with self._lock:
            row = self._conn().execute("SELECT salt, hash, n, r, p FROM note_password WHERE id = 1").fetchone()
        if row is None:
            raise PasswordError("No password is set. Set one in Settings → Notes.", 409)
        ok = isinstance(password, str) and 0 < len(password) <= MAX_LENGTH
        ok = ok and hmac.compare_digest(_derive(password, row["salt"], row["n"], row["r"], row["p"]), row["hash"])
        with self._fails_lock:
            if ok:
                self._fails.pop(device, None)
            else:
                n += 1
                wait = 0.0 if n < FREE_TRIES else min(MAX_WAIT, 2.0 ** (n - FREE_TRIES + 1))
                self._fails[device] = (n, now + wait)
        return ok
