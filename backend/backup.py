"""Automatic backups of the library and the notes.

One archive per backup, ``library-YYYYMMDD-HHMMSS.tar.gz``, holding:

* ``library.db`` — copied with SQLite's online backup API, so it is consistent while the app runs;
* ``settings.json``;
* ``notes/`` — every note, the images notes use (``attachments/``) and both notes trashes;
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
indexes. Nothing in the live data is touched until the archive has been checked.
"""

from __future__ import annotations

import builtins
import io
import json
import logging
import re
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

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


class BackupError(Exception):
    pass


class Backups:
    def __init__(self, services):
        self.s = services
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._last_before: datetime | None = None
        self._last_stamp: str | None = None
        self.last_error: str | None = None

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
        return {
            "dir": str(self.dir),
            "backups": self.list(),
            "last": newest,
            "error": self.last_error,
            "interval": self.s.settings.get("backup_interval"),
            "keep": self.s.settings.get("backup_keep"),
        }

    # ------------------------------------------------------------------ create
    def create(self, reason: str = "manual") -> dict:
        with self._lock:
            try:
                entry = self._create(reason)
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
            p.unlink(missing_ok=True)
            removed.append(p.name)
        return removed

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

        def tick():
            try:
                self.run_if_due()
            except Exception:
                log.exception("Scheduled backup failed")
            self._timer = threading.Timer(CHECK_EVERY_S, tick)
            self._timer.daemon = True
            self._timer.start()

        t = threading.Thread(target=tick, name="backups", daemon=True)
        t.start()

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()

    # ------------------------------------------------------------------ restore
    def restore(self, name: str) -> dict:
        path = self._path(name)
        with tempfile.TemporaryDirectory(dir=str(self.s.config.data_dir)) as tmp:
            root = Path(tmp)
            self._extract(path, root)
            db = root / "library.db"
            self._check_db(db)
            # The current state is kept first: a restore can always be undone.
            safety = self.create(f"before restoring {name}")
            plugins = getattr(self.s, "plugins", None)
            if plugins:
                plugins.stop()  # nothing of theirs may be open while their data is replaced
            try:
                with self._lock:
                    self._restore_db(db)
                    if (root / "settings.json").is_file():
                        # The backup settings themselves stay as they are now: restoring must not
                        # move the backup folder away from the backups just listed.
                        keep = {k: self.s.settings.get(k) for k in ("backup_interval", "backup_keep", "backup_dir")}
                        shutil.copyfile(root / "settings.json", self.s.config.settings_path)
                        self.s.settings.reload()
                        self.s.settings.update(keep)
                    self._restore_notes(root / "notes")
                    self._restore_plugins(root)
            finally:
                if plugins and plugins.role:
                    try:
                        plugins.start(plugins.role, announce=False)
                    except Exception:
                        log.exception("After restore: starting the plugins failed")
        self._after_restore()
        return {"restored": name, "safety_backup": safety["name"]}

    @staticmethod
    def _extract(path: Path, dest: Path) -> None:
        try:
            with tarfile.open(path, "r:gz") as tar:
                members = tar.getmembers()
                for m in members:
                    parts = Path(m.name).parts
                    if (
                        m.name.startswith(("/", "\\"))
                        or ".." in parts
                        or not (m.isfile() or m.isdir())
                        or not parts
                        or parts[0]
                        not in (
                            "manifest.json",
                            "library.db",
                            "settings.json",
                            "notes",
                            "plugins.json",
                            "plugin-data",
                            "fields.db",
                        )
                    ):
                        raise BackupError(f"Unexpected entry in the backup: {m.name}")
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(dest, members=members, filter="data")
                else:  # Python without extraction filters: entries were checked above
                    tar.extractall(dest, members=members)
        except (tarfile.TarError, OSError, EOFError) as e:
            raise BackupError(f"The backup cannot be read: {e}") from e

    @staticmethod
    def _check_db(db: Path) -> None:
        if not db.is_file():
            raise BackupError("The backup has no library database.")
        try:
            c = sqlite3.connect(str(db))
            try:
                ok = c.execute("PRAGMA integrity_check").fetchone()[0]
                c.execute("SELECT COUNT(*) FROM papers").fetchone()
            finally:
                c.close()
        except sqlite3.Error as e:
            raise BackupError(f"The library database in the backup is damaged: {e}") from e
        if ok != "ok":
            raise BackupError(f"The library database in the backup is damaged: {ok}")

    def _restore_db(self, db: Path) -> None:
        src = sqlite3.connect(str(db))
        dst = sqlite3.connect(str(self.s.config.db_path), timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        self.s.db.migrate()  # an older backup gets the columns this version needs

    def _restore_notes(self, notes: Path) -> None:
        live = self.s.config.notes_dir
        aside = live.parent / f".notes-before-restore-{int(time.time())}"
        if live.exists():
            live.rename(aside)
        try:
            if notes.is_dir():
                shutil.move(str(notes), str(live))
            else:
                live.mkdir(parents=True)
        except Exception:
            if live.exists():
                shutil.rmtree(live, ignore_errors=True)
            if aside.exists():
                aside.rename(live)
            raise
        shutil.rmtree(aside, ignore_errors=True)
        self.s.vault.ensure()

    def _restore_plugins(self, root: Path) -> None:
        """Which plugins are on and what they store, as in the backup. A backup made before
        plugins existed leaves today's plugin data alone."""
        cfg = self.s.config
        if (root / "plugins.json").is_file():
            shutil.copyfile(root / "plugins.json", cfg.plugins_state_path)
        if (root / "fields.db").is_file():
            self._sqlite_copy(
                root / "fields.db", cfg.fields_db_path
            )  # through the backup API: open connections stay valid
        data = root / "plugin-data"
        if not data.is_dir():
            return
        live = cfg.plugin_data_dir
        logs = {p.relative_to(live): p.read_bytes() for p in live.rglob(".plugin.log*")} if live.is_dir() else {}
        aside = live.parent / f".plugin-data-before-restore-{int(time.time())}"
        if live.exists():
            live.rename(aside)
        try:
            shutil.move(str(data), str(live))
        except Exception:
            if live.exists():
                shutil.rmtree(live, ignore_errors=True)
            if aside.exists():
                aside.rename(live)
            raise
        shutil.rmtree(aside, ignore_errors=True)
        for rel, content in logs.items():  # the logs are not in backups; keep today's
            (live / rel).parent.mkdir(parents=True, exist_ok=True)
            (live / rel).write_bytes(content)

    def _after_restore(self) -> None:
        s = self.s
        for step in (s.notes.rebuild, s.attachments.poke):
            try:
                step()
            except Exception:
                log.exception("After restore: %s failed", getattr(step, "__name__", step))
        if s.settings.get("fulltext_auto_index"):
            s.indexer.sync()
