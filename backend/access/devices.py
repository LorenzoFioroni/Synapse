"""Other devices (an iPad, a phone) allowed to use the library over the network (issue #34).

There is one user, so there are no accounts: a device is added by **pairing**. On this computer,
Settings → Devices makes a one-time code (valid for a few minutes) and shows it as a QR code;
opening that link on the other device exchanges the code for a long-lived **device token**,
which the browser keeps in a cookie. Requests from this computer itself need no token.

Only SHA-256 hashes of codes and tokens are stored (both are long random strings, so a plain
hash is enough). The tokens live in ``auth.db`` in the data folder — not in settings.json and
not in backups, so restoring a backup never brings back a device that was signed out.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

COOKIE = "synapse_device"
COOKIE_MAX_AGE = 400 * 24 * 3600     # the longest browsers keep a cookie
PAIRING_TTL = 5 * 60                 # seconds a pairing code stays valid
TOUCH_EVERY = 60                     # seconds between "last seen" updates of a device
FAILED_LIMIT = 10                    # failed pairing attempts per address …
FAILED_WINDOW = 10 * 60              # … within this many seconds
NAME_MAX = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    user_agent TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    last_seen TEXT,
    last_addr TEXT
);
"""


class PairingError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def guess_name(user_agent: str) -> str:
    ua = user_agent or ""
    for key, name in (("iPad", "iPad"), ("iPhone", "iPhone"), ("Android", "Android device")):
        if key in ua:
            return name
    if "Macintosh" in ua:
        # iPadOS Safari asks for desktop sites and says "Macintosh"; the pairing page tells them
        # apart (touch points) and sends a name, so this is only the fallback.
        return "Mac"
    if "Windows" in ua:
        return "Windows PC"
    return "Device"


class Devices:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()
        self._codes: dict[str, float] = {}           # code hash → expiry (in memory: a restart voids them)
        self._failed: dict[str, list[float]] = {}    # address → times of failed pairing attempts
        self._touched: dict[str, float] = {}         # device id → last "last seen" write
        self._db: sqlite3.Connection | None = None

    # -- storage -------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(SCHEMA)
        return self._db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    # -- pairing -------------------------------------------------------------
    def new_pairing_code(self) -> tuple[str, float]:
        """A one-time code and the time it expires (epoch seconds)."""
        code = secrets.token_urlsafe(18)
        expires = self.clock() + PAIRING_TTL
        with self._lock:
            now = self.clock()
            self._codes = {h: t for h, t in self._codes.items() if t > now}
            self._codes[_hash(code)] = expires
        return code, expires

    def _too_many_failures(self, addr: str) -> bool:
        now = self.clock()
        recent = [t for t in self._failed.get(addr, []) if now - t < FAILED_WINDOW]
        self._failed[addr] = recent
        return len(recent) >= FAILED_LIMIT

    def redeem(self, code: str, name: str, user_agent: str, addr: str) -> tuple[dict, str]:
        """Exchange a pairing code for a new device. Returns (device, token)."""
        with self._lock:
            if self._too_many_failures(addr):
                raise PairingError("Too many attempts. Wait a few minutes and make a new code on the computer.", 429)
            h = _hash(code or "")
            expires = self._codes.pop(h, None)
            if expires is None or expires < self.clock():
                self._failed.setdefault(addr, []).append(self.clock())
                raise PairingError("This pairing code is not valid any more. Make a new one in Settings → Devices "
                                   "on the computer running Synapse.", 401)
            token = secrets.token_urlsafe(32)
            dev_id = secrets.token_hex(8)
            name = (name or "").strip()[:NAME_MAX] or guess_name(user_agent)
            now = _now_iso()
            db = self._conn()
            db.execute("INSERT INTO devices (id, name, token_hash, user_agent, created, last_seen, last_addr) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (dev_id, name, _hash(token), (user_agent or "")[:300], now, now, addr))
            db.commit()
            self._touched[dev_id] = self.clock()
            return self._get(dev_id), token

    # -- checking ------------------------------------------------------------
    def check(self, token: str | None, addr: str | None = None) -> dict | None:
        """The device this token belongs to (and note that it was seen), or None."""
        if not token:
            return None
        with self._lock:
            row = self._conn().execute("SELECT * FROM devices WHERE token_hash = ?", (_hash(token),)).fetchone()
            if row is None:
                return None
            now = self.clock()
            dev = dict(row)
            if now - self._touched.get(row["id"], 0) >= TOUCH_EVERY:
                dev["_touched"] = True
                self._touched[row["id"]] = now
                self._conn().execute("UPDATE devices SET last_seen = ?, last_addr = ? WHERE id = ?",
                                     (_now_iso(), addr or row["last_addr"], row["id"]))
                self._conn().commit()
            return dev

    # -- managing ------------------------------------------------------------
    def _get(self, dev_id: str) -> dict | None:
        row = self._conn().execute("SELECT * FROM devices WHERE id = ?", (dev_id,)).fetchone()
        return public(dict(row)) if row else None

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._conn().execute("SELECT * FROM devices ORDER BY created").fetchall()
            return [public(dict(r)) for r in rows]

    def rename(self, dev_id: str, name: str) -> dict | None:
        name = (name or "").strip()[:NAME_MAX]
        if not name:
            raise PairingError("Give the device a name.")
        with self._lock:
            self._conn().execute("UPDATE devices SET name = ? WHERE id = ?", (name, dev_id))
            self._conn().commit()
            return self._get(dev_id)

    def revoke(self, dev_id: str) -> bool:
        with self._lock:
            cur = self._conn().execute("DELETE FROM devices WHERE id = ?", (dev_id,))
            self._conn().commit()
            self._touched.pop(dev_id, None)
            return cur.rowcount > 0


def public(row: dict) -> dict:
    """A device as the API shows it (never the token hash)."""
    return {k: row.get(k) for k in ("id", "name", "user_agent", "created", "last_seen", "last_addr")}
