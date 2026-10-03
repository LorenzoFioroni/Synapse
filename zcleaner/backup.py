"""Backups taken before anything is written to Zotero.

1. A *logical* backup: the complete library as JSON, exported through the API
   (consistent, and restorable item-by-item through the API).
2. A *physical* copy of zotero.sqlite (+ journal/WAL files), verified with
   SQLite's integrity check.  Zotero keeps the database locked while running,
   so the file is copied byte-by-byte and re-copied if the check fails.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from .config import BACKUP_DIR


class BackupError(RuntimeError):
    pass


def find_data_dir(settings, store) -> Path | None:
    for cand in (settings.get("data_dir"), store.meta_get("data_dir"), str(Path.home() / "Zotero")):
        if cand and (Path(cand).expanduser() / "zotero.sqlite").exists():
            return Path(cand).expanduser()
    return None


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_database(data_dir: Path, dest: Path, tries: int = 4) -> dict:
    src = data_dir / "zotero.sqlite"
    last_err = None
    for attempt in range(tries):
        for suffix in ("", "-wal", "-shm", "-journal"):
            s = Path(str(src) + suffix)
            if s.exists():
                shutil.copy2(s, dest / (s.name))
        copy = dest / "zotero.sqlite"
        try:
            c = sqlite3.connect(str(copy))  # our own copy
            res = c.execute("PRAGMA quick_check").fetchone()[0]
            n = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            c.close()
            if res == "ok":
                return {"file": str(copy), "items_in_db": n, "sha256": _sha256(copy), "bytes": copy.stat().st_size,
                        "attempts": attempt + 1}
            last_err = res
        except sqlite3.Error as e:
            last_err = str(e)
        time.sleep(1.5)
    raise BackupError(f"Could not obtain a consistent copy of zotero.sqlite ({last_err}). "
                      "Zotero may be busy (syncing?) — try again in a moment.")


def make_backup(settings, store, zot, touched: list[str], label: str = "commit", progress=None) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{ts}-{label}"
    n = 2
    while dest.exists():
        dest = BACKUP_DIR / f"{ts}-{label}-{n}"
        n += 1
    dest.mkdir(parents=True)
    manifest = {"created": ts, "label": label, "server_id": zot.server_id, "library": settings.get("library"),
                "touched_items": len(touched)}
    # 1. logical backup of the whole library
    if progress:
        progress("Exporting the library as JSON …")
    items, lib_version = zot.fetch_items()
    with gzip.open(dest / "library.json.gz", "wt", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    by_key = {i["key"]: i for i in items}
    (dest / "items_before.json").write_text(json.dumps([by_key[k] for k in touched if k in by_key], ensure_ascii=False, indent=1))
    manifest.update(items_exported=len(items), library_version=lib_version)
    # 2. physical copy of the database
    if settings.get("commit", "db_backup"):
        data_dir = find_data_dir(settings, store)
        if not data_dir:
            raise BackupError("Zotero data directory not found — set it in Settings (Zotero → Settings → Advanced → "
                              "Files and Folders → Data Directory Location), or disable the database copy.")
        if progress:
            progress("Copying zotero.sqlite …")
        manifest["database"] = copy_database(data_dir, dest)
        manifest["data_dir"] = str(data_dir)
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (dest / "README.txt").write_text(
        "Backup made by Zotero Library Cleaner before applying changes.\n\n"
        "library.json.gz  complete library export (Zotero API JSON)\n"
        "items_before.json  the items about to be changed, as they were\n"
        "zotero.sqlite    copy of the Zotero database (if enabled)\n\n"
        "To restore the database copy: quit Zotero, keep a copy of your current zotero.sqlite, replace it with this\n"
        "one and restart Zotero. If you use Zotero sync, prefer the 'Roll back' button in the app (it reverts\n"
        "through the API and syncs cleanly), because restoring an old database with sync enabled causes conflicts.\n")
    return dest
