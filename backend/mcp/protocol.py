"""The MCP message layer: JSON-RPC 2.0 requests in, responses out.

Independent of the transport. ``Server.handle(message)`` takes one decoded
JSON-RPC message (or a list of them) and returns the reply to send back, or
``None`` for notifications. ``serve_stdio`` runs it over stdin/stdout, one JSON
message per line, which is how Claude Desktop and Claude Code start local
servers. ``http.py`` runs it over HTTP for apps on another computer.

Only what a tools-only server needs is implemented: ``initialize``, ``ping``,
``tools/list`` and ``tools/call`` (plus empty ``resources/list``,
``resources/templates/list`` and ``prompts/list`` for clients that ask anyway).
Nothing in it is particular to one AI app: Claude, Codex, Cursor … all speak this.
The app named in ``initialize`` (``Server.client``) is recorded with what the tools change.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..library.agent import acting_as
from .clients import client_label

log = logging.getLogger(__name__)

# Newest first. A client asking for one of these gets it back; otherwise we answer with the newest.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603


class ToolError(Exception):
    """A problem the model should read and can fix (unknown key, bad page range…).
    Reported as a tool result with ``isError: true``, not as a protocol error."""


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    fn: Callable[..., Any]
    read_only: bool = True

    def describe(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "idempotentHint": self.read_only,
                "openWorldHint": False,
            },
        }


def text_content(result: Any) -> list[dict]:
    """Turn a tool's return value into MCP content blocks.

    A string is sent as is; a dict/list as indented JSON; an ``Image`` as an image block;
    ``Parts`` (a list of these) as several blocks."""
    if isinstance(result, Parts):
        return [_block(p) for p in result]
    return [_block(result)]


def _block(p: Any) -> dict:
    if isinstance(p, Image):
        return {"type": "image", "data": base64.b64encode(p.data).decode("ascii"), "mimeType": p.mime}
    if isinstance(p, str):
        return {"type": "text", "text": p}
    return {"type": "text", "text": _json(p)}


class Parts(list):
    """Several content blocks (e.g. a JSON summary followed by a note's Markdown)."""


@dataclass
class Image:
    data: bytes
    mime: str = "image/png"


def _json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, indent=1, default=str)


@dataclass
class Server:
    name: str
    version: str
    instructions: str = ""
    tools: dict[str, Tool] = field(default_factory=dict)
    before_call: Callable[[], None] | None = None  # e.g. refresh indexes before each tool call
    # More tools, asked for at each tools/list and tools/call (plugins come and go while it runs)
    extra: Callable[[], dict[str, Tool]] | None = None
    # The AI app connected (from initialize's clientInfo): notes and tasks record it with each change
    client: str | None = None

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def all_tools(self) -> dict[str, Tool]:
        if not self.extra:
            return self.tools
        try:
            more = self.extra()
        except Exception:
            log.exception("Listing the extra tools failed")
            more = {}
        return {**self.tools, **{name: tool for name, tool in more.items() if name not in self.tools}}

    # ------------------------------------------------------------------ dispatch
    def handle(self, message: Any) -> Any:
        if isinstance(message, list):  # JSON-RPC batch (older protocol versions)
            replies = [r for r in (self.handle(m) for m in message) if r is not None]
            return replies or None
        if (
            not isinstance(message, dict)
            or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)
        ):
            if isinstance(message, dict) and ("result" in message or "error" in message):
                return None  # a response to a request we never send; ignore
            return error_reply(
                message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "Invalid request"
            )
        method, params = message["method"], message.get("params") or {}
        is_request = "id" in message
        if not is_request:
            return None  # notifications (initialized, cancelled, …) need no reply
        rid = message["id"]
        try:
            handler = getattr(self, "_m_" + method.replace("/", "_"), None)
            if handler is None:
                return error_reply(rid, METHOD_NOT_FOUND, f"Method not found: {method}")
            if not isinstance(params, dict):
                return error_reply(rid, INVALID_PARAMS, "params must be an object")
            return {"jsonrpc": "2.0", "id": rid, "result": handler(params)}
        except _ProtocolError as e:
            return error_reply(rid, e.code, str(e))
        except Exception as e:  # never crash the server on one bad request
            log.exception("MCP request %s failed", method)
            return error_reply(rid, INTERNAL_ERROR, f"Internal error: {e}")

    # ------------------------------------------------------------------ methods
    def _m_initialize(self, params: dict) -> dict:
        asked = params.get("protocolVersion")
        self.client = client_label(params.get("clientInfo"))
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        out = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "title": "Synapse library", "version": self.version},
        }
        if self.instructions:
            out["instructions"] = self.instructions
        return out

    def _m_ping(self, params: dict) -> dict:
        return {}

    def _m_tools_list(self, params: dict) -> dict:
        return {"tools": [t.describe() for t in self.all_tools().values()]}

    def _m_resources_list(self, params: dict) -> dict:
        return {"resources": []}

    def _m_resources_templates_list(self, params: dict) -> dict:
        return {"resourceTemplates": []}

    def _m_prompts_list(self, params: dict) -> dict:
        return {"prompts": []}

    def _m_tools_call(self, params: dict) -> dict:
        name = params.get("name")
        tool = self.all_tools().get(name) if isinstance(name, str) else None
        if tool is None:
            raise _ProtocolError(INVALID_PARAMS, f"Unknown tool: {name}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise _ProtocolError(INVALID_PARAMS, "arguments must be an object")
        try:
            args = check_args(tool.input_schema, args)
            if self.before_call:
                self.before_call()
            with acting_as(self.client):
                result = tool.fn(**args)
            return {"content": text_content(result), "isError": False}
        except ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:
            log.error("Tool %s failed:\n%s", name, traceback.format_exc())
            return {"content": [{"type": "text", "text": f"The tool failed: {type(e).__name__}: {e}"}], "isError": True}


class _ProtocolError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def error_reply(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


_TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


def check_args(schema: dict, args: dict) -> dict:
    """Light validation against the (flat) input schema: unknown names, required ones,
    basic types, enums and integer bounds. Raises ToolError with a readable message."""
    props = schema.get("properties", {})
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolError(f"Unknown argument(s): {', '.join(unknown)}. Accepted: {', '.join(props)}.")
    missing = [k for k in schema.get("required", []) if args.get(k) in (None, "")]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}.")
    out = {}
    for k, v in args.items():
        spec = props[k]
        if v is None:
            continue
        t = spec.get("type")
        if t == "integer" and isinstance(v, float) and v.is_integer():
            v = int(v)
        if t == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
            v = int(v)
        if t == "array" and isinstance(v, str):
            v = [v]  # a single value where a list is expected
        py = _TYPES.get(t)
        if py and (not isinstance(v, py) or (py is int and isinstance(v, bool))):
            raise ToolError(f"Argument '{k}' must be {'an' if t[0] in 'aeiou' else 'a'} {t}.")
        if "enum" in spec and v not in spec["enum"]:
            raise ToolError(f"Argument '{k}' must be one of: {', '.join(map(str, spec['enum']))}.")
        if t == "integer":
            if "minimum" in spec and v < spec["minimum"]:
                raise ToolError(f"Argument '{k}' must be at least {spec['minimum']}.")
            if "maximum" in spec and v > spec["maximum"]:
                raise ToolError(f"Argument '{k}' must be at most {spec['maximum']}.")
        out[k] = v
    return out


def serve_stdio(server: Server, stdin=None, stdout=None) -> None:
    """Read one JSON-RPC message per line from stdin, write each reply as one line to stdout.

    Anything else that prints goes to stderr: ``sys.stdout`` is redirected while
    serving, because a stray line on stdout would corrupt the protocol stream."""
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    real_stdout, sys.stdout = sys.stdout, sys.stderr
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as e:
                reply = error_reply(None, PARSE_ERROR, f"Parse error: {e}")
            else:
                reply = server.handle(message)
            if reply is not None:
                out.write(json.dumps(reply, ensure_ascii=False, default=str) + "\n")
                out.flush()
    finally:
        sys.stdout = real_stdout
