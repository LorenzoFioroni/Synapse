"""Autocompletion for the notes editor: the component behind the plugin's routes.

Most computers have no Ollama, or not the chosen model, so nothing is asked of the model before
`status` has found both. The status is remembered for a short while (shorter when something is
missing, so starting Ollama is noticed soon). Requests run one at a time: a request that is still
waiting when a newer one arrives gives up, because the person has typed on since.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from . import prompt
from .ollama import GENERATE_TIMEOUT, Ollama, Unavailable, local_url, same_model

log = logging.getLogger(__name__)

STATUS_TTL_OK = 60.0  # seconds a good status is trusted
STATUS_TTL_BAD = 15.0  # ... and a bad one, before Ollama is asked again
WARM_EVERY = 300.0  # at most one "load the model now" request per this many seconds


class Completer:
    def __init__(self, values: Callable[[], dict], client: Callable[[str], Ollama]):
        self.values = values  # the plugin's settings: model, length, url
        self._client = client
        self._run = threading.Lock()  # one generation at a time
        self._lock = threading.Lock()  # guards the fields below
        self._seq = 0
        self._status: dict | None = None
        self._status_key: tuple[str, str] | None = None
        self._status_at = 0.0
        self._warmed_at = 0.0

    def _config(self) -> tuple[bool, str, str, str]:
        """(on, Ollama's address, the model, the length). The plugin running means it is on."""
        v = self.values()
        return True, str(v.get("url") or ""), str(v.get("model") or ""), str(v.get("length") or "line")

    def forget(self) -> None:
        """The settings changed: ask Ollama again before the next suggestion."""
        self._forget_status()

    # ------------------------------------------------------------------ status
    def status(self, *, fresh: bool = False, warm: bool = False) -> dict:
        """Whether suggestions can be made, and if not, why. Asks Ollama only when the remembered
        answer is too old, the settings changed, or ``fresh`` is set (Settings' Check button).
        ``warm`` also loads the model into memory in the background (when the editor opens)."""
        enabled, url, model, _ = self._config()
        key = (url, model)
        now = time.monotonic()
        with self._lock:
            st, age = self._status, now - self._status_at
            usable = st is not None and self._status_key == key
            if usable and st is not None:
                usable = age < (STATUS_TTL_OK if st["ok"] else STATUS_TTL_BAD)
        if fresh or not usable:
            st = self._probe(url, model)
            with self._lock:
                self._status, self._status_key, self._status_at = st, key, time.monotonic()
        assert st is not None
        if warm and enabled and st["ok"]:
            self._warm(url, model)
        return {**st, "enabled": enabled, "url": url, "model": model}

    def _forget_status(self) -> None:
        with self._lock:
            self._status = None

    def _probe(self, url: str, model: str) -> dict:
        out = {"ok": False, "reason": "", "message": "", "models": [], "version": "", "fim": False, "thinking": False}
        try:
            url = local_url(url)
        except ValueError as e:
            return {**out, "reason": "bad_url", "message": str(e)}
        if not model or len(model) > 200 or any(c.isspace() for c in model):
            return {**out, "reason": "unsupported", "message": "Model: a name such as qwen2.5-coder:1.5b"}
        client = self._client(url)
        try:
            models = client.models()
        except Unavailable as e:
            return {**out, "reason": e.reason, "message": str(e)}
        out["models"] = models
        out["version"] = client.version()
        if not any(same_model(m, model) for m in models):
            return {
                **out,
                "reason": "no_model",
                "message": f"The model {model} is not installed in Ollama. Install it with: ollama pull {model}",
            }
        try:
            caps = client.capabilities(model)
        except Unavailable as e:
            return {**out, "reason": e.reason, "message": str(e)}
        if not caps & {"completion", "insert"}:
            return {
                **out,
                "reason": "unsupported",
                "message": f"{model} cannot write text (an embedding model?). Choose another model.",
            }
        fim = "insert" in caps
        message = (
            "Ready."
            if fim
            else "Ready. This model only continues the text before the cursor; a fill-in-the-middle model (e.g. qwen2.5-coder) also sees the text after it."
        )
        return {**out, "ok": True, "message": message, "fim": fim, "thinking": "thinking" in caps}

    def _warm(self, url: str, model: str) -> None:
        with self._lock:
            if time.monotonic() - self._warmed_at < WARM_EVERY and self._warmed_at:
                return
            self._warmed_at = time.monotonic()

        def run() -> None:
            try:
                self._client(local_url(url)).load(model)
            except Unavailable as e:
                log.info("Could not load %s in Ollama: %s", model, e)

        threading.Thread(target=run, name="assist-warm", daemon=True).start()

    # -------------------------------------------------------------- complete
    def complete(self, prefix: str, suffix: str) -> dict:
        """A suggestion for the cursor between ``prefix`` and ``suffix``: ``{"text": …}``, with
        ``reason`` (and ``message``) set when there is none because of a problem: off,
        unreachable, no_model, unsupported, timeout, error, or superseded (a newer request came)."""
        enabled, url, model, length = self._config()
        if not enabled:
            return {"text": "", "reason": "off"}
        with self._lock:
            self._seq += 1
            mine = self._seq
        st = self.status()
        if not st["ok"]:
            return {"text": "", "reason": st["reason"], "message": st["message"]}
        if not self._run.acquire(timeout=GENERATE_TIMEOUT):
            return {"text": "", "reason": "timeout", "message": "Ollama is busy."}
        try:
            if mine != self._seq:
                return {"text": "", "reason": "superseded"}
            fim = bool(st["fim"])
            payload = prompt.build(model, prefix, suffix, fim=fim, thinking=bool(st["thinking"]), length=length)
            try:
                raw = self._client(local_url(url)).generate(payload)
            except Unavailable as e:
                self._forget_status()  # look again before the next request
                return {"text": "", "reason": e.reason, "message": str(e)}
            return {"text": prompt.clean(raw, prefix, suffix, length, fim=fim), "reason": ""}
        finally:
            self._run.release()
