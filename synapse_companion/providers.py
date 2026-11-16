"""The AI command-line tools the companion runs, restricted to Synapse's tools: how to start each
one, and how to read its output into the events the server expects."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from .common import CHILD_TOKEN, ENV_TOKEN

TOOL_PREFIX = "mcp__synapse__"


class Abort(Exception):
    """Stop the job with this message for the user."""


NO_HISTORY = "This conversation's history is no longer on that computer. Start a new one."
CHOICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,63}")  # never starts like a flag


def _count(value: object, limit: float = 10**9) -> float | None:
    """A non-negative number the AI tool reported, or None; usage is numbers only."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= limit:
        return None
    return value


class Provider:
    """How to run one AI tool restricted to Synapse's tools. Subclasses build the command and read
    the tool's output stream into events.

    ``models`` and ``efforts`` are built in, the only values a question may select: the server's
    choice is looked up here and never copied into the command line."""

    id = ""
    label = ""
    exe = ""
    models: tuple[tuple[str, str], ...] = ()  # (id, label)
    efforts: tuple[str, ...] = ()

    def __init__(self, exe: str):
        self.exe = exe

    def offered(self, setting: dict | None, key: str) -> list[str]:
        """The models or efforts offered: the built-in ones the user's settings keep (None: all)."""
        built_in = [i for i, _ in self.models] if key == "models" else list(self.efforts)
        chosen = (setting or {}).get(key)
        return built_in if chosen is None else [i for i in built_in if i in chosen]

    def advert(self, setting: dict | None = None) -> dict:
        models = self.offered(setting, "models")
        return {
            "id": self.id,
            "label": self.label,
            "models": [{"id": i, "label": label} for i, label in self.models if i in models],
            "efforts": self.offered(setting, "efforts"),
        }

    def choice(self, job: dict, key: str, setting: dict | None = None) -> str | None:
        """The model or effort the question asked for if this provider offers it, else None (default)."""
        value = job.get(key)
        allowed = self.offered(setting, "models" if key == "model" else "efforts")
        return value if isinstance(value, str) and CHOICE.fullmatch(value) and value in allowed else None

    def command(
        self,
        job: dict,
        mcp_url: str,
        *,
        config: Path,
        system_file: Path,
        session: str,
        model: str | None = None,
        effort: str | None = None,
    ) -> list[str]:
        """The argument list. Nothing the server sent may be in it (on Windows a ``.cmd`` tool is run
        through cmd.exe): the system text goes in ``system_file``, the session id is derived here, and
        ``model`` and ``effort`` are values from this provider's own lists."""
        raise NotImplementedError

    def can_resume(self, job: dict) -> bool:
        return True

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
        if kind == "result" and e.get("is_error"):
            # also what a resume of a session that is gone answers, before any init event
            self.finished = True
            errors = [str(x) for x in e["errors"]] if isinstance(e.get("errors"), list) else []
            text = e.get("result") if isinstance(e.get("result"), str) else ""
            if any("No conversation found" in x for x in errors):
                self.emit({"type": "error", "message": NO_HISTORY})
            else:
                self.emit({"type": "error", "message": text or "; ".join(errors) or "Claude Code failed."})
            return
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
            if not self.streamed and text:
                self.emit({"type": "text", "text": text})
            self.usage(e)
            self.emit({"type": "done"})

    def usage(self, result: dict) -> None:
        """Tokens, cost and time from the result event. Input counts everything sent to the model,
        including what came from its cache."""
        usage: dict = result["usage"] if isinstance(result.get("usage"), dict) else {}
        parts = [
            _count(usage.get(k)) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        ]
        out = _count(usage.get("output_tokens"))
        if out is None:
            return
        event: dict = {"type": "usage", "input_tokens": int(sum(p or 0 for p in parts)), "output_tokens": int(out)}
        if (cost := _count(result.get("total_cost_usd"), 10**6)) is not None:
            event["cost_usd"] = float(cost)
        if (ms := _count(result.get("duration_ms"))) is not None:
            event["duration_ms"] = int(ms)
        self.emit(event)


class Claude(Provider):
    id, label = "claude", "Claude Code"
    # The aliases and --effort levels Claude Code 2.1.x accepts (checked against the installed CLI)
    models = (("fable", "Fable"), ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"))
    efforts = ("low", "medium", "high", "xhigh", "max")

    def command(
        self,
        job: dict,
        mcp_url: str,
        *,
        config: Path,
        system_file: Path,
        session: str,
        model: str | None = None,
        effort: str | None = None,
    ) -> list[str]:
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
            *(["--model", model] if model else []),
            *(["--effort", effort] if effort else []),
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
            usage: dict = e["usage"] if isinstance(e.get("usage"), dict) else {}
            inp, out = _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
            if inp is not None and out is not None:
                self.emit({"type": "usage", "input_tokens": int(inp), "output_tokens": int(out)})
            self.emit({"type": "done"})
        elif kind in ("turn.failed", "error"):
            self.finished = True
            err: dict = e["error"] if isinstance(e.get("error"), dict) else {}
            self.emit({"type": "error", "message": str(err.get("message") or e.get("message") or "Codex failed.")})


class Codex(Provider):
    id, label = "codex", "Codex"
    # No model list: the models differ by account and release, so Codex keeps the user's own default.
    # The effort values are the ones its API accepts for every model.
    efforts = ("low", "medium", "high", "xhigh")

    def __init__(self, exe: str):
        super().__init__(exe)
        self.threads: dict[str, str] = {}  # conversation -> Codex thread, for follow-ups

    def can_resume(self, job: dict) -> bool:
        return not job["resume"] or job["conversation"] in self.threads

    def command(
        self,
        job: dict,
        mcp_url: str,
        *,
        config: Path,
        system_file: Path,
        session: str,
        model: str | None = None,
        effort: str | None = None,
    ) -> list[str]:
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
            *(["-m", model] if model else []),
            *(["-c", f"model_reasoning_effort={json.dumps(effort)}"] if effort else []),
        ]
        return cmd + ([thread, "-"] if thread else ["-"])

    def env(self, token: str) -> dict[str, str]:
        return {**super().env(token), CHILD_TOKEN: token}

    def stdin(self, job: dict) -> str:
        # Codex has no system-prompt option.
        return job["system"] + "\n\n" + job["prompt"]

    def stream(self, job: dict, emit: Callable[[dict], None]) -> Stream:
        return CodexStream(emit, lambda thread: self.threads.__setitem__(job["conversation"], thread))


PROVIDERS: tuple[type[Provider], ...] = (Claude, Codex)


def build_providers(enabled: set[str], known: dict[str, Provider] | None = None) -> dict[str, Provider]:
    """The enabled AI tools found on PATH now. Instances are kept while their executable stays the
    same, since Codex remembers its conversations' threads."""
    found: dict[str, Provider] = {}
    for cls in PROVIDERS:
        exe = shutil.which(cls.id) if cls.id in enabled else None
        if exe:
            old = (known or {}).get(cls.id)
            found[cls.id] = old if old is not None and old.exe == exe else cls(exe)
    return found
