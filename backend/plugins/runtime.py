"""What runs a plugin's code safely: its log file, the worker thread its event listeners run on, and
the count of its failures."""

from __future__ import annotations

import logging
import logging.handlers
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

LOG_FILE = ".plugin.log"  # a dot file: left out of backups
LOG_BYTES = 256 * 1024
SLOW_S = 10.0  # a listener taking longer than this is reported in the log
FAULTS = 3  # this many failures …
FAULT_WINDOW_S = 600  # … within this many seconds turn the plugin off


def make_logger(pid: str, folder: Path) -> tuple[logging.Logger, logging.Handler | None]:
    """The logger a plugin gets as ``plugin.log``; it writes to the plugin's log file (shown in
    Settings → Plugins) and, like every logger, to the server's output."""
    log = logging.getLogger(f"synapse.plugin.{pid}")
    log.setLevel(logging.INFO)
    handler: logging.Handler | None = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            folder / LOG_FILE, maxBytes=LOG_BYTES, backupCount=1, encoding="utf-8", delay=True
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        log.addHandler(handler)
    except OSError:
        handler = None
    return log, handler


def tail(folder: Path, lines: int = 500) -> list[str]:
    """The last ``lines`` lines of a plugin's log (from both processes that may run it)."""
    out: deque[str] = deque(maxlen=lines)
    for p in (folder / (LOG_FILE + ".1"), folder / LOG_FILE):
        try:
            with p.open(encoding="utf-8", errors="replace") as f:
                out.extend(line.rstrip("\n") for line in f)
        except OSError:
            continue
    return list(out)


class Faults:
    """Failures of one plugin; ``add`` says when there were too many in a short time."""

    def __init__(self):
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def add(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._times.append(now)
            while self._times and now - self._times[0] > FAULT_WINDOW_S:
                self._times.popleft()
            return len(self._times) >= FAULTS


class Worker:
    """One thread per plugin that runs its event listeners in order, so a slow plugin delays only
    itself and never a request."""

    def __init__(self, pid: str, log: logging.Logger, on_error: Callable[[str, BaseException], None]):
        self.pid = pid
        self.log = log
        self.on_error = on_error
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = False

    def submit(self, what: str, fn: Callable, *args) -> None:
        with self._lock:
            if self._stopped:
                return
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=f"plugin-{self.pid}", daemon=True)
                self._thread.start()
            self._q.put((what, fn, args))

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            what, fn, args = job
            start = time.monotonic()
            try:
                fn(*args)
            except BaseException as e:
                self.on_error(what, e)
            took = time.monotonic() - start
            if took > SLOW_S:
                self.log.warning("%s took %.0f s", what, took)

    def drain(self, timeout: float = 5.0) -> bool:
        """Wait until the jobs submitted so far have run (tests use this). False on timeout."""
        done = threading.Event()
        self.submit("drain", done.set)
        return done.wait(timeout)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stopped = True
            thread = self._thread
        if thread:
            self._q.put(None)
            thread.join(timeout)
