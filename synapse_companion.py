#!/usr/bin/env python3
"""Synapse Companion: connects a Synapse server to services on this computer.

It answers "Ask AI" questions from Synapse by running your own AI command-line tool (Claude Code;
Codex only with ``run --allow codex``) restricted to Synapse's tools, and it can keep the Synapse
browser extension folder up to date. One file, Python standard library only. See docs/companion.md.

    python synapse_companion.py connect https://synapse.example.com
    python synapse_companion.py run
    python synapse_companion.py extension
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

POLL_TIMEOUT = 35  # the server answers a poll within 20 s
MAX_RUNNING = 2
FLUSH_EVERY = 0.15
TOOL_PREFIX = "mcp__synapse__"
MAX_DOWNLOAD = 50 * 1024 * 1024
SYNC_EVERY = 3600
ENV_TOKEN = "SYNAPSE_COMPANION_TOKEN"
CHILD_TOKEN = "SYNAPSE_MCP_TOKEN"  # how a child gets the token when it cannot read a config file


def clean(text: object) -> str:
    """Text from the server for the terminal, without control characters (escape sequences)."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(text)).strip()


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {clean(message)}", flush=True)


class Fail(Exception):
    """An error to show the user (no traceback)."""


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class NetworkError(Exception):
    pass


# ---------------------------------------------------------------------- server access


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

    def call(self, method: str, path: str, payload: Any = None, *, timeout: float = 30, raw: bool = False):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "synapse-companion",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with _opener.open(req, timeout=timeout) as resp:
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


# ---------------------------------------------------------------------- profiles


def home() -> Path:
    if env := os.environ.get("SYNAPSE_COMPANION_HOME"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "synapse-companion"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "synapse-companion"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "synapse-companion"


def profile_path(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        raise Fail("A profile name is letters, digits, - and _ (up to 40).")
    return home() / f"{name}.json"


def load_profile(name: str) -> dict:
    try:
        profile = json.loads(profile_path(name).read_text(encoding="utf-8"))
        check_url(profile["server"])
        ext = profile.get("extension")
        return {
            **profile,
            "extension": ext if isinstance(ext, dict) else None,
            "token": str(profile["token"]),
            "client": str(profile["client"]),
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise Fail(f"Not connected yet. Run:  python {Path(sys.argv[0]).name} connect <server address>") from None


def save_profile(name: str, profile: dict) -> None:
    path = profile_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the token is a secret
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    os.replace(tmp, path)


def own_build() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------------- extension folder


SECURITY_KEYS = (
    "permissions",
    "optional_permissions",
    "host_permissions",
    "optional_host_permissions",
    "content_scripts",
    "externally_connectable",
    "web_accessible_resources",
    "background",
    "content_security_policy",
)


class ExtensionReview(Fail):
    """An extension update changes what the extension may do and needs the user's say-so."""

    def __init__(self, keys: list[str]):
        super().__init__("The extension update changes: " + ", ".join(keys))
        self.keys = keys


def _safe_extract(data: bytes, target: Path) -> None:
    """Unpack the zip into ``target``: regular files only, nothing outside it."""
    root = target.resolve()
    try:
        _extract(data, root)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, RuntimeError, EOFError, NotImplementedError):
        raise Fail("The extension download could not be unpacked.") from None
    if not (root / "manifest.json").is_file():
        raise Fail("The download is not a browser extension (no manifest.json).")


def _extract(data: bytes, root: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > 2000 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
            raise Fail("The extension download is larger than expected.")
        for info in infos:
            name = info.filename
            mode = info.external_attr >> 16
            if info.is_dir():
                continue
            parts = name.split("/")
            if (
                name.startswith(("/", "\\"))
                or "\\" in name
                or re.match(r"[A-Za-z]:", name)
                or any(p in ("", ".", "..") for p in parts)
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
            ):
                raise Fail(f"The download contains an unsafe path: {clean(repr(name))}")
            dest = (root / name).resolve()
            if root not in dest.parents:
                raise Fail(f"The download contains an unsafe path: {clean(repr(name))}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def folder_build(folder: Path) -> str | None:
    """The build of the files in ``folder``, computed as the server does (names and contents of the
    regular, non-hidden files except build.json), so local edits show; None if it cannot be read."""
    digest = hashlib.sha256()
    try:
        for path in sorted(folder.rglob("*")):
            rel = path.relative_to(folder)
            if path.is_symlink() or not path.is_file() or any(p.startswith(".") for p in rel.parts):
                continue
            if rel.as_posix() == "build.json":
                continue
            digest.update(rel.as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    except OSError:
        return None
    return digest.hexdigest()[:16]


def _manifest(folder: Path) -> dict:
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def sync_extension(
    server: Server, folder: Path, *, force: bool = False, review: Callable[[list[str]], bool] | None = None
) -> bool:
    """Download the extension into ``folder`` when it differs from the server's. True if it changed.

    An update that changes the manifest's security-relevant keys (``SECURITY_KEYS``) is applied only
    if ``review`` approves the list of changed keys; without ``review`` it raises ``ExtensionReview``.
    A non-empty folder is replaced only if an earlier download put a build.json in it."""
    build = server.hello()["extension"]
    if not build:
        raise Fail("This server has no browser extension to download.")
    if not force and build == folder_build(folder):
        return False
    installed = folder.is_dir() and any(folder.iterdir())
    if installed and not (folder / "build.json").is_file():
        raise Fail(
            f"{folder} is not a folder this program installed (it has no build.json); choose another with --dir."
        )
    folder.parent.mkdir(parents=True, exist_ok=True)
    new = Path(tempfile.mkdtemp(prefix=folder.name + ".new-", dir=folder.parent))  # ours alone
    old = folder.with_name(f"{folder.name}.old-{uuid.uuid4().hex[:8]}")
    try:
        _safe_extract(server.call("GET", "/api/companion/extension", raw=True, timeout=120), new)
        if installed:
            before, after = _manifest(folder), _manifest(new)
            changed = [k for k in SECURITY_KEYS if before.get(k) != after.get(k)]
            if changed and not (review and review(changed)):
                raise ExtensionReview(changed)
        # Two renames, not one atomic step: a crash between them leaves the old copy as <folder>.old-…
        if folder.exists():
            os.replace(folder, old)
        try:
            os.replace(new, folder)
        except OSError:
            if old.exists():
                os.replace(old, folder)
            raise Fail("The extension folder could not be replaced; it was left as it was.") from None
    finally:
        shutil.rmtree(new, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    return True


# ---------------------------------------------------------------------- running a question


class Abort(Exception):
    """Stop the job with this message for the user."""


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)  # the tool and anything it started
    except (OSError, ProcessLookupError):
        return

    def kill():
        if proc.poll() is None:
            try:
                proc.kill() if sys.platform == "win32" else os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass

    threading.Timer(3, kill).start()


class Sink:
    """Sends a job's events to the server in small batches."""

    def __init__(self, server: Server, client: str, job_id: str, on_stop: Callable[[], None]):
        self.server, self.client, self.job_id, self.on_stop = server, client, job_id, on_stop
        self._pending: list[dict] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        self._failing_since = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def push(self, event: dict) -> None:
        with self._lock:
            if event["type"] == "text" and self._pending and self._pending[-1]["type"] == "text":
                self._pending[-1]["text"] += event["text"]
            else:
                self._pending.append(event)

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        self._thread.join(timeout=20)

    def _loop(self) -> None:
        while not self._closed:
            self._wake.wait(FLUSH_EVERY)
            self._flush()
        self._flush()

    def _flush(self) -> None:
        while True:
            with self._lock:
                batch: list[dict] = []
                size = 0
                while self._pending and len(batch) < 64 and size < 100_000:
                    event = self._pending.pop(0)
                    if event["type"] == "text" and len(event["text"]) > 100_000:
                        event, rest = {**event, "text": event["text"][:100_000]}, event["text"][100_000:]
                        self._pending.insert(0, {"type": "text", "text": rest})
                    size += len(event.get("text", ""))
                    batch.append(event)
            if not batch:
                return
            try:
                reply = self.server.call(
                    "POST", f"/api/companion/jobs/{self.job_id}/events", {"client": self.client, "events": batch}
                )
                self._failing_since = 0.0
                if reply.get("cancelled"):
                    self.on_stop()
            except ApiError as e:
                if e.status in (404, 409):  # the job is gone or not ours: nobody reads these events
                    self.on_stop()
                    return
                self._retry(batch)
                return
            except NetworkError:
                self._retry(batch)
                return

    def _retry(self, batch: list[dict]) -> None:
        with self._lock:
            self._pending[:0] = batch
        self._failing_since = self._failing_since or time.monotonic()
        if time.monotonic() - self._failing_since > 60:
            self.on_stop()
            with self._lock:
                self._pending.clear()
        elif not self._closed:
            time.sleep(1)


class Provider:
    """How to run one AI tool restricted to Synapse's tools. Subclasses build the command and read
    the tool's output stream into events."""

    id = ""
    label = ""
    exe = ""

    def __init__(self, exe: str):
        self.exe = exe

    def command(self, job: dict, mcp_url: str, *, config: Path, system_file: Path, session: str) -> list[str]:
        """The argument list. Nothing the server sent may be in it (on Windows a ``.cmd`` tool is run
        through cmd.exe): the system text goes in ``system_file``, the session id is derived here."""
        raise NotImplementedError

    def env(self, token: str) -> dict[str, str]:
        return {k: v for k, v in os.environ.items() if k not in (ENV_TOKEN, CHILD_TOKEN)}

    def stdin(self, job: dict) -> str:
        return job["prompt"]

    def stream(self, job: dict, emit: Callable[[dict], None]) -> Stream:
        raise NotImplementedError


class Stream:
    """Reads the lines of a tool's output; calls ``emit`` with events; sets ``finished`` at the end."""

    def __init__(self, emit: Callable[[dict], None]):
        self.emit = emit
        self.finished = False

    def line(self, line: str) -> None:
        raise NotImplementedError


class ClaudeStream(Stream):
    def __init__(self, emit):
        super().__init__(emit)
        self.verified = False
        self.streamed = False

    def line(self, line: str) -> None:
        try:
            e = json.loads(line)
        except ValueError:
            return
        if not isinstance(e, dict):
            return
        kind = e.get("type")
        if kind == "system" and e.get("subtype") == "init":
            tools = e.get("tools")
            if not isinstance(tools, list) or not tools:
                raise Abort("Claude Code could not reach Synapse's tools; check the server address and token.")
            bad = [t for t in tools if not (isinstance(t, str) and t.startswith(TOOL_PREFIX))]
            if bad:
                raise Abort("Stopped: Claude Code offered tools other than Synapse's.")
            self.verified = True
        elif kind in ("stream_event", "assistant", "user", "result") and not self.verified:
            raise Abort("Stopped: Claude Code did not report which tools it can use.")
        elif kind == "stream_event" and isinstance(e.get("event"), dict) and not e.get("parent_tool_use_id"):
            ev = e["event"]
            block = ev.get("content_block") or {}
            delta = ev.get("delta") or {}
            if ev.get("type") == "content_block_start" and block.get("type") == "tool_use":
                self.emit({"type": "tool", "name": str(block.get("name", "")).removeprefix(TOOL_PREFIX)})
            elif ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                self.streamed = True
                self.emit({"type": "text", "text": str(delta.get("text", ""))})
        elif kind == "result":
            self.finished = True
            text = e.get("result") if isinstance(e.get("result"), str) else ""
            if e.get("is_error"):
                self.emit({"type": "error", "message": text or "Claude Code failed."})
            else:
                if not self.streamed and text:
                    self.emit({"type": "text", "text": text})
                self.emit({"type": "done"})


class Claude(Provider):
    id, label = "claude", "Claude Code"

    def command(self, job: dict, mcp_url: str, *, config: Path, system_file: Path, session: str) -> list[str]:
        # Built-in tools, skills and slash commands off, only Synapse's MCP server, no user/project
        # settings or other servers. The token is in the 0600 config file and the server's system text
        # in a 0600 file, so argv holds only fixed words, file paths and a derived session id.
        return [
            self.exe,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            str(config),
            "--allowedTools",
            "mcp__synapse",
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--append-system-prompt-file",
            str(system_file),
            "--resume" if job["resume"] else "--session-id",
            session,
        ]

    def stream(self, job: dict, emit: Callable[[dict], None]) -> Stream:
        return ClaudeStream(emit)


class CodexStream(Stream):
    def __init__(self, emit, on_thread: Callable[[str], None]):
        super().__init__(emit)
        self.on_thread = on_thread
        self.messages = 0

    def line(self, line: str) -> None:
        try:
            e = json.loads(line)
        except ValueError:
            return
        if not isinstance(e, dict):
            return
        kind = e.get("type")
        item: dict = e["item"] if isinstance(e.get("item"), dict) else {}
        if kind == "thread.started" and isinstance(e.get("thread_id"), str):
            self.on_thread(e["thread_id"])
        elif kind == "item.started" and item.get("type") == "mcp_tool_call":
            self.emit({"type": "tool", "name": str(item.get("tool", ""))})
        elif kind == "item.started" and item.get("type") == "command_execution":
            self.emit({"type": "tool", "name": "shell"})
        elif kind == "item.completed" and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            self.emit({"type": "text", "text": ("\n\n" if self.messages else "") + item["text"]})
            self.messages += 1
        elif kind == "turn.completed":
            self.finished = True
            self.emit({"type": "done"})
        elif kind in ("turn.failed", "error"):
            self.finished = True
            err: dict = e["error"] if isinstance(e.get("error"), dict) else {}
            self.emit({"type": "error", "message": str(err.get("message") or e.get("message") or "Codex failed.")})


class Codex(Provider):
    id, label = "codex", "Codex"

    def __init__(self, exe: str):
        super().__init__(exe)
        self.threads: dict[str, str] = {}  # conversation -> Codex thread, for follow-ups

    def command(self, job: dict, mcp_url: str, *, config: Path, system_file: Path, session: str) -> list[str]:
        cmd = [self.exe, "exec"]
        thread = self.threads.get(job["conversation"]) if job["resume"] else None
        if thread:
            cmd.append("resume")
        cmd += [
            "--json",
            "--skip-git-repo-check",
            "-c",
            'sandbox_mode="read-only"',
            "-c",
            f"mcp_servers.synapse.url={json.dumps(mcp_url)}",
            "-c",
            f'mcp_servers.synapse.bearer_token_env_var="{CHILD_TOKEN}"',
        ]
        return cmd + ([thread, "-"] if thread else ["-"])

    def env(self, token: str) -> dict[str, str]:
        return {**super().env(token), CHILD_TOKEN: token}

    def stdin(self, job: dict) -> str:
        # Codex has no system-prompt option.
        return job["system"] + "\n\n" + job["prompt"]

    def stream(self, job: dict, emit: Callable[[dict], None]) -> Stream:
        return CodexStream(emit, lambda thread: self.threads.__setitem__(job["conversation"], thread))


def build_providers(allow: set[str], known: dict[str, Provider] | None = None) -> dict[str, Provider]:
    """The AI tools found on PATH now. Instances are kept while their executable stays the same, since
    Codex remembers its conversations' threads."""
    found: dict[str, Provider] = {}
    for cls in (Claude, Codex):
        exe = shutil.which(cls.id) if cls.id == "claude" or cls.id in allow else None
        if exe:
            old = (known or {}).get(cls.id)
            found[cls.id] = old if old is not None and old.exe == exe else cls(exe)
    return found


def _private_file(prefix: str, suffix: str, text: str, folder: Path) -> Path:
    """A new file only this user can read (mkstemp makes it 0600)."""
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return Path(name)


def mcp_config(url: str, token: str) -> dict:
    return {"mcpServers": {"synapse": {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"}}}}


class Job(threading.Thread):
    def __init__(self, companion: Companion, spec: dict):
        super().__init__(daemon=True, name=f"job-{spec['id'][:8]}")
        self.companion, self.spec = companion, spec
        self.id = spec["id"]
        self.stopped = threading.Event()
        self.reason = "server"  # who stopped it: the server (cancel) or this program (shutdown)
        self.proc: subprocess.Popen | None = None

    def stop(self, reason: str = "server") -> None:
        if not self.stopped.is_set():
            self.reason = reason
            self.stopped.set()
        if self.proc is not None:
            _terminate(self.proc)

    def run(self) -> None:
        c = self.companion
        sink = Sink(c.server, c.client, self.id, self.stop)
        try:
            self._run(sink)
        except Abort as e:
            self.stop("abort")
            sink.push({"type": "error", "message": c.redact(str(e))})
        except Exception as e:  # never let one job take the loop down
            log(f"job {self.id[:8]} failed: {type(e).__name__}")
            sink.push({"type": "error", "message": "The companion could not run the question."})
            c.debug(e)
        finally:
            if self.stopped.is_set() and self.reason == "shutdown":
                sink.push({"type": "error", "message": "The companion was stopped."})
            sink.close()
            c.finished(self)

    def _run(self, sink: Sink) -> None:
        c, spec = self.companion, self.spec
        provider = c.providers.get(str(spec.get("provider")))
        if provider is None:
            raise Abort(f"This companion does not offer {spec.get('provider')}.")
        for key in ("prompt", "system", "conversation"):
            if not isinstance(spec.get(key), str) or len(spec[key]) > 200_000:
                raise Abort("The server sent an invalid question.")
        spec["resume"] = bool(spec.get("resume"))
        config = _private_file(
            "mcp-", ".json", json.dumps(mcp_config(c.server.base + "/mcp", c.server.token)), c.work_root
        )
        system_file = _private_file("system-", ".txt", spec["system"], c.work_root)
        # Another server (or profile) must not resume this conversation's session, nor the reverse.
        session = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{c.server.base}|{spec['conversation']}"))
        try:
            log(f"job {self.id[:8]} started ({provider.id})")
            self._exec(
                provider.command(spec, c.server.base + "/mcp", config=config, system_file=system_file, session=session),
                provider.env(c.server.token),
                provider.stdin(spec),
                provider.stream(spec, sink.push),
                sink,
            )
        finally:
            config.unlink(missing_ok=True)
            system_file.unlink(missing_ok=True)
        log(f"job {self.id[:8]} ended")

    def _exec(self, cmd: list[str], env: dict, text: str, stream: Stream, sink: Sink) -> None:
        c = self.companion
        if self.stopped.is_set():
            return
        kwargs: dict[str, Any] = {"start_new_session": True} if sys.platform != "win32" else {}
        self.proc = proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=c.work_dir,
            env=env,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
        if self.stopped.is_set():
            _terminate(proc)
        tail: list[str] = []

        def read_stderr():
            assert proc.stderr
            for line in proc.stderr:
                tail.append(line)
                del tail[:-20]

        def feed():
            assert proc.stdin
            try:
                proc.stdin.write(text)
                proc.stdin.close()
            except OSError:
                pass

        threading.Thread(target=read_stderr, daemon=True).start()
        threading.Thread(target=feed, daemon=True).start()
        assert proc.stdout
        try:
            for line in proc.stdout:
                if self.stopped.is_set():
                    continue  # drain; the process is going away
                stream.line(line)
        except Abort:
            _terminate(proc)
            raise
        finally:
            code = proc.wait()
        if not stream.finished and not self.stopped.is_set():
            detail = c.redact("".join(tail).strip()[-400:])
            sink.push(
                {
                    "type": "error",
                    "message": f"The AI tool stopped without an answer (exit code {code}). {detail}".strip(),
                }
            )


# ---------------------------------------------------------------------- the loop


class Companion:
    def __init__(
        self,
        profile: dict,
        name: str,
        allow: Iterable[str] = (),
        *,
        home_dir: Path | None = None,
        profile_name: str = "default",
    ):
        self.server = Server(profile["server"], profile["token"])
        self.client = profile["client"]
        self.name = name
        self.allow = set(allow)
        self.profile = profile
        self.work_root = home_dir or home()
        self.work_dir = self.work_root / f"work-{profile_name}"  # empty: the AI tool finds no files here
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(mode=0o700, exist_ok=True)
        self.providers = build_providers(self.allow)
        self.jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.verbose = bool(os.environ.get("SYNAPSE_COMPANION_DEBUG"))

    def redact(self, text: str) -> str:
        return text.replace(self.server.token, "***")

    def debug(self, e: Exception) -> None:
        if self.verbose:
            log(self.redact(repr(e)))

    def finished(self, job: Job) -> None:
        with self._jobs_lock:
            if self.jobs.get(job.id) is job:
                del self.jobs[job.id]

    def stop(self) -> None:
        self.stop_event.set()

    def shutdown(self) -> None:
        """Stop now: end every running question (their AI tools are killed), then the loop."""
        self.stop_event.set()
        with self._jobs_lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            job.stop("shutdown")

    def join_jobs(self, timeout: float = 10) -> None:
        with self._jobs_lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            job.join(timeout=timeout)

    def _poll_body(self) -> dict:
        self.providers = build_providers(self.allow, self.providers)  # a tool installed meanwhile shows up
        with self._jobs_lock:
            running = list(self.jobs)
        return {
            "client": self.client,
            "name": self.name,
            "providers": [{"id": p.id, "label": p.label} for p in self.providers.values()],
            "running": running,
        }

    def _accept(self, spec: dict) -> None:
        """Start the question the server handed over, within this program's own limit: whatever the
        server sends, at most MAX_RUNNING AI tools run, and a job id never runs twice."""
        job_id = spec["id"]
        with self._jobs_lock:
            if job_id in self.jobs:
                return
            if len(self.jobs) >= MAX_RUNNING:
                busy = True
            else:
                busy = False
                job = Job(self, spec)
                self.jobs[job_id] = job
        if busy:
            log(f"job {clean(job_id)[:8]} refused: already running {MAX_RUNNING} questions")
            try:
                self.server.call(
                    "POST",
                    f"/api/companion/jobs/{urllib.parse.quote(job_id, safe='')}/events",
                    {
                        "client": self.client,
                        "events": [{"type": "error", "message": "The companion is busy with other questions."}],
                    },
                )
            except (ApiError, NetworkError):
                pass
            return
        job.start()

    def run(self, *, sync: Callable[[], None] | None = None) -> None:
        delay = 0.0
        next_sync = 0.0
        while not self.stop_event.is_set():
            if sync and time.monotonic() >= next_sync:
                next_sync = time.monotonic() + SYNC_EVERY
                try:
                    sync()
                except ExtensionReview as e:
                    log(f"An extension update changes {', '.join(e.keys)}; run `extension` to review and apply it.")
                except Exception as e:  # an update problem never ends the companion
                    log(f"extension update skipped: {e}")
            try:
                reply = self.server.call("POST", "/api/companion/poll", self._poll_body(), timeout=POLL_TIMEOUT)
                delay = 0.0
            except ApiError as e:
                if e.status == 401:
                    raise Fail(
                        "The server rejected the token. Make a new companion token and run connect again."
                    ) from None
                delay = min(max(delay * 2, 2), 30)
                log(f"server answered: {e} (trying again in {delay:.0f}s)")
                self.stop_event.wait(delay)
                continue
            except NetworkError as e:
                delay = min(max(delay * 2, 2), 30)
                log(f"cannot reach the server: {e} (trying again in {delay:.0f}s)")
                self.stop_event.wait(delay)
                continue
            cancelled = reply.get("cancelled")
            for job_id in cancelled if isinstance(cancelled, list) else []:
                with self._jobs_lock:
                    job = self.jobs.get(job_id) if isinstance(job_id, str) else None
                if job:
                    job.stop()
            spec = reply.get("job")
            if isinstance(spec, dict) and isinstance(spec.get("id"), str):
                self._accept(spec)
        self.shutdown()
        self.join_jobs()


# ---------------------------------------------------------------------- commands


def cmd_connect(a) -> None:
    base = check_url(a.url)
    token = a.token or os.environ.get(ENV_TOKEN) or getpass.getpass("Companion token (Settings → AI Access): ").strip()
    if not token:
        raise Fail("A token is needed.")
    server = Server(base, token)
    try:
        hello = server.hello()
    except ApiError as e:
        if e.status == 401:
            raise Fail("The server did not accept this token. It must be a companion token.") from None
        raise Fail(f"The server answered: {e}") from None
    except NetworkError as e:
        raise Fail(f"Cannot reach {base}: {e}") from None
    if hello.get("app") != "synapse":
        raise Fail("That address does not answer like Synapse.")
    try:
        previous = load_profile(a.profile)
    except Fail:
        previous = {}
    profile = {
        "server": base,
        "token": token,
        "client": previous.get("client") or uuid.uuid4().hex,
        "name": (a.name or previous.get("name") or socket.gethostname() or "computer")[:60],
        "extension": previous.get("extension"),
    }
    save_profile(a.profile, profile)
    print(f"Connected to {base} as '{profile['name']}'. Start the companion with:  python {Path(__file__).name} run")


CODEX_WARNING = (
    "Codex is on: it can read files on this computer, so text inside a paper can make it read local files and "
    "put them in a note on the server, and the MCP servers in ~/.codex/config.toml stay enabled."
)


def cmd_run(a) -> None:
    profile = load_profile(a.profile)
    allow = set(a.allow or [])
    companion = Companion(profile, a.name or profile.get("name") or "computer", allow, profile_name=a.profile)
    server = companion.server
    try:
        hello = server.hello()
    except ApiError as e:
        raise Fail(f"The server answered: {e}") from None
    except NetworkError as e:
        raise Fail(f"Cannot reach {server.base}: {e}") from None
    if hello["script"] not in ("", own_build()):
        log("A newer companion is available; update it with:  python synapse_companion.py update")
    names = ", ".join(p.label for p in companion.providers.values()) or "none found"
    log(f"Connected to {server.base}. AI tools: {names}. Press Ctrl+C to stop.")
    if "claude" not in companion.providers:
        log("Claude Code ('claude') was not found on PATH; questions will wait until it is installed.")
    if "codex" in companion.providers:
        log(CODEX_WARNING)
    ext = profile.get("extension")
    sync = None
    if ext and ext.get("dir"):

        def sync() -> None:
            if sync_extension(server, Path(ext["dir"])):
                log("The browser extension folder was updated.")

    def terminate(*_):
        # Stop the AI tools at once (a supervisor kills this program soon after SIGTERM), and break out of
        # the poll that may be waiting.
        companion.shutdown()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        companion.run(sync=sync)
    except KeyboardInterrupt:
        companion.shutdown()
        companion.join_jobs()
    print("Stopped.")


def cmd_extension(a) -> None:
    profile = load_profile(a.profile)
    server = Server(profile["server"], profile["token"])
    folder = Path(a.dir).expanduser().resolve() if a.dir else home() / f"{a.profile}-extension"
    ext = profile.get("extension") or {}
    first = not ext.get("dir") or Path(str(ext["dir"])) != folder

    def review(keys: list[str]) -> bool:
        print(f"This update changes what the extension may do: {', '.join(keys)}.")
        if a.yes:
            return True
        try:
            return input("Apply it? [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            return False

    try:
        changed = sync_extension(server, folder, force=first, review=review)
    except ExtensionReview:
        raise Fail("The update was not applied.") from None
    except ApiError as e:
        raise Fail(f"The server answered: {e}") from None
    except NetworkError as e:
        raise Fail(f"Cannot reach {server.base}: {e}") from None
    profile["extension"] = {"dir": str(folder)}
    save_profile(a.profile, profile)
    print(f"Extension folder {'updated' if changed else 'is up to date'}: {folder}")
    if first:
        print(
            "Install it once in Chrome (or another Chromium browser):\n"
            "  1. Open chrome://extensions and turn on Developer mode.\n"
            f"  2. Click Load unpacked and choose {folder}\n"
            f"  3. Keep `python {Path(__file__).name} run` running: it keeps this folder up to date and the "
            "extension reloads itself."
        )


def cmd_status(a) -> None:
    profile = load_profile(a.profile)
    server = Server(profile["server"], profile["token"])
    print(f"Profile {a.profile}: {profile['server']} as '{profile['name']}'")
    try:
        hello = server.hello()
    except ApiError as e:
        print(f"Server: refused ({e})")
        return
    except NetworkError as e:
        print(f"Server: unreachable ({e})")
        return
    print(f"Server: reachable, version {hello['version']}")
    print(
        f"Companion: {'up to date' if hello['script'] in ('', own_build()) else 'a newer version exists (run update)'}"
    )
    ext = (profile.get("extension") or {}).get("dir")
    if ext:
        state = "up to date" if folder_build(Path(ext)) == hello["extension"] else "out of date or edited"
        print(f"Extension: {ext} ({state})")
    else:
        print("Extension: not installed (run extension)")
    for tool in ("claude", "codex"):
        print(f"{tool}: {shutil.which(tool) or 'not found'}")


def cmd_update(a) -> None:
    profile = load_profile(a.profile)
    server = Server(profile["server"], profile["token"])
    try:
        data = server.call("GET", "/api/companion/script", raw=True)
    except ApiError as e:
        raise Fail(f"The server answered: {e}") from None
    except NetworkError as e:
        raise Fail(f"Cannot reach {server.base}: {e}") from None
    try:
        compile(data, "synapse_companion.py", "exec")
    except (SyntaxError, ValueError):
        raise Fail("The downloaded script is not valid Python; nothing was changed.") from None
    if b"Synapse Companion" not in data:
        raise Fail("The download is not the Synapse companion; nothing was changed.")
    target = Path(__file__).resolve()
    if hashlib.sha256(data).hexdigest()[:16] == own_build():
        print("Already up to date.")
        return
    tmp = target.with_name(target.name + ".new")
    tmp.write_bytes(data)
    shutil.copymode(target, tmp)
    os.replace(tmp, target)
    print(f"Updated {target}. Restart the companion if it is running.")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="synapse_companion.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", default="default", help="which server's connection to use (default: default)")
    c = sub.add_parser("connect", parents=[common], help="connect to a Synapse server")
    c.add_argument("url", help="the server's address, such as https://synapse.example.com")
    c.add_argument("--token", help=f"companion token (else ${ENV_TOKEN} or asked for)")
    c.add_argument("--name", help="how this computer is shown in Synapse")
    c.set_defaults(fn=cmd_connect)
    r = sub.add_parser("run", parents=[common], help="answer questions from Synapse")
    r.add_argument("--name", help="how this computer is shown in Synapse")
    r.add_argument(
        "--allow",
        action="append",
        choices=["codex"],
        help="also offer Codex. Unlike Claude Code here it has a shell tool that can read files on this computer: "
        "text inside a paper can make it read local files and write them into a note on the server, and the "
        "MCP servers in ~/.codex/config.toml stay enabled",
    )
    r.set_defaults(fn=cmd_run)
    e = sub.add_parser("extension", parents=[common], help="download or refresh the browser extension folder")
    e.add_argument("--dir", help="where to put the extension")
    e.add_argument("--yes", action="store_true", help="apply an update that changes the extension's permissions")
    e.set_defaults(fn=cmd_extension)
    sub.add_parser("status", parents=[common], help="show the connection").set_defaults(fn=cmd_status)
    sub.add_parser("update", parents=[common], help="replace this script with the server's").set_defaults(fn=cmd_update)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.fn(args)
    except Fail as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
