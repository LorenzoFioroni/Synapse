"""Transactions for plugin files shared by the app and the standalone MCP process.

Lock a separate, stable file: replacing the data file must not replace the lock's inode.
Hidden lock files are coordination only and are left out of backups.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class _Lock:
    def __init__(self):
        self.mutex = threading.RLock()
        self.held = False


_locks: weakref.WeakValueDictionary[Path, _Lock] = weakref.WeakValueDictionary()
_registry_lock = threading.Lock()


@contextmanager
def transaction(path: Path) -> Iterator[None]:
    """Serialize one file's read/modify/write, across instances, threads and processes."""
    path = path.resolve()
    with _registry_lock:
        lock = _locks.get(path)
        if lock is None:
            lock = _Lock()
            _locks[path] = lock
    with lock.mutex:
        if lock.held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with (path.parent / f".{path.name}.lock").open("a+b") as f:
            if sys.platform == "win32":
                import msvcrt

                if f.tell() == 0:
                    f.write(b"\0")
                    f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                lock.held = True
                yield
            finally:
                lock.held = False
                if sys.platform == "win32":
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def save_json(path: Path, data: dict, *, indent: int = 1) -> None:
    """Publish a complete JSON document atomically; caller holds ``transaction``."""
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
