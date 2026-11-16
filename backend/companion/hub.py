"""In-memory rendezvous between the web app and companions on the user's own computers.

A companion (``synapse_companion.py``) long-polls for questions; the app asks and follows the
answer with its own long-polls. Everything lives in memory, per ``Services``: nothing here is
saved, and a restart of the server ends open conversations, as ``BrowserPdfs`` does for its
handoffs. The hub depends on nothing else in Synapse.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

POLL_SECONDS = 20.0
WATCH_SECONDS = 20.0
MAX_COMPANIONS = 16
MAX_PER_TOKEN = 4  # one token cannot fill every slot
MAX_JOB_SECONDS = 900.0  # a question never lives longer, however its companion behaves
GRACE_SECONDS = 30.0  # a companion counts as connected this long after its last poll
PICKUP_SECONDS = 15.0  # a question nobody takes ends with an error
SILENCE_SECONDS = 60.0  # a running question whose companion went quiet ends with an error
KEEP_SECONDS = 600.0  # finished jobs stay readable this long
MAX_FINISHED = 100
MAX_ACTIVE = 32
MAX_RUNNING_EACH = 2  # jobs handed to one companion at once
MAX_CONVERSATIONS = 200
CONVERSATION_SECONDS = 24 * 3600.0

MAX_BATCH_EVENTS = 64
MAX_BATCH_BYTES = 256 * 1024
MAX_JOB_TEXT = 2 * 1024 * 1024
MAX_JOB_EVENTS = 20000
MAX_NAME = 60
MAX_PROVIDERS = 8

CLIENT = re.compile(r"[A-Za-z0-9_-]{1,128}")
PROVIDER = re.compile(r"[a-z0-9_-]{1,32}")
FINISHED = ("done", "error", "cancelled")


class InvalidCompanion(ValueError):
    """The companion's identity or capabilities are malformed."""


class CompanionLimit(ValueError):
    """All companion slots are occupied."""


class NoCompanion(ValueError):
    """No connected companion can take the question."""


class TooBusy(ValueError):
    """Too many questions are open, or the conversation is still answering."""


class InvalidEvents(ValueError):
    """The events are malformed or larger than allowed."""


class UnknownJob(LookupError):
    """No such job (never existed, or kept long enough)."""


class WrongCompanion(PermissionError):
    """The job, or the companion ID, belongs to another companion or token."""


@dataclass
class _Companion:
    id: str
    name: str = ""
    providers: list[dict] = field(default_factory=list)
    last_seen: float = 0.0
    polling: int = 0  # open long-polls
    token: str = ""  # the access token it connected with; its client id is bound to it


@dataclass
class _Job:
    id: str
    companion: str
    provider: str
    prompt: str
    system: str
    conversation: str
    resume: bool
    created: float
    state: str = "queued"
    events: list[dict] = field(default_factory=list)
    text_bytes: int = 0
    activity: float = 0.0
    finished: float = 0.0
    stop: bool = False  # the companion must stop it (cancelled, or ended here)
    stop_sent: bool = False


def _clean_providers(providers) -> list[dict]:
    if not isinstance(providers, list) or len(providers) > MAX_PROVIDERS:
        raise InvalidCompanion("Invalid provider list.")
    out = []
    for p in providers:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not PROVIDER.fullmatch(p["id"]):
            raise InvalidCompanion("Invalid provider.")
        label = p.get("label")
        out.append({"id": p["id"], "label": (label if isinstance(label, str) else p["id"]).strip()[:MAX_NAME]})
    return out


class Companions:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._condition = threading.Condition()
        self._companions: dict[str, _Companion] = {}
        self._jobs: dict[str, _Job] = {}
        self._conversations: dict[str, tuple[str, str, float]] = {}  # id -> (companion, provider, last used)

    # ------------------------------------------------------------------ companion side
    def poll(
        self,
        client: Any,
        name: Any,
        providers: Any,
        running: Any,
        *,
        token: str = "",
        timeout: float | None = None,
    ) -> dict:
        """Wait for a question for this companion. Answers ``{"job": …|None, "cancelled": [ids]}``."""
        if not isinstance(client, str) or not CLIENT.fullmatch(client):
            raise InvalidCompanion("Invalid companion ID.")
        providers = _clean_providers(providers)
        name = (name if isinstance(name, str) else "").strip()[:MAX_NAME] or client[:8]
        if not isinstance(running, list) or not all(isinstance(i, str) for i in running) or len(running) > 16:
            raise InvalidCompanion("Invalid list of running jobs.")
        with self._condition:
            self._sweep()
            comp = self._companions.get(client)
            if comp is None:
                if len(self._companions) >= MAX_COMPANIONS:
                    raise CompanionLimit("All companion slots are occupied.")
                if sum(c.token == token for c in self._companions.values()) >= MAX_PER_TOKEN:
                    raise CompanionLimit("This token already has as many companions as it may.")
                comp = self._companions[client] = _Companion(client, token=token)
            elif comp.token != token:
                raise WrongCompanion("This companion ID belongs to another token.")
            comp.name, comp.providers = name, providers
            comp.last_seen = self._clock()
            comp.polling += 1
            self._condition.notify_all()
            deadline = comp.last_seen + (POLL_SECONDS if timeout is None else timeout)
            try:
                while True:
                    self._sweep()
                    stopped = self._stopped(client, running)
                    job = self._next_job(client)
                    if job is not None:
                        return {"job": self._handout(job), "cancelled": stopped}
                    if any(not self._jobs[i].stop_sent for i in stopped):
                        for i in stopped:
                            self._jobs[i].stop_sent = True
                        return {"job": None, "cancelled": stopped}
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return {"job": None, "cancelled": stopped}
                    self._condition.wait(min(remaining, 1.0))
            finally:
                comp.polling -= 1
                comp.last_seen = self._clock()
                self._condition.notify_all()

    def _stopped(self, client: str, running: list[str]) -> list[str]:
        return [i for i in running if (j := self._jobs.get(i)) and j.companion == client and j.stop]

    def _running_on(self, client: str) -> int:
        return sum(j.companion == client and j.state == "running" for j in self._jobs.values())

    def _next_job(self, client: str) -> _Job | None:
        mine = [j for j in self._jobs.values() if j.companion == client]
        if self._running_on(client) >= MAX_RUNNING_EACH:
            return None
        return next((j for j in mine if j.state == "queued"), None)

    def _handout(self, job: _Job) -> dict:
        job.state = "running"
        job.activity = self._clock()
        self._condition.notify_all()
        return {
            "id": job.id,
            "provider": job.provider,
            "prompt": job.prompt,
            "system": job.system,
            "conversation": job.conversation,
            "resume": job.resume,
        }

    def post_events(self, client: Any, job_id: str, events: Any, *, token: str = "") -> bool:
        """Add events from the companion that has the job. Answers whether it must stop the job."""
        if not isinstance(events, list) or len(events) > MAX_BATCH_EVENTS:
            raise InvalidEvents("Too many events in one request.")
        clean: list[dict] = []
        size = 0
        for e in events:
            if not isinstance(e, dict):
                continue
            kind = e.get("type")
            if kind == "text" and isinstance(e.get("text"), str):
                size += len(e["text"])
                if clean and clean[-1]["type"] == "text":
                    clean[-1]["text"] += e["text"]
                else:
                    clean.append({"type": "text", "text": e["text"]})
            elif kind == "tool" and isinstance(e.get("name"), str):
                size += len(e["name"])
                clean.append({"type": "tool", "name": e["name"][:100]})
            elif kind == "done":
                clean.append({"type": "done"})
            elif kind == "error":
                message: str = e["message"] if isinstance(e.get("message"), str) else ""
                size += len(message)
                clean.append({"type": "error", "message": message[:1000] or "The assistant failed."})
        if size > MAX_BATCH_BYTES:
            raise InvalidEvents("The events are too large.")
        with self._condition:
            self._sweep()
            job = self._jobs.get(job_id)
            if job is None:
                raise UnknownJob(job_id)
            owner = self._companions.get(job.companion)
            if job.companion != client or (owner is not None and owner.token != token):
                raise WrongCompanion(job_id)
            now = self._clock()
            job.activity = now
            if job.state in FINISHED:
                return job.stop
            for e in clean:
                if e["type"] == "text":
                    job.text_bytes += len(e["text"].encode("utf-8"))
                job.events.append(e)
                if e["type"] in ("done", "error"):
                    self._finish(job, e["type"])
                    break
                if job.text_bytes > MAX_JOB_TEXT or len(job.events) > MAX_JOB_EVENTS:
                    self._end(job, "The answer became too long and was stopped.")
                    break
            self._condition.notify_all()
            return job.stop

    # ------------------------------------------------------------------ app side
    def status(self) -> list[dict]:
        with self._condition:
            self._sweep()
            return [
                {
                    "id": c.id,
                    "name": c.name,
                    "providers": [dict(p) for p in c.providers],
                    "busy": sum(j.companion == c.id and j.state not in FINISHED for j in self._jobs.values()),
                }
                for c in self._connected()
            ]

    def ask(
        self,
        prompt: str,
        system: str,
        *,
        provider: str | None = None,
        conversation: str | None = None,
        companion: str | None = None,
    ) -> dict:
        """Queue a question for one connected companion. Answers the job and its conversation."""
        with self._condition:
            self._sweep()
            if sum(j.state not in FINISHED for j in self._jobs.values()) >= MAX_ACTIVE:
                raise TooBusy("Too many questions are open. Try again in a moment.")
            resume = False
            if conversation:
                pinned = self._conversations.get(conversation)
                comp = self._companions.get(pinned[0]) if pinned else None
                if pinned is None or comp is None or comp not in self._connected():
                    raise NoCompanion("The companion of this conversation is not connected. Start a new conversation.")
                if any(j.conversation == conversation and j.state not in FINISHED for j in self._jobs.values()):
                    raise TooBusy("Wait for the answer before asking again.")
                provider, resume = pinned[1], True
            else:
                comp, provider = self._choose(provider, companion)
                conversation = uuid.uuid4().hex
            now = self._clock()
            self._conversations[conversation] = (comp.id, provider, now)
            while len(self._conversations) > MAX_CONVERSATIONS:
                self._conversations.pop(min(self._conversations, key=lambda k: self._conversations[k][2]))
            job = _Job(uuid.uuid4().hex, comp.id, provider, prompt, system, conversation, resume, now, activity=now)
            self._jobs[job.id] = job
            self._condition.notify_all()
            return {
                "job": job.id,
                "conversation": conversation,
                "provider": provider,
                "companion": {"id": comp.id, "name": comp.name},
            }

    def _choose(self, provider: str | None, companion: str | None) -> tuple[_Companion, str]:
        candidates = [c for c in self._connected() if companion in (None, "", c.id)]
        offered = [p["id"] for c in candidates for p in c.providers]
        if not provider:
            provider = "claude" if "claude" in offered else (offered[0] if offered else None)
        candidates = [c for c in candidates if any(p["id"] == provider for p in c.providers)]
        if not candidates or provider is None:
            raise NoCompanion("No companion is connected" + (f" that offers {provider}." if provider else "."))
        busy = {
            c.id: sum(j.companion == c.id and j.state not in FINISHED for j in self._jobs.values()) for c in candidates
        }
        return min(candidates, key=lambda c: (busy[c.id], -c.last_seen)), provider

    def watch(self, job_id: str, after: int = 0, *, timeout: float | None = None) -> dict:
        """Events from index ``after`` on, waiting for new ones (or the end) up to ``timeout``."""
        after = max(0, after)
        with self._condition:
            deadline = self._clock() + (WATCH_SECONDS if timeout is None else timeout)
            while True:
                self._sweep()
                job = self._jobs.get(job_id)
                if job is None:
                    raise UnknownJob(job_id)
                if len(job.events) > after or job.state in FINISHED or self._clock() >= deadline:
                    return {"events": job.events[after:], "next": len(job.events), "state": job.state}
                self._condition.wait(min(deadline - self._clock(), 1.0))

    def cancel(self, job_id: str) -> str:
        with self._condition:
            self._sweep()
            job = self._jobs.get(job_id)
            if job is None:
                raise UnknownJob(job_id)
            if job.state not in FINISHED:
                job.stop = True
                self._finish(job, "cancelled")
                self._condition.notify_all()
            return job.state

    # ------------------------------------------------------------------ bookkeeping (lock held)
    def _connected(self) -> list[_Companion]:
        now = self._clock()
        return [c for c in self._companions.values() if c.polling or now - c.last_seen <= GRACE_SECONDS]

    def _finish(self, job: _Job, state: str) -> None:
        job.state = state
        job.finished = self._clock()

    def _end(self, job: _Job, message: str) -> None:
        """End a job from this side; the companion is told to stop it."""
        job.events.append({"type": "error", "message": message})
        job.stop = True
        self._finish(job, "error")

    def _sweep(self) -> None:
        now = self._clock()
        for job in list(self._jobs.values()):
            if job.state not in FINISHED and now - job.created > MAX_JOB_SECONDS:
                self._end(job, "The question took too long and was stopped.")
            elif job.state == "queued" and now - job.created > PICKUP_SECONDS:
                comp = self._companions.get(job.companion)
                if comp and comp in self._connected() and self._running_on(comp.id) >= MAX_RUNNING_EACH:
                    self._end(job, "The companion is busy with other questions.")
                else:
                    self._end(job, "No companion picked up the question. Is the companion still running?")
            elif job.state == "running":
                comp = self._companions.get(job.companion)
                seen = max(job.activity, comp.last_seen if comp else 0.0)
                if not (comp and comp.polling) and now - seen > SILENCE_SECONDS:
                    self._end(job, "The companion stopped responding.")
            elif job.state in FINISHED and now - job.finished > KEEP_SECONDS:
                del self._jobs[job.id]
        done = sorted((j for j in self._jobs.values() if j.state in FINISHED), key=lambda j: j.finished)
        for job in done[: max(0, len(done) - MAX_FINISHED)]:
            del self._jobs[job.id]
        for cid, comp in list(self._companions.items()):
            if (
                not comp.polling
                and now - comp.last_seen > GRACE_SECONDS
                and not any(j.companion == cid and j.state not in FINISHED for j in self._jobs.values())
            ):
                del self._companions[cid]
        for key, (_, _, used) in list(self._conversations.items()):
            if now - used > CONVERSATION_SECONDS:
                del self._conversations[key]
