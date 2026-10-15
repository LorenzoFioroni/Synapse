"""A stand-in for Ollama's HTTP API, for the unit tests and the browser checklist.

It answers /api/tags, /api/version, /api/show and /api/generate on a free port of 127.0.0.1 and
records every request. ``reply(payload)`` decides what /api/generate answers.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOllama:
    def __init__(
        self,
        models: tuple[str, ...] = ("qwen2.5-coder:1.5b",),
        capabilities: tuple[str, ...] = ("completion", "insert"),
        reply: Callable[[dict], str] | None = None,
        delay: float = 0.0,
    ):
        self.models = list(models)
        self.capabilities = list(capabilities)
        self.reply = reply or (lambda payload: " world")
        self.delay = delay
        self.requests: list[tuple[str, dict]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, status: int, data: dict) -> None:
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def do_GET(self):
                fake.requests.append((self.path, {}))
                if self.path == "/api/tags":
                    self._send(200, {"models": [{"name": m, "model": m} for m in fake.models]})
                elif self.path == "/api/version":
                    self._send(200, {"version": "0.12.0"})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                data = self._body()
                fake.requests.append((self.path, data))
                known = any(m == data.get("model") or m == f"{data.get('model')}:latest" for m in fake.models)
                if self.path == "/api/show":
                    if not known:
                        self._send(404, {"error": f"model '{data.get('model')}' not found"})
                    else:
                        self._send(200, {"capabilities": fake.capabilities, "details": {}})
                elif self.path == "/api/generate":
                    if not known:
                        self._send(404, {"error": f"model '{data.get('model')}' not found"})
                        return
                    if "prompt" not in data:  # a load request
                        self._send(200, {"response": "", "done": True})
                        return
                    if fake.delay:
                        time.sleep(fake.delay)
                    self._send(200, {"response": fake.reply(data), "done": True})
                else:
                    self._send(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> FakeOllama:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    def generated(self) -> list[dict]:
        return [d for p, d in self.requests if p == "/api/generate" and "prompt" in d]


def free_port() -> int:
    """A port nothing listens on (for 'Ollama is not running')."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
