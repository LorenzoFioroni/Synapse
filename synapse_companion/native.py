"""The native messaging host: the browser starts it (``native-host``) for the Synapse extension and
talks to it over stdin/stdout. It passes the extension's requests to the server with the
companion's token, so the browser holds no token and the extension never reaches the server itself.

Only ``/api/connector/`` paths are passed on (what the extension does: save, file, find and attach
PDFs, the live PDF receiver); the rest of the API, the companion endpoints and ``/mcp`` are refused.
Chrome starts it only for the extension ids in its manifest's ``allowed_origins``.

Messages are JSON, each prefixed by its length (4 bytes, native byte order). Requests may be large
(a PDF, in base64; Chrome allows 64 MiB); answers are cut into parts well under Chrome's 1 MB.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import struct
import sys
import threading
import uuid
from typing import Any, BinaryIO

from .common import Fail, clean, load_profile
from .extension import record_seen
from .remote import NetworkError, Server

MAX_IN = 64 * 1024 * 1024
CHUNK = 128 * 1024  # characters per answer part; even fully escaped this stays under 1 MB
MAX_TIMEOUT = 60
PATH = re.compile(r"/api/connector/[A-Za-z0-9/_-]*(\?[A-Za-z0-9._~%&=+,:-]*)?")
NOT_CONNECTED = "Synapse Companion is not connected to a library yet. Open Synapse Companion and connect it."


class Host:
    def __init__(self, profile: str, stdin: BinaryIO, stdout: BinaryIO):
        self.profile, self.stdin, self.stdout = profile, stdin, stdout
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------ framing
    def read(self) -> dict | None:
        """The next message, or None when the browser closed the connection."""
        head = self.stdin.read(4)
        if len(head) < 4:
            return None
        (size,) = struct.unpack("=I", head)
        if size > MAX_IN:
            raise Fail("Message too large.")
        data = self.stdin.read(size)
        if len(data) < size:
            return None
        try:
            message = json.loads(data)
        except ValueError:
            return {}
        return message if isinstance(message, dict) else {}

    def send(self, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=False).encode()
        with self._write_lock:
            self.stdout.write(struct.pack("=I", len(data)) + data)
            self.stdout.flush()

    # ------------------------------------------------------------------ messages
    def serve(self) -> None:
        while (message := self.read()) is not None:
            msg_id = message.get("id")
            if not isinstance(msg_id, str) or len(msg_id) > 100:
                continue
            threading.Thread(target=self.handle, args=(msg_id, message), daemon=True).start()

    def handle(self, msg_id: str, message: dict) -> None:
        try:
            for reply in self.answer(message):
                self.send({"id": msg_id, **reply})
        except Fail as e:
            self.send({"id": msg_id, "type": "error", "message": str(e)})
        except NetworkError as e:
            self.send({"id": msg_id, "type": "error", "message": f"Synapse Companion cannot reach the library: {e}"})
        except Exception:  # never let one message end the connection
            self.send({"id": msg_id, "type": "error", "message": "Synapse Companion could not pass on the request."})

    def answer(self, message: dict):
        profile = load_profile(self.profile)
        if profile is None:
            raise Fail(NOT_CONNECTED)
        kind = message.get("type")
        if kind == "hello":
            record_seen(self.profile, message.get("version"), message.get("build"))
            yield {"type": "hello", "server": profile["server"], "name": profile["name"]}
            return
        if kind != "request":
            raise Fail("Unknown request.")
        method, path = message.get("method"), message.get("path")
        if method not in ("GET", "POST") or not isinstance(path, str) or not PATH.fullmatch(path):
            raise Fail("Synapse Companion passes on the extension's library requests only.")
        timeout = message.get("timeout")
        timeout = min(float(timeout), MAX_TIMEOUT) if isinstance(timeout, (int, float)) and timeout > 0 else 30.0
        data, content_type = body(message) if method == "POST" else (None, None)
        status, raw = Server(profile["server"], profile["token"]).forward(
            method, path, data, content_type, timeout=timeout
        )
        text = raw.decode("utf-8", errors="replace")
        parts = [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]
        for i, part in enumerate(parts):
            yield {"type": "response", "status": status, "body": part, "more": i < len(parts) - 1}


def body(message: dict) -> tuple[bytes | None, str | None]:
    """The request body: JSON from ``json``, or multipart form data from ``form``."""
    if "form" in message:
        return multipart(message["form"])
    if "json" in message:
        return json.dumps(message["json"]).encode(), "application/json"
    return None, None


def _header_value(text: str) -> str:
    return clean(text).replace('"', "'")[:200]


def multipart(form: Any) -> tuple[bytes, str]:
    if not isinstance(form, list) or len(form) > 50:
        raise Fail("Invalid form.")
    boundary = uuid.uuid4().hex
    out = bytearray()
    for entry in form:
        if not (isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], str)):
            raise Fail("Invalid form.")
        name, value = _header_value(entry[0]), entry[1]
        out += f"--{boundary}\r\n".encode()
        if isinstance(value, str):
            out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value.encode()
        elif isinstance(value, dict) and isinstance(value.get("data"), str):
            try:
                data = base64.b64decode(value["data"], validate=True)
            except (binascii.Error, ValueError):
                raise Fail("Invalid file in the form.") from None
            filename = _header_value(str(value.get("filename") or "file"))
            ctype = str(value.get("type") or "")
            ctype = ctype if re.fullmatch(r"[\w.+-]+/[\w.+-]+", ctype) else "application/octet-stream"
            out += (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'
            ).encode() + data
        else:
            raise Fail("Invalid form.")
        out += b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def main(profile: str) -> int:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), 0x8000)  # O_BINARY: no newline translation
        msvcrt.setmode(sys.stdout.fileno(), 0x8000)
    try:
        Host(profile, sys.stdin.buffer, sys.stdout.buffer).serve()
    except Fail:
        return 1
    return 0
