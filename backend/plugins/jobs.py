"""A plugin's background work: queued jobs (kept in a small database, so they survive a restart and
the MCP server can queue work the app then does) and scheduled runs. One thread per plugin does
both, in the web app only, one thing at a time.

A job that fails is tried again after 30 s, 2 min and 10 min (``retries`` times); a failure to
reach the web (``HttpError``) does not count against the plugin, any other error does.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import PluginError
from .http import HttpError
from .schedule import Schedule

BACKOFF = (30, 120, 600)  # seconds before the 1st, 2nd, 3rd … retry
POLL_S = 2.0  # how often the queue is looked at when nothing woke the runner
QUEUE_FILE = ".jobs.sqlite"  # a dot file: not in backups (work to do, not data)
STATE_FILE = ".schedules.json"


@dataclass
class Handler:
    fn: Callable
    retries: int = 3


class JobQueue:
    def __init__(self, folder: Path):
        self.path = folder / QUEUE_FILE
        folder.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.executescript(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    key TEXT UNIQUE,
                    args TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    run_after REAL NOT NULL,
                    created TEXT NOT NULL,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_due ON jobs (run_after, id);"""
            )

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def add(self, name: str, args: list, key: str | None = None, delay: float = 0) -> bool:
        """Queue ``name(*args)``; with ``key``, not again while one with that key waits. True if added."""
        payload = json.dumps(args)
        with self._connect() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO jobs (name, key, args, run_after, created) VALUES (?, ?, ?, ?, ?)",
                (name, key, payload, time.time() + max(0.0, delay), datetime.now(timezone.utc).isoformat()),
            )
            return cur.rowcount > 0

    def next_due(self, now: float | None = None) -> dict | None:
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE run_after <= ? ORDER BY run_after, id LIMIT 1",
                (time.time() if now is None else now,),
            ).fetchone()
        return dict(row) if row else None

    def done(self, job_id: int) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def retry(self, job_id: int, error: str, wait: float) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE jobs SET attempts = attempts + 1, run_after = ?, last_error = ? WHERE id = ?",
                (time.time() + wait, error[:2000], job_id),
            )

    def pending(self) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT id, name, key, args, attempts, run_after, last_error FROM jobs ORDER BY run_after, id"
            )
            return [dict(r) for r in rows]


class Jobs:
    """``plugin.jobs``: queue work for a handler registered with ``@plugin.job(name)``."""

    def __init__(self, folder: Path, handlers: dict[str, Handler], wake: Callable[[], None]):
        self._folder = folder
        self._queue: JobQueue | None = None
        self._handlers = handlers
        self._wake = wake

    @property
    def queue(self) -> JobQueue:
        if self._queue is None:
            self._queue = JobQueue(self._folder)
        return self._queue

    def enqueue(self, name: str, *args, key: str | None = None, delay: float = 0) -> bool:
        """Run handler ``name`` with ``args`` (JSON values) in the background. ``key``: skip it
        while a job with the same key waits (say "refs:2610.01234"). True if it was queued."""
        if name not in self._handlers:
            raise PluginError(f"No job handler {name!r}; register one with @plugin.job({name!r})")
        try:
            added = self.queue.add(name, list(args), key, delay)
        except TypeError as e:
            raise PluginError(f"Job {name}: its arguments must be JSON values ({e})") from e
        if added:
            self._wake()
        return added

    def pending(self) -> list[dict]:
        return self.queue.pending()


class Runner:
    """The thread that runs one plugin's jobs and schedules."""

    def __init__(
        self,
        pid: str,
        folder: Path,
        log: logging.Logger,
        handlers: dict[str, Handler],
        schedules: list[Schedule],
        fault: Callable[[str, BaseException], None],
    ):
        self.pid = pid
        self.folder = folder
        self.log = log
        self.handlers = handlers
        self.schedules = schedules
        self.fault = fault
        self.queue = JobQueue(folder)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=f"plugin-jobs-{self.pid}", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    # ------------------------------------------------------------------ schedules
    def _state(self) -> dict:
        try:
            data = json.loads((self.folder / STATE_FILE).read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _remember(self, name: str, when: datetime) -> None:
        state = self._state()
        state[name] = when.isoformat()
        tmp = self.folder / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(state, indent=1), "utf-8")
        tmp.replace(self.folder / STATE_FILE)

    def run_schedules(self) -> int:
        """Run the schedules that are due; returns how many ran."""
        ran = 0
        state = self._state()
        for s in self.schedules:
            if self._stop.is_set():
                break
            last = None
            if s.name in state:
                try:
                    last = datetime.fromisoformat(state[s.name])
                except ValueError:
                    last = None
            now = self.now()
            go, remember = s.due(now, last)
            if remember is not None:
                self._remember(s.name, remember)
            if not go:
                continue
            self._remember(s.name, now)  # before running: a failure is not retried in a loop
            start = time.monotonic()
            try:
                s.run()
                self.log.info("Scheduled %s ran (%.1f s)", s.name, time.monotonic() - start)
            except HttpError as e:
                self.log.warning("Scheduled %s could not finish: %s", s.name, e)
            except Exception as e:
                self.fault(f"schedule {s.name}", e)
            ran += 1
        return ran

    # ------------------------------------------------------------------ jobs
    def run_one(self) -> bool:
        """Run the next due job, if any; True if one ran."""
        job = self.queue.next_due()
        if not job:
            return False
        h = self.handlers.get(job["name"])
        if not h:
            self.log.error("Job %s dropped: no handler of that name any more", job["name"])
            self.queue.done(job["id"])
            return True
        what = f"job {job['name']}" + (f" ({job['key']})" if job["key"] else "")
        try:
            h.fn(*json.loads(job["args"]))
        except Exception as e:
            reachable = not isinstance(e, HttpError)
            if reachable:
                self.fault(what, e)
            if job["attempts"] < h.retries:
                wait = BACKOFF[min(job["attempts"], len(BACKOFF) - 1)]
                self.log.warning("%s failed (%s); trying again in %s s", what, e, wait)
                self.queue.retry(job["id"], "".join(traceback.format_exception_only(type(e), e)).strip(), wait)
            else:
                self.log.error("%s failed %s times; giving up: %s", what, job["attempts"] + 1, e)
                self.queue.done(job["id"])
            return True
        self.queue.done(job["id"])
        return True

    def _loop(self) -> None:
        last_sched = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() - last_sched >= 20:
                    last_sched = time.monotonic()
                    self.run_schedules()
                while not self._stop.is_set() and self.run_one():
                    pass
            except Exception:  # the queue itself failed (disk full …): keep the thread alive
                self.log.error("Background work failed:\n%s", traceback.format_exc())
            self._wake.wait(POLL_S)
            self._wake.clear()
