"""Validation and restoration of Synapse archives, shared by local and remote recovery."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import BackupError

log = logging.getLogger(__name__)


class Restore:
    # ------------------------------------------------------------------ restore
    def restore(self: Any, name: str) -> dict:
        return self._restore_path(self._path(name), name)

    def _restore_path(self: Any, path: Path, name: str) -> dict:
        with tempfile.TemporaryDirectory(dir=str(self.s.config.data_dir)) as tmp:
            root = Path(tmp)
            self._extract(path, root)
            db = root / "library.db"
            self._check_db(db)
            history = self.s.note_service.history
            lost = history.lost()  # checked before the safety backup creates a new one
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
                        keep = {
                            k: self.s.settings.get(k)
                            for k in ("backup_interval", "backup_keep", "backup_dir", "backup_destination")
                        }
                        shutil.copyfile(root / "settings.json", self.s.config.settings_path)
                        self.s.settings.reload()
                        self.s.settings.update(keep)
                    self._restore_notes(root / "notes")
                    bundle = root / "notes-history.bundle"
                    history.after_restore(bundle if bundle.is_file() else None, name, rebuild=lost)
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
                            "notes-history.bundle",
                        )
                    ):
                        raise BackupError(f"Unexpected entry in the backup: {m.name}")
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(dest, members=members, filter="data")
                else:  # Python without extraction filters: entries were checked above
                    tar.extractall(dest, members=members)
        except OSError as e:  # its text names paths on the server
            raise BackupError(f"The backup cannot be read: {e.strerror or type(e).__name__}.") from e
        except (tarfile.TarError, EOFError) as e:
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

    def _restore_db(self: Any, db: Path) -> None:
        src = sqlite3.connect(str(db))
        dst = sqlite3.connect(str(self.s.config.db_path), timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        self.s.db.migrate()  # an older backup gets the columns this version needs

    def _restore_notes(self: Any, notes: Path) -> None:
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

    def _restore_plugins(self: Any, root: Path) -> None:
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

    def _after_restore(self: Any) -> None:
        s = self.s
        for step in (s.notes.rebuild, s.attachments.poke):
            try:
                step()
            except Exception:
                log.exception("After restore: %s failed", getattr(step, "__name__", step))
        if s.settings.get("fulltext_auto_index"):
            s.indexer.sync()
