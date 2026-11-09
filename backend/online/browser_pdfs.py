"""Immediate PDF handoff to a browser already listening; no durable/offline work queue."""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass

POLL_SECONDS = 20.0
ACCEPT_SECONDS = 5.0
MAX_BROWSERS = 16


class InvalidBrowserClient(ValueError):
    """The client ID does not match the browser receiver format."""


class BrowserClientBusy(ValueError):
    """The client is already polling or accepting an offer."""


class BrowserReceiverLimit(ValueError):
    """All receiver slots are occupied."""


@dataclass
class _Offer:
    client: str
    ids: list[str]
    deadline: float
    accepted: bool | None = None


class BrowserPdfs:
    def __init__(self):
        self._condition = threading.Condition()
        self._listeners: set[str] = set()
        self._offers: dict[str, _Offer] = {}

    @property
    def available(self) -> bool:
        with self._condition:
            return bool(self._listeners)

    def poll(self, client: str, *, timeout: float = POLL_SECONDS) -> dict | None:
        if not isinstance(client, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client):
            raise InvalidBrowserClient
        with self._condition:
            if client in self._listeners or any(o.client == client for o in self._offers.values()):
                raise BrowserClientBusy
            if len(self._listeners) + len(self._offers) >= MAX_BROWSERS:
                raise BrowserReceiverLimit
            self._listeners.add(client)
            self._condition.notify_all()
            deadline = time.monotonic() + timeout
            try:
                while True:
                    for key, offer in self._offers.items():
                        if offer.client == client:
                            return {"id": key, "ids": offer.ids} if offer.accepted is None else None
                    if client not in self._listeners:
                        return None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._condition.wait(remaining)
            finally:
                self._listeners.discard(client)

    def request(self, ids: list[str], *, timeout: float = ACCEPT_SECONDS) -> bool:
        with self._condition:
            if not ids or not self._listeners:
                return False
            client = self._listeners.pop()
            key = uuid.uuid4().hex
            offer = _Offer(client, ids, time.monotonic() + timeout)
            self._offers[key] = offer
            self._condition.notify_all()
            try:
                while offer.accepted is None:
                    remaining = offer.deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._condition.wait(remaining)
                return offer.accepted
            finally:
                self._offers.pop(key, None)
                self._condition.notify_all()

    def claim(self, client: str, key: str) -> float:
        """Seconds left to open the finder; stale deliveries cannot start a later search."""
        with self._condition:
            offer = self._offers.get(key)
            if not offer or offer.client != client or offer.accepted is not None:
                return 0.0
            return max(0.0, offer.deadline - time.monotonic())

    def acknowledge(self, client: str, key: str, accepted: bool) -> bool:
        with self._condition:
            if not self.claim(client, key):
                return False
            self._offers[key].accepted = accepted
            self._condition.notify_all()
            return True

    def leave(self, client: str) -> None:
        with self._condition:
            self._listeners.discard(client)
            for offer in self._offers.values():
                if offer.client == client and offer.accepted is None:
                    offer.accepted = False
            self._condition.notify_all()
