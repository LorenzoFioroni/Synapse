"""The updater: installs a new Synapse when Synapse asks (Settings → Updates). See docs/updates.md.

It is the only container with access to Docker, so it decides everything itself and Synapse only
says "now":

* the source is its own setting (``SYNAPSE_SOURCE``, ``https://github.com/owner/repo.git#main``),
  never part of a request; it asks Git for the branch's newest commit and builds exactly that one
  (``SYNAPSE_BUILD_SOURCE=<repo>#<commit>``, ``SYNAPSE_COMMIT=<commit>`` for compose.yaml);
* it keeps the image that ran before (``<image>-previous``), restarts the Synapse services, waits
  for them to be healthy, and goes back to the previous image when they are not;
* every request needs its token (``UPDATER_TOKEN``), and it listens only on the network it shares
  with Synapse.

Standard library only: it runs in the official Docker CLI image with Python added.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SOURCE_RE = re.compile(r"(https://[^#\s]+?)(?:#([^:\s]+))?")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
LOG_LINES = 40
COMMAND_TIMEOUT = 1800  # seconds: building from scratch downloads the base images
# docker inspect --format: the value of SYNAPSE_COMMIT and nothing else of the environment
COMMIT_FORMAT = (
    '{{range .Config.Env}}{{if eq (index (split . "=") 0) "SYNAPSE_COMMIT"}}{{index (split . "=") 1}}{{end}}{{end}}'
)
# A user name and password (a token) in an address, as a private repository's SYNAPSE_SOURCE has.
USERINFO_RE = re.compile(r"(?i)\b(https?://)[^/@\s]+@")
ROLLBACK_WAIT = 120.0  # seconds the previous version gets to be healthy again, whatever UPDATER_HEALTH_TIMEOUT says


class UpdaterError(Exception):
    pass


@dataclass
class Settings:
    token: str
    repo: str
    branch: str
    project_dir: Path
    services: list[str]
    image: str
    health_timeout: float = 300.0

    @classmethod
    def from_env(cls, env: dict[str, str]) -> Settings:
        token = env.get("UPDATER_TOKEN", "")
        if len(token) < 16:
            raise UpdaterError("UPDATER_TOKEN must be set to a random value of at least 16 characters.")
        m = SOURCE_RE.fullmatch(env.get("SYNAPSE_SOURCE", "").strip())
        if not m:
            raise UpdaterError(
                "SYNAPSE_SOURCE must be the Git repository to build, like https://github.com/owner/Synapse.git#main."
            )
        project = Path(env.get("UPDATER_PROJECT_DIR", ""))
        if not project.is_absolute() or not project.is_dir():
            raise UpdaterError(
                "UPDATER_PROJECT_DIR must be the absolute path of the folder with compose.yaml, mounted at the same path."
            )
        services = env.get("UPDATER_SERVICES", "synapse").split()
        return cls(
            token=token,
            repo=m.group(1),
            branch=m.group(2) or "main",
            project_dir=project,
            services=services,
            image=env.get("UPDATER_IMAGE", "synapse:local"),
            health_timeout=float(env.get("UPDATER_HEALTH_TIMEOUT", "300")),
        )


def redact(text: str) -> str:
    """``text`` without the credentials of any address in it: what Synapse is told goes to every
    signed-in device (Settings → Updates)."""
    return USERINFO_RE.sub(r"\1***@", text)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_command(cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    r = subprocess.run(
        cmd, cwd=cwd, env={**os.environ, **env}, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


@dataclass
class Updater:
    settings: Settings
    run: Callable[[list[str], Path, dict[str, str]], tuple[int, str]] = run_command
    sleep: Callable[[float], None] = time.sleep
    spawn: Callable[[Callable[[], None]], None] = lambda fn: threading.Thread(
        target=fn, name="update", daemon=True
    ).start()
    state: dict = field(default_factory=lambda: {"state": "idle", "message": "", "log": []})
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def previous(self) -> str:
        return f"{self.settings.image}-previous"

    def _cmd(self, *args: str, env: dict[str, str] | None = None, check: bool = True, private: bool = False) -> str:
        """Run a command; its output goes to the log Settings shows, unless ``private`` (anything
        that could print a container's environment, whose secrets signed-in devices must not see)."""
        code, out = self.run(list(args), self.settings.project_dir, env or {})
        lines = [line for line in out.splitlines() if line.strip()]
        shown = ["(output not shown)"] if private and lines else lines
        self.state["log"] = (self.state.get("log", []) + [f"$ {' '.join(args)}", *shown])[-LOG_LINES:]
        if check and code != 0:
            raise UpdaterError(f"{' '.join(args[:3])} failed: {lines[-1] if lines else f'exit {code}'}")
        return out if code == 0 else ""

    def _compose(self, *args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
        return self._cmd(
            "docker", "compose", "--project-directory", str(self.settings.project_dir), *args, env=env, check=check
        )

    # ------------------------------------------------------------------ what runs, what is new
    def target(self) -> str:
        out = self._cmd("git", "ls-remote", self.settings.repo, f"refs/heads/{self.settings.branch}")
        sha = out.split()[0] if out.split() else ""
        if not COMMIT_RE.fullmatch(sha):
            raise UpdaterError(f"The branch {self.settings.branch} was not found in {self.settings.repo}.")
        return sha

    def _containers(self, service: str) -> list[str]:
        return self._compose("ps", "-q", service, check=False).split()

    def check_project(self) -> None:
        """The Synapse containers were started from this folder (Compose's label says where). From
        any other path, Compose would mount data folders that do not exist on the host, and Docker
        would start Synapse on new, empty ones."""
        found = False
        for service in self.settings.services:
            for cid in self._containers(service):
                found = True
                where = self._cmd(
                    "docker",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
                    cid,
                ).strip()
                if where and Path(where) != self.settings.project_dir:
                    raise UpdaterError(
                        f"UPDATER_PROJECT_DIR is {self.settings.project_dir}, but {service} was started from {where}."
                    )
        if not found:
            raise UpdaterError(
                f"No container of {', '.join(self.settings.services)} runs in {self.settings.project_dir}."
            )

    def running_commit(self, service: str) -> str | None:
        """The commit the service's container was built from (its image's SYNAPSE_COMMIT). Docker is
        asked for that variable alone: the others hold secrets, such as this updater's token."""
        for cid in self._containers(service):
            out = self._cmd("docker", "inspect", "--format", COMMIT_FORMAT, cid, check=False, private=True)
            values = [w.split("=", 1)[1] if w.startswith("SYNAPSE_COMMIT=") else w for w in out.split()]
            found = [v for v in values if COMMIT_RE.fullmatch(v)]
            if found:
                return found[-1]
        return None

    # ------------------------------------------------------------------ updating
    def start(self) -> tuple[int, dict]:
        """(HTTP status, answer) for POST /update."""
        with self._lock:
            if self.state.get("state") == "running":
                return 409, {"error": "An update is being installed already.", **self.public()}
            self.state = {"state": "running", "message": "Looking up the newest code…", "started": now(), "log": []}
        try:
            self.check_project()
            sha = self.target()
            if all(self.running_commit(s) == sha for s in self.settings.services):
                self._finish(
                    "current", f"Synapse runs {sha[:7]}, the newest commit of {self.settings.branch}, already.", sha
                )
                return 200, self.public()
        except UpdaterError as e:
            self._finish("failed", str(e))
            return 502, {"error": str(e), **self.public()}
        self.state.update(commit=sha, message=f"Building {sha[:7]}…")
        self.spawn(lambda: self._update(sha))
        return 202, self.public()

    def _update(self, sha: str) -> None:
        env = {"SYNAPSE_BUILD_SOURCE": f"{self.settings.repo}#{sha}", "SYNAPSE_COMMIT": sha}
        services = self.settings.services
        kept = bool(self._cmd("docker", "image", "inspect", "--format", "{{.Id}}", self.settings.image, check=False))
        try:
            if kept:
                self._cmd("docker", "image", "tag", self.settings.image, self.previous)
            self._compose("build", *services, env=env)
        except UpdaterError as e:
            self._finish("failed", f"Building {sha[:7]} failed, so nothing was changed. {e}", sha)
            return
        try:
            self.state["message"] = f"Restarting Synapse with {sha[:7]}…"
            self._compose("up", "-d", "--no-deps", "--no-build", *services, env=env)
            if self.healthy(services):
                self._finish("done", f"Synapse {sha[:7]} is running.", sha)
                return
            problem = "The new version did not become healthy"
        except UpdaterError as e:
            problem = str(e)
        if not kept:
            self._finish("failed", f"{problem}, and there is no previous image to go back to.", sha)
            return
        self.state["message"] = "Going back to the previous version…"
        try:
            self._cmd("docker", "image", "tag", self.previous, self.settings.image)
            self._compose("up", "-d", "--no-deps", "--no-build", *services)
            back = self.healthy(services, at_least=ROLLBACK_WAIT)  # the version that ran before: give it time
        except UpdaterError:
            back = False
        self._finish(
            "failed",
            f"{problem}; "
            + ("the previous version runs again." if back else "going back to the previous version failed too."),
            sha,
        )

    def healthy(self, services: list[str], at_least: float = 0.0) -> bool:
        deadline = time.monotonic() + max(self.settings.health_timeout, at_least)
        while True:
            states = []
            for service in services:
                ids = self._containers(service)
                states += [
                    self._cmd(
                        "docker",
                        "inspect",
                        "--format",
                        "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                        cid,
                        check=False,
                    ).strip()
                    for cid in ids
                ] or ["missing"]
            if states and all(s in ("healthy", "running") for s in states):
                return True
            if time.monotonic() >= deadline:
                return False
            self.sleep(3)

    def _finish(self, state: str, message: str, commit: str | None = None) -> None:
        self.state.update(state=state, message=message, finished=now())
        if commit:
            self.state["commit"] = commit

    def public(self) -> dict:
        s = self.state
        out = {k: s.get(k) for k in ("state", "message", "commit", "started", "finished", "log")}
        if isinstance(out["message"], str):
            out["message"] = redact(out["message"])
        out["log"] = [redact(line) for line in out["log"] or []]
        return out | {"branch": self.settings.branch}


def handler(updater: Updater) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"{self.address_string()} {fmt % args}", flush=True)

        def _send(self, status: int, data: dict) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            given = self.headers.get("Authorization", "")
            expected = f"Bearer {updater.settings.token}"
            if hmac.compare_digest(given.encode(), expected.encode()):
                return True
            self._send(401, {"error": "The updater needs its token."})
            return False

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"ok": True})
            elif self.path == "/status":
                if self._authorized():
                    self._send(200, updater.public())
            else:
                self._send(404, {"error": "Not found."})

        def do_POST(self):
            if self.path != "/update":
                self._send(404, {"error": "Not found."})
            elif self._authorized():
                self._send(*updater.start())

    return Handler


def main() -> None:
    try:
        settings = Settings.from_env(dict(os.environ))
    except UpdaterError as e:
        raise SystemExit(str(e)) from e
    server = ThreadingHTTPServer(("0.0.0.0", 9000), handler(Updater(settings)))
    print(f"Updater for {' '.join(settings.services)} from {settings.repo}#{settings.branch}, on port 9000", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
