"""Attachments of notes (images in ``attachments/``): which are in use, the attachment trash, and
emptying the notes trash after a while.

An attachment is in use while a note shows it (``![](attachments/x.png)``), counting the notes in
the notes trash (restoring one brings its images back with it). One that no note uses any more is
moved to ``.trash-attachments/<timestamp>/`` — never right after it was added (its note may not
be saved yet) — and comes back by itself if a note uses it again. Entries of both trashes older
than the retention setting are deleted for good; their age counts from when they were deleted,
but never from before this feature first ran (``.trash-attachments/.since``), so a trash that
filled up over the years is not emptied the moment the app is updated.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

from .parser import parse
from .vault import ATTACHMENTS, TRASH, InvalidPath

log = logging.getLogger(__name__)

ATTACHMENT_TRASH = ".trash-attachments"
GRACE_SECONDS = 3600  # a new attachment is not "unused" until its note had time to be saved
STAMP = "%Y%m%d-%H%M%S-%f"

RETENTION = {  # setting value -> seconds (None: keep forever)
    "never": None,
    "1d": 86400,
    "1w": 7 * 86400,
    "2w": 14 * 86400,
    "1m": 30 * 86400,
    "6m": 182 * 86400,
}


def attachment_name(target: str) -> str | None:
    """The file under ``attachments/`` an image path written in a note refers to (as the editor
    resolves it: ``attachments/x.png``, ``./attachments/x.png``, ``x.png``), or None for URLs."""
    t = (target or "").strip()
    if not t or re.match(r"^([a-z][a-z0-9+.-]*:|//)", t, re.I):
        return None
    t = t.split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
    while t.startswith("./") or t.startswith("../"):
        t = t[2:] if t.startswith("./") else t[3:]
    if t.lower().startswith(ATTACHMENTS + "/"):
        t = t[len(ATTACHMENTS) + 1 :]
    parts = [p for p in PurePosixPath(t).parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _stamp_time(name: str, fallback: Path) -> float:
    try:
        return datetime.strptime(name, STAMP).timestamp()
    except ValueError:
        try:
            return fallback.stat().st_mtime
        except OSError:
            return time.time()


class Attachments:
    def __init__(self, vault, index, settings):
        self.vault = vault
        self.index = index
        self.settings = settings
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._soon: threading.Timer | None = None
        self._started = False

    @property
    def dir(self) -> Path:
        return self.vault.root / ATTACHMENTS

    @property
    def trash_dir(self) -> Path:
        return self.vault.root / ATTACHMENT_TRASH

    def since(self) -> float:
        """When automatic emptying started for this notes folder (written the first time)."""
        marker = self.trash_dir / ".since"
        try:
            return float(marker.read_text().strip())
        except (OSError, ValueError):
            now = time.time()
            try:
                self.trash_dir.mkdir(parents=True, exist_ok=True)
                marker.write_text(str(now))
            except OSError:
                pass
            return now

    def _deleted_at(self, stamp_dir: Path) -> float:
        """When an entry of a trash counts as deleted (for emptying)."""
        return max(_stamp_time(stamp_dir.name, stamp_dir), self.since())

    # ------------------------------------------------------------- in use
    def usage(self) -> dict[str, list[str]]:
        """attachment name -> the notes showing it (trashed notes as ``.trash/…``)."""
        out: dict[str, list[str]] = {}
        for path, target in self.index.image_links():
            name = attachment_name(target)
            if name:
                out.setdefault(name, []).append(path)
        base = self.vault.root / TRASH
        if base.is_dir():
            for f in base.rglob("*.md"):
                try:
                    text = f.read_text("utf-8", errors="replace")
                except OSError:
                    continue
                rel = f.relative_to(self.vault.root).as_posix()
                for link in parse(text).links:
                    if link.kind == "image":
                        name = attachment_name(link.target)
                        if name:
                            out.setdefault(name, []).append(rel)
        return out

    def files(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        return sorted(
            p.relative_to(self.dir).as_posix()
            for p in self.dir.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        )

    def trashed(self) -> list[dict]:
        out = []
        if self.trash_dir.is_dir():
            for stamp_dir in sorted(self.trash_dir.iterdir(), reverse=True):
                if not stamp_dir.is_dir():
                    continue
                when = _stamp_time(stamp_dir.name, stamp_dir)
                for f in sorted(stamp_dir.rglob("*")):
                    if f.is_file():
                        out.append(
                            {
                                "trash_id": stamp_dir.name,
                                "name": f.relative_to(stamp_dir).as_posix(),
                                "trashed_at": datetime.fromtimestamp(when).isoformat(timespec="seconds"),
                                "size": f.stat().st_size,
                            }
                        )
        return out

    def with_deletion_dates(self, notes: list[dict]) -> list[dict]:
        """Trashed notes ({trash_id, path}) with trashed_at and, with a retention, deletes_at."""
        secs = RETENTION.get(self.settings.get("notes_trash_retention"))
        out = []
        for n in notes:
            d = self.vault.root / TRASH / n["trash_id"]
            when = _stamp_time(n["trash_id"], d)
            out.append(
                {
                    **n,
                    "trashed_at": datetime.fromtimestamp(when).isoformat(timespec="seconds"),
                    **(
                        {"deletes_at": datetime.fromtimestamp(self._deleted_at(d) + secs).isoformat(timespec="seconds")}
                        if secs
                        else {}
                    ),
                }
            )
        return out

    def overview(self) -> dict:
        usage = self.usage()
        files = []
        for name in self.files():
            p = self.dir / name
            st = p.stat()
            files.append(
                {
                    "name": name,
                    "path": f"{ATTACHMENTS}/{name}",
                    "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                    "used_by": sorted(usage.get(name, [])),
                }
            )
        retention = self.settings.get("notes_trash_retention")
        secs = RETENTION.get(retention)
        trash = self.trashed()
        if secs:
            for t in trash:
                start = max(datetime.fromisoformat(t["trashed_at"]).timestamp(), self.since())
                t["deletes_at"] = datetime.fromtimestamp(start + secs).isoformat(timespec="seconds")
        return {"attachments": files, "trash": trash, "retention": retention}

    # ------------------------------------------------------------- sweeping
    def sweep(self, now: float | None = None) -> dict:
        """Move unused attachments to the attachment trash, bring back trashed ones that are in
        use again. Returns {"trashed": [...], "restored": [...]}."""
        now = time.time() if now is None else now
        with self._lock:
            usage = self.usage()
            trashed, restored = [], []
            # back from the trash: a note uses it again (e.g. that note was restored)
            for t in self.trashed():
                if t["name"] in usage and not (self.dir / t["name"]).exists():
                    self._move(self.trash_dir / t["trash_id"] / t["name"], self.dir / t["name"])
                    restored.append(t["name"])
            stamp = datetime.fromtimestamp(now).strftime(STAMP)
            for name in self.files():
                if name in usage:
                    continue
                p = self.dir / name
                try:
                    if now - p.stat().st_mtime < GRACE_SECONDS:
                        continue
                except OSError:
                    continue
                self._move(p, self.trash_dir / stamp / name)
                trashed.append(name)
            self._tidy(self.dir)
            self._tidy(self.trash_dir)
        if trashed or restored:
            log.info("Attachments: %d moved to the trash, %d restored", len(trashed), len(restored))
        return {"trashed": trashed, "restored": restored}

    def restore(self, trash_id: str, name: str) -> str:
        src = self.trashed_path(trash_id, name)
        with self._lock:
            dst = self.dir / name
            if dst.exists():
                raise InvalidPath(f"An attachment called {name} already exists")
            self._move(src, dst)
            os.utime(dst)  # restored now: not swept again right away
            self._tidy(self.trash_dir)
        return f"{ATTACHMENTS}/{name}"

    def delete(self, trash_id: str, name: str) -> None:
        """Delete one trashed attachment for good."""
        src = self.trashed_path(trash_id, name)
        with self._lock:
            src.unlink()
            self._tidy(self.trash_dir)

    def purge(self, now: float | None = None) -> dict:
        """Delete trashed attachments and trashed notes older than the retention setting."""
        secs = RETENTION.get(self.settings.get("notes_trash_retention"))
        if not secs:
            return {"attachments": 0, "notes": 0}
        now = time.time() if now is None else now
        counts = {"attachments": 0, "notes": 0}
        with self._lock:
            for base, key in ((self.trash_dir, "attachments"), (self.vault.root / TRASH, "notes")):
                if not base.is_dir():
                    continue
                for stamp_dir in base.iterdir():
                    if not stamp_dir.is_dir() or now - self._deleted_at(stamp_dir) < secs:
                        continue
                    n = sum(
                        1 for f in stamp_dir.rglob("*") if f.is_file() and (key == "attachments" or f.suffix == ".md")
                    )
                    shutil.rmtree(stamp_dir, ignore_errors=True)
                    counts[key] += n
        if counts["attachments"] or counts["notes"]:
            log.info("Emptied the notes trash: %(notes)d notes and %(attachments)d attachments deleted", counts)
        return counts

    def maintain(self) -> dict:
        """Sweep, then purge. Never raises (it runs in the background)."""
        try:
            return {**self.sweep(), "purged": self.purge()}
        except Exception:
            log.exception("Attachment maintenance failed")
            return {}

    def poke(self, delay: float = 5) -> None:
        """Notes changed: sweep a little later (once, however many changes come in meanwhile).
        Only once the background maintenance runs (not in tests that never start it)."""
        if not self._started:
            return
        if self._soon:
            self._soon.cancel()
        self._soon = threading.Timer(delay, self._safe_sweep)
        self._soon.daemon = True
        self._soon.start()

    def _safe_sweep(self) -> None:
        try:
            self.sweep()
        except Exception:
            log.exception("Attachment sweep failed")

    def start(self, every: float = 3600) -> None:
        """Run maintain() now and then every hour, in the background."""
        self._started = True

        def run():
            self.maintain()
            self._timer = threading.Timer(every, run)
            self._timer.daemon = True
            self._timer.start()

        t = threading.Thread(target=run, daemon=True)
        t.start()

    def stop(self) -> None:
        self._started = False
        for t in (self._timer, self._soon):
            if t:
                t.cancel()

    # ------------------------------------------------------------- helpers
    def trashed_path(self, trash_id: str, name: str) -> Path:
        if not re.fullmatch(r"[0-9\-]+", trash_id or ""):
            raise InvalidPath("Bad trash id")
        clean = attachment_name(name)
        if not clean:
            raise InvalidPath("Bad attachment name")
        p = self.trash_dir / trash_id / clean
        if not p.is_file():
            raise FileNotFoundError(clean)
        return p

    @staticmethod
    def _move(src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)

    @staticmethod
    def _tidy(base: Path) -> None:
        """Remove emptied subfolders (never `base` itself)."""
        if not base.is_dir():
            return
        for dirpath, _, _ in sorted(os.walk(base), key=lambda t: -len(t[0])):
            if Path(dirpath) != base:
                try:
                    Path(dirpath).rmdir()
                except OSError:
                    pass
