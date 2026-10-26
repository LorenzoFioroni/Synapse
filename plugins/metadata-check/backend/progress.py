"""Track the items actually due in a run, including unsuccessful attempts."""

import threading
import time


class Progress:
    def __init__(self, state, changed):
        self.state = state
        self.changed = changed
        self.remaining: set[str] = set()
        self.running = False
        self.lock = threading.RLock()
        # An interrupted process cannot still own a running check.
        self.state.set("running", False)

    def start(self, ids):
        with self.lock:
            self.remaining = set(ids)
            self.running = True
            for key, value in {"running": True, "done": 0, "total": len(self.remaining), "failed": 0}.items():
                self.state.set(key, value)
            self.changed()

    def done(self, item_id, failed=False):
        with self.lock:
            if item_id not in self.remaining:
                return
            self.remaining.remove(item_id)
            self.state.set("done", self.state.get("done", 0) + 1)
            if failed:
                self.state.set("failed", self.state.get("failed", 0) + 1)
            self.changed()

    def finish(self):
        with self.lock:
            if self.remaining:
                return
            self.running = False
            now = time.time()
            self.state.set("running", False)
            self.state.set("last_checked", now)
            self.state.set(
                "last_run", {"total": self.state.get("done", 0), "failed": self.state.get("failed", 0), "at": now}
            )
            self.changed()
