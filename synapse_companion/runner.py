"""Answering questions: the loop that long-polls the server for them, and one thread per question
that runs the AI tool and sends its events back."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .common import MAX_RUNNING, Fail, clean, home, log, normal_settings
from .providers import NO_HISTORY, Abort, Stream, build_providers
from .remote import ApiError, NetworkError, Server

POLL_TIMEOUT = 35  # the server answers a poll within 20 s
FLUSH_EVERY = 0.15


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
        for key in ("prompt", "system", "conversation", "mcp_token"):
            if not isinstance(spec.get(key), str) or len(spec[key]) > 200_000:
                raise Abort("The server sent an invalid question (is it older than this companion?).")
        token = spec["mcp_token"]  # this question's own /mcp token; the companion's never reaches the tool
        c.secrets.add(token)
        spec["resume"] = bool(spec.get("resume"))
        if not provider.can_resume(spec):
            raise Abort(NO_HISTORY)
        setting = c.settings["providers"].get(provider.id)
        model, effort = provider.choice(spec, "model", setting), provider.choice(spec, "effort", setting)
        config = _private_file("mcp-", ".json", json.dumps(mcp_config(c.server.base + "/mcp", token)), c.work_root)
        system_file = _private_file("system-", ".txt", spec["system"], c.work_root)
        # Another server (or profile) must not resume this conversation's session, nor the reverse.
        session = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{c.server.base}|{spec['conversation']}"))
        try:
            log(f"question {self.id[:8]} started ({provider.label})")
            self._exec(
                provider.command(
                    spec,
                    c.server.base + "/mcp",
                    config=config,
                    system_file=system_file,
                    session=session,
                    model=model,
                    effort=effort,
                ),
                provider.env(token),
                provider.stdin(spec),
                provider.stream(spec, sink.push),
                sink,
            )
        finally:
            config.unlink(missing_ok=True)
            system_file.unlink(missing_ok=True)
            c.secrets.discard(token)
        log(f"question {self.id[:8]} answered")

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
        sink.push({"type": "started"})  # the app shows the work has begun
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
    """Answers the questions of one server. ``state`` says how the connection is going, for the
    window: connecting, connected, unreachable or refused (the token was removed)."""

    def __init__(
        self,
        profile: dict,
        name: str,
        *,
        home_dir: Path | None = None,
        profile_name: str = "default",
    ):
        self.server = Server(profile["server"], profile["token"])
        self.client = profile["client"]
        self.name = name
        self.settings = normal_settings(profile.get("settings"))
        self.profile = profile
        self.state, self.state_message = "connecting", ""
        self.secrets: set[str] = set()  # per-question tokens, kept out of messages
        self.work_root = home_dir or home()
        self.work_dir = self.work_root / f"work-{profile_name}"  # empty: the AI tool finds no files here
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(mode=0o700, exist_ok=True)
        self.providers = build_providers(self.enabled())
        self.jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.verbose = bool(os.environ.get("SYNAPSE_COMPANION_DEBUG"))

    def enabled(self) -> set[str]:
        return {pid for pid, p in self.settings["providers"].items() if p["enabled"]}

    def update_settings(self, settings: dict) -> None:
        self.settings = normal_settings(settings)

    def running(self) -> list[dict]:
        with self._jobs_lock:
            return [{"id": j.id[:8], "provider": str(j.spec.get("provider"))} for j in self.jobs.values()]

    def redact(self, text: str) -> str:
        for secret in [self.server.token, *self.secrets]:
            text = text.replace(secret, "***")
        return text

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
        self.providers = build_providers(self.enabled(), self.providers)  # a tool installed meanwhile shows up
        with self._jobs_lock:
            running = list(self.jobs)
        return {
            "client": self.client,
            "name": self.name,
            "providers": [p.advert(self.settings["providers"].get(p.id)) for p in self.providers.values()],
            "running": running,
        }

    def _accept(self, spec: dict) -> None:
        """Start the question the server handed over, within this program's own limit: whatever the
        server sends, at most MAX_RUNNING AI tools run, and a job id never runs twice."""
        job_id = spec["id"]
        with self._jobs_lock:
            if job_id in self.jobs:
                return
            if len(self.jobs) >= min(MAX_RUNNING, self.settings["max_running"]):
                busy = True
            else:
                busy = False
                job = Job(self, spec)
                self.jobs[job_id] = job
        if busy:
            log(f"question {clean(job_id)[:8]} refused: already answering {len(self.jobs)}")
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

    def run(self) -> None:
        """Poll until stopped. A refused token ends the loop with ``Fail``."""
        delay = 0.0
        while not self.stop_event.is_set():
            try:
                if self.state != "connected":
                    self.server.hello()  # a poll waits up to 20 s: say "connected" as soon as the server answers
                    log(f"Connected to {self.server.base}.")
                    self.state, self.state_message = "connected", ""
                reply = self.server.call("POST", "/api/companion/poll", self._poll_body(), timeout=POLL_TIMEOUT)
                delay = 0.0
            except ApiError as e:
                if e.status == 401:
                    self.state, self.state_message = "refused", "The server no longer accepts this token."
                    self.shutdown()
                    self.join_jobs()
                    raise Fail("The server rejected the token. Make a new companion token and connect again.") from None
                delay = min(max(delay * 2, 2), 30)
                self.state, self.state_message = "unreachable", f"The server answered: {e}"
                log(f"server answered: {e} (trying again in {delay:.0f}s)")
                self.stop_event.wait(delay)
                continue
            except NetworkError as e:
                delay = min(max(delay * 2, 2), 30)
                self.state, self.state_message = "unreachable", f"Cannot reach the server: {e}"
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
