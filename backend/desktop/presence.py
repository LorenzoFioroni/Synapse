"""Which Synapse windows are open, so a server started by the desktop shortcut can stop with its
last window, like a program whose window was closed.

Each app page says hello every 30 seconds (``frontend/src/presence.ts``) and goodbye when it
closes. The server stops:

* ``BYE_GRACE`` seconds after the last window said goodbye (a reload says hello again sooner);
* when no window was heard from for ``SILENCE`` seconds (closed while the computer slept, the
  browser crashed; a hidden window is still heard from, at least once a minute);
* ``FIRST`` seconds after starting if no window ever said hello (it never opened).

A server started any other way does not watch (``ping`` answers False and the pages stop asking).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

BYE_GRACE = 15.0
SILENCE = 600.0
FIRST = 180.0


class Presence:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._clients: dict[str, float] = {}  # client id → when it was last heard from
        self._empty_since = clock()
        self._ever = False  # some window has said hello
        self.watching = False

    def ping(self, client: str) -> bool:
        """A window is open. Answers whether the server watches (otherwise it need not ask again)."""
        with self._lock:
            if self.watching and client:
                self._clients[client] = self._clock()
                self._ever = True
            return self.watching

    def bye(self, client: str) -> None:
        """A window closed (or reloads)."""
        with self._lock:
            if self._clients.pop(client, None) is not None and not self._clients:
                self._empty_since = self._clock()

    def open_windows(self) -> int:
        with self._lock:
            return len(self._clients)

    def idle(self) -> bool:
        """Whether the server should stop now."""
        with self._lock:
            now = self._clock()
            for c, seen in list(self._clients.items()):
                if now - seen > SILENCE:
                    del self._clients[c]
                    if not self._clients:
                        self._empty_since = now
            if self._clients:
                return False
            return now - self._empty_since >= (BYE_GRACE if self._ever else FIRST)

    def watch(self, on_idle: Callable[[], None], check_every: float = 5.0) -> None:
        """Call ``on_idle`` (once, from a background thread) when the last window is gone."""
        with self._lock:
            self.watching = True
            self._empty_since = self._clock()

        def run() -> None:
            while not self.idle():
                time.sleep(check_every)
            log.info("No Synapse window is open any more: stopping")
            on_idle()

        threading.Thread(target=run, name="presence", daemon=True).start()
