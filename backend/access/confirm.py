"""Confirming with a passkey before an administrative action on another device.

Pairing a device, making an access token, adding or removing a passkey and removing other devices
give lasting access, so they were allowed only on the computer running Synapse. When Synapse runs on
a computer you don't sit at (a server reached through a tunnel, ``proxy.py``), a signed-in device may
do them too, but only for a few minutes after proving again that its user holds one of Synapse's
passkeys (Face ID, Touch ID …). A stolen device cookie alone is then not enough to hand out new access.

The confirmations live in memory: a restart forgets them, which only means confirming again.
"""

from __future__ import annotations

import threading
import time

CONFIRM_TTL = 5 * 60  # seconds a confirmation lasts


class Confirmations:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._until: dict[str, float] = {}  # device id → when its confirmation runs out
        self._lock = threading.Lock()

    def confirm(self, dev_id: str) -> float:
        """Record that this device has just confirmed with a passkey. Returns when that runs out."""
        until = self.clock() + CONFIRM_TTL
        with self._lock:
            now = self.clock()
            self._until = {d: t for d, t in self._until.items() if t > now}
            self._until[dev_id] = until
        return until

    def valid(self, dev_id: str | None) -> bool:
        if not dev_id:
            return False
        with self._lock:
            return self._until.get(dev_id, 0) > self.clock()

    def forget(self, dev_id: str) -> None:
        with self._lock:
            self._until.pop(dev_id, None)


def may_administer(this_computer: bool, device: dict | None, confirmations: Confirmations) -> bool:
    """The computer running Synapse always may; another device after a recent passkey confirmation."""
    return this_computer or (device is not None and confirmations.valid(device.get("id")))
