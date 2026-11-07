"""Automatic backups of the library and the notes.

One archive per backup, ``library-YYYYMMDD-HHMMSS.tar.gz``, holding:

* ``library.db`` — copied with SQLite's online backup API, so it is consistent while the app runs;
* ``settings.json``;
* ``notes/`` — every note, the images notes use (``attachments/``) and both notes trashes;
* ``notes-history.bundle`` — the whole history of the notes' text (``git bundle``), when Git is
  installed;
* ``fields.db`` — the values plugins attach to papers (copied like ``library.db``);
* ``plugins.json`` and ``plugin-data/`` — which plugins are on, their settings and what they store
  (their SQLite databases copied consistently, like ``library.db``; their log files left out);
* ``manifest.json`` — when, why, and how many items and notes it holds.

Left out on purpose: the PDFs of the entries (``pdfs/``, large; the user asked to leave them
out), ``fulltext.db`` and ``notes_index.db`` (rebuilt automatically) and ``citations.db``
(reference lists can be fetched again).

Backups run when the app starts if the newest one is older than the interval (Settings →
Backups: off / daily / weekly), then whenever that interval passes, and before Empty Trash,
permanent deletes, imports and "Regenerate all keys" (at most one such backup every ten
minutes). Only the newest ``backup_keep`` archives are kept; the notes backups made before a
citation-key rename (``notes-*.tar.gz``) are separate and never touched here.

Restoring first backs up the current state, then puts back ``library.db`` (through the backup
API, so open connections stay valid), ``settings.json`` and ``notes/``, and rebuilds the
indexes. Nothing in the live data is touched until the archive has been checked. The notes history
is not rolled back: the restored notes become one more change in it, so nothing recorded after the
backup is lost. Only a missing or damaged history is rebuilt from the backup's bundle.
"""

from __future__ import annotations

import builtins
import io
import json
import logging
import re
import sqlite3
import tarfile
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .delivery import Delivery
from .errors import BackupError
from .restore import Restore

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^library-(\d{8}-\d{6})(?:-(\d+))?\.tar\.gz$")


def _order(p: Path) -> tuple:
    """Oldest first: by time, then by the -1, -2 … suffix of backups made in the same second."""
    m = NAME_RE.match(p.name)
    return (m.group(1), int(m.group(2) or 0)) if m else ("", 0)


INTERVALS = {"off": None, "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}
BEFORE_EVERY = timedelta(minutes=10)  # backups before risky operations: at most this often
CHECK_EVERY_S = 15 * 60  # how often the scheduler looks at the clock
FORMAT = 1


class Backups(Restore):
    def __init__(self, services, remote):
        self.s = services
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._last_before: datetime | None = None
        self._last_stamp: str | None = None
        self.last_error: str | None = None
        self.remote = remote
        self.delivery = Delivery(self, remote)
        self._stopped = threading.Event()

    # ------------------------------------------------------------------ where
    @property
    def dir(self) -> Path:
        custom = (self.s.settings.get("backup_dir") or "").strip()
        return Path(custom).expanduser() if custom else self.s.config.backups_dir

    def _path(self, name: str) -> Path:
        if not NAME_RE.match(name or ""):
            raise BackupError("Unknown backup.")
        p = self.dir / name
        if not p.is_file():
            raise BackupError("Unknown backup.")
        return p

    # ------------------------------------------------------------------ list
    def list(self) -> builtins.list[dict]:
        out: builtins.list[dict] = []
        if not self.dir.is_dir():
            return out
        for p in sorted(
            (p for p in self.dir.glob("library-*.tar.gz") if NAME_RE.match(p.name)), key=_order, reverse=True
        ):
            entry = {
                "name": p.name,
                "size": p.stat().st_size,
                "created": None,
                "reason": None,
                "items": None,
                "notes": None,
            }
            try:
                entry.update({k: v for k, v in self._manifest(p).items() if k in entry})
            except Exception:
                entry["damaged"] = True
            if not entry["created"]:
                entry["created"] = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
            out.append(entry)
        return out

    @staticmethod
    def _manifest(path: Path) -> dict:
        with tarfile.open(path, "r:gz") as tar:
            member = tar.next()  # written first, so this does not read the whole archive
            if not member or member.name != "manifest.json":
                raise BackupError("No manifest")
            f = tar.extractfile(member)
            if f is None:
                raise BackupError("No manifest")
            return json.loads(f.read().decode("utf-8"))

    def newest(self) -> dict | None:
        backups = [b for b in self.list() if not b.get("damaged")]
        return backups[0] if backups else None

    def status(self) -> dict:
        newest = self.newest()
        remote = self.remote.configuration()
        delivery = self.delivery.status()
        revision = remote.get("revision")
        backups = self.list()
        for entry in backups:
            entry["uploaded"] = bool(revision and delivery["completed"].get(entry["name"]) == revision)
        return {
            "dir": str(self.dir),
            "backups": backups,
            "last": newest,
            "error": self.last_error,
            "interval": self.s.settings.get("backup_interval"),
            "keep": self.s.settings.get("backup_keep"),
            "destination": self.s.settings.get("backup_destination"),
            "remote": remote,
            "delivery": {k: v for k, v in delivery.items() if k != "completed"},
        }

    # ------------------------------------------------------------------ create
    def create(self, reason: str = "manual") -> dict:
        with self._lock:
            try:
                entry = self._create(reason)
                if self.s.settings.get("backup_destination") == "remote":
                    self.delivery.enqueue(entry, self.dir / entry["name"])
                self.last_error = None
                return entry
            except Exception as e:
                self.last_error = f"Backup failed: {e}"
                log.exception("Backup failed")
                raise BackupError(self.last_error) from e

    def _create(self, reason: str) -> dict:
        dest_dir = self.dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        stamp = now.strftime("%Y%m%d-%H%M%S")
        # Several backups in the same second get -1, -2 …, always above any taken before
        # (even ones already pruned), so the newest also sorts last.
        same = [_order(p)[1] for p in dest_dir.glob(f"library-{stamp}*.tar.gz") if NAME_RE.match(p.name)]
        n = max(same) + 1 if same else (1 if self._last_stamp == stamp else 0)
        name = f"library-{stamp}-{n}.tar.gz" if n else f"library-{stamp}.tar.gz"
        self._last_stamp = stamp
        cfg = self.s.config
        with tempfile.TemporaryDirectory() as tmp:
            db_copy = Path(tmp) / "library.db"
            src = sqlite3.connect(str(cfg.db_path), timeout=30)
            dst = sqlite3.connect(str(db_copy))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            items = self._count_items(db_copy)
            note_files = [p for p in cfg.notes_dir.rglob("*") if p.is_file()] if cfg.notes_dir.is_dir() else []
            notes = sum(
                1
                for p in note_files
                if p.suffix == ".md" and not any(part.startswith(".") for part in p.relative_to(cfg.notes_dir).parts)
            )
            manifest = {
                "app": "synapse",
                "format": FORMAT,
                "created": now.isoformat(timespec="seconds"),
                "reason": reason,
                "items": items,
                "notes": notes,
                "note_files": len(note_files),
            }
            part = dest_dir / (name + ".part")
            try:
                with tarfile.open(part, "w:gz") as tar:
                    data = json.dumps(manifest, indent=2).encode("utf-8")
                    info = tarfile.TarInfo("manifest.json")
                    info.size, info.mtime = len(data), int(now.timestamp())
                    tar.addfile(info, io.BytesIO(data))
                    tar.add(db_copy, arcname="library.db")
                    if cfg.settings_path.is_file():
                        tar.add(cfg.settings_path, arcname="settings.json")
                    for p in note_files:
                        tar.add(p, arcname=f"notes/{p.relative_to(cfg.notes_dir).as_posix()}", recursive=False)
                    bundle = Path(tmp) / "notes-history.bundle"
                    if self.s.note_service.history.bundle(bundle):
                        tar.add(bundle, arcname="notes-history.bundle")
                    if cfg.plugins_state_path.is_file():
                        tar.add(cfg.plugins_state_path, arcname="plugins.json")
                    if cfg.fields_db_path.is_file():
                        fields_copy = Path(tmp) / "fields.db"
                        self._sqlite_copy(cfg.fields_db_path, fields_copy)
                        tar.add(fields_copy, arcname="fields.db")
                    for p, arc in self._plugin_files(Path(tmp)):
                        tar.add(p, arcname=arc, recursive=False)
                part.replace(dest_dir / name)  # a half-written archive never looks like a backup
            finally:
                part.unlink(missing_ok=True)
        size = (dest_dir / name).stat().st_size
        self.prune()
        log.info("Backup %s (%s): %s items, %s notes", name, reason, items, notes)
        return {"name": name, "size": size, **manifest}

    @staticmethod
    def _sqlite_copy(src_path: Path, dst_path: Path) -> None:
        src = sqlite3.connect(str(src_path), timeout=30)
        dst = sqlite3.connect(str(dst_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    def _plugin_files(self, tmp: Path) -> builtins.list[tuple[Path, str]]:
        """What plugins store, as (file to add, name in the archive). A SQLite database is copied
        with the backup API first, so a plugin writing to it meanwhile cannot tear it."""
        root = self.s.config.plugin_data_dir
        out: builtins.list[tuple[Path, str]] = []
        if not root.is_dir():
            return out
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if not p.is_file() or any(part.startswith(".") for part in rel.parts):
                continue  # log files and other dot files
            if p.name.endswith(("-wal", "-shm", "-journal", ".tmp")):
                continue
            arc = f"plugin-data/{rel.as_posix()}"
            if p.suffix in (".sqlite", ".db"):
                copy = tmp / "plugin-db" / rel
                copy.parent.mkdir(parents=True, exist_ok=True)
                try:
                    src = sqlite3.connect(str(p), timeout=30)
                    dst = sqlite3.connect(str(copy))
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                        src.close()
                    out.append((copy, arc))
                    continue
                except sqlite3.Error:
                    log.warning("Backup: %s is not a SQLite database; copied as it is", p)
            out.append((p, arc))
        return out

    @staticmethod
    def _count_items(db: Path) -> int | None:
        try:
            c = sqlite3.connect(str(db))
            try:
                return c.execute("SELECT COUNT(*) FROM papers WHERE deleted_at IS NULL").fetchone()[0]
            finally:
                c.close()
        except sqlite3.Error:
            return None

    def prune(self) -> builtins.list[str]:
        keep = max(1, int(self.s.settings.get("backup_keep") or 1))
        archives = sorted((p for p in self.dir.glob("library-*.tar.gz") if NAME_RE.match(p.name)), key=_order)
        removed = []
        for p in archives[:-keep]:
            if p.name == getattr(self.delivery, "active", None):
                continue
            p.unlink(missing_ok=True)
            removed.append(p.name)
        self.delivery.forget(removed)
        return removed

    def remote_list(self) -> builtins.list[dict]:
        entries = []
        for item in self.remote.entries():
            name = item.get("Name", "")
            if not isinstance(name, str) or not NAME_RE.fullmatch(name) or item.get("IsDir"):
                continue
            entries.append(
                {
                    "name": name,
                    "size": item.get("Size", 0),
                    "created": item.get("ModTime"),
                    "reason": None,
                    "items": None,
                    "notes": None,
                }
            )
        return sorted(entries, key=lambda e: _order(Path(e["name"])), reverse=True)

    def restore_remote(self, name: str) -> dict:
        if not NAME_RE.fullmatch(name or ""):
            raise BackupError("Unknown backup.")
        with tempfile.TemporaryDirectory(dir=self.s.config.data_dir) as tmp:
            path = Path(tmp) / name
            self.remote.download(name, path)
            return self._restore_path(path, name)

    # ------------------------------------------------------------------ automatic
    def due(self, now: datetime | None = None) -> bool:
        interval = INTERVALS.get(self.s.settings.get("backup_interval"))
        if interval is None:
            return False
        newest = self.newest()
        if not newest:
            return True
        try:
            last = datetime.fromisoformat(newest["created"])
        except (TypeError, ValueError):
            return True
        return (now or datetime.now()) - last >= interval

    def run_if_due(self) -> dict | None:
        if not self.due():
            return None
        try:
            return self.create("scheduled")
        except BackupError:
            return None

    def before(self, operation: str) -> dict | None:
        """A backup before an operation that changes much of the library, unless one was made
        in the last ten minutes. Never stops the operation: a failure is logged and shown."""
        if self.s.settings.get("backup_interval") == "off":
            return None
        now = datetime.now()
        newest = self.newest()
        recent = [t for t in (self._last_before,) if t]
        if newest and newest.get("created"):
            try:
                recent.append(datetime.fromisoformat(newest["created"]))
            except ValueError:
                pass
        if any(now - t < BEFORE_EVERY for t in recent):
            return None
        self._last_before = now
        try:
            return self.create(f"before {operation}")
        except BackupError:
            return None

    def start(self) -> None:
        """Back up now if one is due, then check every quarter of an hour (the web app only;
        the MCP server never calls this)."""

        self._stopped.clear()
        self.delivery.start()

        def tick():
            if self._stopped.is_set():
                return
            try:
                self.run_if_due()
            except Exception:
                log.exception("Scheduled backup failed")
            if not self._stopped.is_set():
                self._timer = threading.Timer(CHECK_EVERY_S, tick)
                self._timer.daemon = True
                self._timer.start()

        t = threading.Thread(target=tick, name="backups", daemon=True)
        t.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._timer:
            self._timer.cancel()
        self.delivery.stop()
