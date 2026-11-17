"""Requests to the Synapse server, always with the companion token and never following a redirect."""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .common import Fail, clean

MAX_DOWNLOAD = 50 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class NetworkError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the token to wherever it points; treat it as an error instead."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def check_url(url: str) -> str:
    """The server's base URL. Remote servers must use https; http is for this computer only."""
    parts = urllib.parse.urlsplit(url.strip())
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host:
        raise Fail("Give the server's address, such as https://synapse.example.com")
    if parts.scheme == "http":
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise Fail("A server on another computer needs an https:// address: the token must not travel in clear.")
    return f"{parts.scheme}://{parts.netloc}"


class Server:
    def __init__(self, base: str, token: str):
        self.base = check_url(base)
        self.token = token

    def _request(self, method: str, path: str, data: bytes | None, content_type: str | None, timeout: float):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "synapse-companion",
        }
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        return _opener.open(req, timeout=timeout)

    def call(self, method: str, path: str, payload: Any = None, *, timeout: float = 30, raw: bool = False):
        data = None if payload is None else json.dumps(payload).encode()
        try:
            with self._request(method, path, data, "application/json" if data else None, timeout) as resp:
                body = resp.read(MAX_DOWNLOAD + 1)
        except urllib.error.HTTPError as e:
            message = e.reason
            try:
                message = json.loads(e.read(65536)).get("error") or message
            except (ValueError, AttributeError, OSError):
                pass
            if 300 <= e.code < 400:
                message = "The server redirected the request; check the address."
            raise ApiError(e.code, clean(message)) from None
        except (urllib.error.URLError, OSError, ValueError) as e:  # TimeoutError is an OSError
            raise NetworkError(clean(getattr(e, "reason", e))) from None
        if len(body) > MAX_DOWNLOAD:
            raise ApiError(0, "The download is too large.")
        if raw:
            return body
        try:
            answer = json.loads(body or b"{}")
        except ValueError:
            raise ApiError(0, "The server sent an answer that is not JSON.") from None
        if not isinstance(answer, dict):
            raise ApiError(0, "The server sent an unexpected answer.")
        return answer

    def forward(
        self, method: str, path: str, data: bytes | None, content_type: str | None, *, timeout: float = 30
    ) -> tuple[int, bytes]:
        """A request passed on for the browser extension: (status, body) whatever the status, so the
        extension sees the server's own answers (409 duplicate, 400 with a message …)."""
        try:
            with self._request(method, path, data, content_type, timeout) as resp:
                status, body = resp.status, resp.read(MAX_DOWNLOAD + 1)
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                raise NetworkError("The server redirected the request; check the address.") from None
            status, body = e.code, e.read(MAX_DOWNLOAD + 1)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise NetworkError(clean(getattr(e, "reason", e))) from None
        if len(body) > MAX_DOWNLOAD:
            raise NetworkError("The answer is too large.")
        return status, body

    def hello(self) -> dict:
        """``/api/companion/hello`` with only the fields used, whatever the server sends."""
        answer = self.call("GET", "/api/companion/hello")

        def build(key: str) -> str:
            part = answer.get(key)
            value = part.get("build") if isinstance(part, dict) else None
            return value if isinstance(value, str) else ""

        return {
            "app": answer.get("app"),
            "version": clean(answer.get("version", "")),
            "extension": build("extension"),
            "script": build("script"),
        }
