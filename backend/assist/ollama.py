"""A small client for the Ollama HTTP API (https://github.com/ollama/ollama/blob/main/docs/api.md).

Only what autocompletion needs: the installed models, a model's capabilities, and one completion.
Every failure becomes `Unavailable` with a reason code and a message that can be shown as is.
"""

from __future__ import annotations

from typing import Any

import requests

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
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def _call(self, method: str, path: str, timeout: float, payload: dict | None = None) -> Any:
        try:
            r = requests.request(method, self.base + path, json=payload, timeout=(CONNECT_TIMEOUT, timeout))
        except requests.Timeout as e:
            if isinstance(e, requests.ConnectTimeout):
                raise Unavailable("unreachable", self._not_running()) from e
            raise Unavailable("timeout", "Ollama took too long to answer.") from e
        except requests.RequestException as e:
            raise Unavailable("unreachable", self._not_running()) from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 404 and path in ("/api/show", "/api/generate"):
            raise Unavailable("no_model", (data or {}).get("error") or "Model not found.")
        if not r.ok:
            msg = (data or {}).get("error") if isinstance(data, dict) else None
            if msg and "does not support" in msg:
                raise Unavailable("unsupported", f"Ollama: {msg}")
            if r.status_code == 404:
                # something answers on that port, but it is not Ollama (or a very old one)
                raise Unavailable("unreachable", f"No Ollama at {self.base} (it answered {r.status_code}).")
            raise Unavailable("error", f"Ollama: {msg or f'error {r.status_code}'}")
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
