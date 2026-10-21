"""What a plugin stores: small JSON files and SQLite databases in its own folder of
``data/plugin-data/<id>/``. That folder is part of every backup."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class StorageError(ValueError):
    pass


def _name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise StorageError(f"Storage name {name!r}: lowercase letters, digits, - and _")
    return name


class JsonStore:
    """A small JSON object kept in a file, saved after each change. For settings-like state, not for
    many records (use ``sqlite`` for those)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), "utf-8")
        tmp.replace(self.path)

    def all(self) -> dict:
        with self._lock:
            return self._load()

    def get(self, key: str, default=None):
        with self._lock:
            return self._load().get(key, default)

    def set(self, key: str, value) -> None:
        self.update({key: value})

    def update(self, values: dict) -> None:
        json.dumps(values)  # refuse what cannot be stored before touching the file
        with self._lock:
            data = self._load()
            data.update(values)
            self._save(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._load()
            if key in data:
                del data[key]
                self._save(data)


class SqliteStore:
    """A SQLite database with numbered migrations: ``migrations[i]`` is the SQL that brings the
    schema from version ``i`` to ``i + 1`` (kept in ``PRAGMA user_version``). Migrations only ever
    get appended; existing ones are never changed, so every user's database can be brought up to
    date. ``before_upgrade(name)`` runs before migrating a database that already holds data (the host
    makes a backup there)."""

    def __init__(self, path: Path, migrations: Sequence[str], before_upgrade: Callable[[str], None] | None = None):
        self.path = path
        self.migrations = list(migrations)
        path.parent.mkdir(parents=True, exist_ok=True)
        c = self._connect()
        try:
            c.execute("PRAGMA journal_mode=WAL")
            have = c.execute("PRAGMA user_version").fetchone()[0]
            if have > len(self.migrations):
                raise StorageError(
                    f"{path.name} was written by a newer version of the plugin (schema {have}); "
                    f"this one knows {len(self.migrations)}"
                )
            if have < len(self.migrations) and have > 0 and before_upgrade:
                before_upgrade(path.name)
            for i in range(have, len(self.migrations)):
                try:
                    c.executescript("BEGIN;\n" + self.migrations[i] + f"\n;PRAGMA user_version = {i + 1};\nCOMMIT;")
                except sqlite3.Error as e:
                    if c.in_transaction:
                        c.execute("ROLLBACK")
                    raise StorageError(f"{path.name}: migration {i + 1} failed: {e}") from e
        finally:
            c.close()

    @property
    def version(self) -> int:
        with self._connect() as c:
            return c.execute("PRAGMA user_version").fetchone()[0]

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """A connection inside one transaction: committed at the end, rolled back on an error."""
        c = self._connect()
        try:
            c.execute("BEGIN")
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    def query(self, sql: str, params: Sequence | dict = ()) -> list[dict]:
        c = self._connect()
        try:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
        finally:
            c.close()


class Storage:
    """``plugin.storage``: the plugin's folder and the stores in it."""

    def __init__(self, folder: Path, before_upgrade: Callable[[str], None] | None = None):
        self._folder = folder
        self._before_upgrade = before_upgrade
        self._json: dict[str, JsonStore] = {}

    @property
    def dir(self) -> Path:
        """The plugin's own folder, for files of any kind (created on first use)."""
        self._folder.mkdir(parents=True, exist_ok=True)
        return self._folder

    def json(self, name: str) -> JsonStore:
        name = _name(name)
        if name not in self._json:
            self._json[name] = JsonStore(self._folder / f"{name}.json")
        return self._json[name]

    def sqlite(self, name: str, migrations: Sequence[str] = ()) -> SqliteStore:
        return SqliteStore(self._folder / f"{_name(name)}.sqlite", migrations, self._before_upgrade)
