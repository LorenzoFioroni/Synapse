"""Bounded scholarly retries over plugin.http, with an activation-owned stop signal."""

from __future__ import annotations

import threading
from urllib.parse import urlsplit

from backend.plugins import HttpError, PluginError


class FetchFailed(PluginError):
    """Reference providers failed; retrying later may succeed."""


class Stopped(PluginError):
    """This activation was retired; do not fall through to another provider."""


class Client:
    def __init__(self, http):
        self.http = http
        self._stopped = threading.Event()

    def close(self) -> None:
        self._stopped.set()

    def ensure_open(self) -> None:
        if self._stopped.is_set():
            raise Stopped("Reference Sources was stopped.")

    def wait(self, seconds: float) -> None:
        self._stopped.wait(seconds)
        self.ensure_open()

    def get(self, url: str, *, params=None, retries: int = 1):
        """One retry on 429/selected 5xx; 404 means no record. Every attempt uses the host policy."""
        for attempt in range(retries + 1):
            self.ensure_open()
            response = self.http.get(url, params=params, raise_for_status=False)
            self.ensure_open()
            if response.status == 404:
                return None
            if response.status in (429, 500, 502, 503, 504) and attempt < retries:
                retry_after = next((v for k, v in response.headers.items() if k.lower() == "retry-after"), "")
                try:
                    delay = float(retry_after or 2)
                except ValueError:
                    delay = 2
                self.wait(min(max(delay, 0), 10))
                continue
            if response.status >= 400:
                host = urlsplit(response.url).hostname or "Reference service"
                raise HttpError(f"{host}: HTTP {response.status}", response.status)
            return response
        return None
