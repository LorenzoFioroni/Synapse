"""Durable upload receipts and retry work, independent of the local backup schedule."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from .errors import BackupError
from .remote import write_private

log = logging.getLogger(__name__)
RETRY_SECONDS = 60


class Delivery:
    def __init__(self, backups, remote):
        self.backups = backups
        self.remote = remote
        self._lock = threading.Lock()
        self._work = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False
        self.active: str | None = None

    def _read(self) -> dict:
        try:
            return json.loads((self.remote.root / "delivery.json").read_text("utf-8"))
        except FileNotFoundError:
            return {}
        except (ValueError, OSError) as e:
            raise BackupError("Remote upload status cannot be read. Check the server's backup-remote folder.") from e

    def _save(self, state: dict) -> None:
        write_private(self.remote.root / "delivery.json", json.dumps(state))

    def status(self) -> dict:
        with self._lock:
            state = self._read()
        return {
            "running": self.running,
            "pending": len(state.get("pending", {})),
            "last_uploaded": state.get("last_uploaded"),
            "error": state.get("error"),
            "completed": state.get("completed", {}),
            "warning": state.get("warning"),
        }

    def enqueue(self, entry: dict, path: Path) -> None:
        with self._lock:
            state = self._read()
            pending = state.setdefault("pending", {})
            pending[entry["name"]] = {"path": str(path), "entry": entry}
            self._save(state)
        self._wake.set()

    def retry(self) -> None:
        self._wake.set()

    def process(self) -> None:
        if self.backups.s.settings.get("backup_destination") != "remote" or not self._work.acquire(blocking=False):
            return
        self.running = True
        try:
            revision = self.remote.metadata().get("revision")
            # Publishing the local archive and recording its upload cannot be one filesystem
            # transaction. Reconcile on every retry to cover a crash between those two writes.
            with self.backups._lock:
                entries = self.backups.list()
                with self._lock:
                    state = self._read()
                    for entry in entries:
                        name = entry["name"]
                        if not entry.get("damaged") and state.get("completed", {}).get(name) != revision:
                            state.setdefault("pending", {}).setdefault(
                                name, {"path": str(self.backups.dir / name), "entry": entry}
                            )
                    if entries:
                        self._save(state)
            with self._lock:
                pending = dict(self._read().get("pending", {}))
            for name, item in pending.items():
                if self._stop.is_set() or self.backups.s.settings.get("backup_destination") != "remote":
                    break
                with self.backups._lock:
                    path = Path(item["path"])
                    if not path.is_file():
                        with self._lock:
                            state = self._read()
                            state.get("pending", {}).pop(name, None)
                            state["warning"] = (
                                f"The pending local backup {name} is missing. Other backups will still be uploaded."
                            )
                            self._save(state)
                        continue
                    self.active = name
                with self.remote.lock:
                    revision = self.remote.metadata().get("revision")
                    self.remote.upload(path)
                    with self._lock:
                        state = self._read()
                        state.setdefault("pending", {}).pop(name, None)
                        state.setdefault("completed", {})[name] = revision
                        state["last_uploaded"] = {"name": name, "created": datetime.now().isoformat(timespec="seconds")}
                        state["error"] = None
                        self._save(state)
                    self._prune_remote()
                self.active = None
            if self.remote.metadata():
                self._prune_remote()
                with self._lock:
                    state = self._read()
                    state["error"] = None
                    self._save(state)
            with self.backups._lock:
                self.backups.prune()
        except BackupError as e:
            with self._lock:
                state = self._read()
                state["error"] = str(e)
                self._save(state)
            log.warning("Remote backup: %s", e)
        except Exception:
            # Do not let an unexpected failure silently terminate all subsequent retries.
            with self._lock:
                state = self._read()
                state["error"] = "Remote backup work failed. Retry from Settings → Backups."
                self._save(state)
            log.error("Remote backup work failed")
        finally:
            self.active = None
            self.running = False
            self._work.release()

    def _prune_remote(self) -> None:
        entries = self.backups.remote_list()
        keep = max(1, int(self.backups.s.settings.get("backup_keep") or 1))
        for entry in entries[keep:]:
            self.remote.delete(entry["name"])

    def forget(self, names: list[str]) -> None:
        if not names or not self.remote.root.exists():
            return
        with self._lock:
            state = self._read()
            for name in names:
                state.get("completed", {}).pop(name, None)
                if state.get("pending", {}).pop(name, None):
                    state["warning"] = (
                        "Some older pending uploads exceeded local retention. The newest local backups are still available for retry."
                    )
            self._save(state)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.remote.rclone.start()
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                self._wake.clear()
                self.process()
                self._wake.wait(RETRY_SECONDS)

        self._thread = threading.Thread(target=loop, name="backup-delivery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.remote.rclone.stop()
        self._wake.set()
        if self._thread:
            self._thread.join(5)
