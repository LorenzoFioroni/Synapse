"""The MCP server over HTTP, for AI apps on another computer (Streamable HTTP, the simple form).

The web app serves it at ``/mcp`` (``backend/api/mcp.py``); the guard lets a request through only
with an access token (``backend/access/tokens.py``). Each POST carries one JSON-RPC message (or a
batch) and gets the reply as ``application/json``: the tools answer at once, so there is no event
stream, and no session id is needed because the server keeps no state between requests.

Requests are handled one at a time, as over stdio: the tools keep caches that are not made for
several threads.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from .protocol import INVALID_REQUEST, PARSE_ERROR, PROTOCOL_VERSIONS, Server, error_reply


class HttpTransport:
    def __init__(self, build: Callable[[], Server], wrote: Callable[[], None] | None = None):
        self._build = build  # the server is made on the first request
        self._wrote = wrote  # called after a tool that may write (a note, a task) has run
        self._server: Server | None = None
        self._lock = threading.Lock()

    def handle(self, body: bytes, protocol_version: str | None = None) -> tuple[int, Any]:
        """One POST: returns (HTTP status, JSON reply or None for an empty 202)."""
        if protocol_version and protocol_version not in PROTOCOL_VERSIONS:
            return 400, error_reply(None, INVALID_REQUEST, f"Unsupported MCP protocol version: {protocol_version}")
        try:
            message = json.loads(body)
        except (ValueError, UnicodeDecodeError) as e:
            return 400, error_reply(None, PARSE_ERROR, f"Parse error: {e}")
        with self._lock:
            if self._server is None:
                self._server = self._build()
            reply = self._server.handle(message)
            writes = self._writes(self._server, message)
        if writes and self._wrote:
            self._wrote()
        if reply is None:
            return 202, None  # notifications and responses only
        return 200, reply

    @staticmethod
    def _writes(server: Server, message: Any) -> bool:
        messages = message if isinstance(message, list) else [message]
        for m in messages:
            if isinstance(m, dict) and m.get("method") == "tools/call" and isinstance(m.get("params"), dict):
                name = m["params"].get("name")
                tool = server.tools.get(name) if isinstance(name, str) else None
                if tool is not None and not tool.read_only:
                    return True
        return False
