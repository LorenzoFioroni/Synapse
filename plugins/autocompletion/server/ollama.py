"""A small client for the Ollama HTTP API (https://github.com/ollama/ollama/blob/main/docs/api.md).

Only what autocompletion needs: the installed models, a model's capabilities, and one completion.
Every failure becomes `Unavailable` with a reason code and a message that can be shown as is.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Protocol
from urllib.parse import urlsplit

from backend.plugins import HttpError, Offline, PluginError


class HttpClient(Protocol):
    def request(self, method: str, url: str, **kwargs) -> Any: ...


CONNECT_TIMEOUT = 1.0  # seconds: Ollama runs on this computer, so it answers at once or not at all
STATUS_TIMEOUT = 4.0
GENERATE_TIMEOUT = 60.0  # the first request after a while also loads the model into memory
KEEP_ALIVE = "30m"  # how long Ollama keeps the model loaded after the last request


class Unavailable(Exception):
    """Ollama or the model cannot be used. ``reason`` is a code the frontend understands:
    unreachable · no_model · unsupported · error · timeout."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def same_model(installed: str, wanted: str) -> bool:
    """Ollama names a model ``name:tag``; a name without a tag means ``:latest``."""
    a, b = installed.strip().lower(), wanted.strip().lower()
    return a == b or a == f"{b}:latest" or f"{a}:latest" == b


class Ollama:
    def __init__(self, base_url: str, http: HttpClient):
        self.base = base_url.rstrip("/")
        self.http = http

    def _call(self, method: str, path: str, timeout: float, payload: dict | None = None) -> Any:
        try:
            r = self.http.request(
                method,
                self.base + path,
                json=payload,
                timeout=(CONNECT_TIMEOUT, timeout),
                cache=False,
                raise_for_status=False,
            )
        except Offline as e:
            reason = "timeout" if "Timeout" in str(e) and "ConnectTimeout" not in str(e) else "unreachable"
            raise Unavailable(
                reason, "Ollama took too long to answer." if reason == "timeout" else self._not_running()
            ) from e
        except (HttpError, PluginError) as e:
            raise Unavailable("error", str(e)) from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status == 404 and path in ("/api/show", "/api/generate"):
            raise Unavailable("no_model", (data or {}).get("error") or "Model not found.")
        if r.status >= 400:
            msg = (data or {}).get("error") if isinstance(data, dict) else None
            if msg and "does not support" in msg:
                raise Unavailable("unsupported", f"Ollama: {msg}")
            if r.status == 404:
                # something answers on that port, but it is not Ollama (or a very old one)
                raise Unavailable("unreachable", f"No Ollama at {self.base} (it answered {r.status}).")
            raise Unavailable("error", f"Ollama: {msg or f'error {r.status}'}")
        if not isinstance(data, dict):
            raise Unavailable("unreachable", f"No Ollama at {self.base}.")
        return data

    def _not_running(self) -> str:
        return f"Ollama is not running at {self.base}. Is it installed and started?"

    def models(self) -> list[str]:
        """Names of the installed models (``name:tag``)."""
        data = self._call("GET", "/api/tags", STATUS_TIMEOUT)
        if "models" not in data:
            raise Unavailable("unreachable", f"No Ollama at {self.base}.")
        return sorted(str(m.get("name") or m.get("model") or "") for m in data["models"] if isinstance(m, dict))

    def version(self) -> str:
        try:
            return str(self._call("GET", "/api/version", STATUS_TIMEOUT).get("version") or "")
        except Unavailable:
            return ""

    def capabilities(self, model: str) -> set[str]:
        """What the model can do: "completion", "insert" (fill in the middle), "thinking", "embedding" …
        Older Ollama versions do not say; then plain completion is assumed."""
        data = self._call("POST", "/api/show", STATUS_TIMEOUT, {"model": model})
        caps = data.get("capabilities")
        if isinstance(caps, list):
            return {str(c) for c in caps}
        return {"completion"}

    def load(self, model: str) -> None:
        """Load the model into memory now, so the first suggestion does not wait for it."""
        self._call("POST", "/api/generate", GENERATE_TIMEOUT, {"model": model, "keep_alive": KEEP_ALIVE})

    def generate(self, payload: dict) -> str:
        data = self._call(
            "POST", "/api/generate", GENERATE_TIMEOUT, {**payload, "stream": False, "keep_alive": KEEP_ALIVE}
        )
        return str(data.get("response") or "")


DEFAULT_URL = "http://127.0.0.1:11434"  # Ollama's own default


def local_url(value: str) -> str:
    """The Ollama address, cleaned: http(s) on this computer only (localhost, 127.x.x.x, ::1),
    so a note's text is never sent elsewhere and the server cannot be pointed at other hosts.
    Empty means the default. Raises ValueError for any other address."""
    v = (value or "").strip().rstrip("/") or DEFAULT_URL
    if "://" not in v:
        v = "http://" + v
    try:
        u = urlsplit(v)
        host = (u.hostname or "").lower()
        u.port  # noqa: B018 - raises ValueError on a port that is not a number
    except ValueError as e:
        raise ValueError("Ollama address: something like http://127.0.0.1:11434") from e
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if u.scheme not in ("http", "https") or not loopback or u.username or u.password:
        raise ValueError("Ollama address: only a model on this computer is supported (localhost or 127.0.0.1)")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("Ollama address: just the address and port, e.g. http://127.0.0.1:11434")
    return f"{u.scheme}://{u.netloc}"
