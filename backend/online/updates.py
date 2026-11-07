"""Updates of Synapse itself (Settings → Updates): is there newer code on the GitHub branch this
Synapse comes from, and — where the deployment runs an updater (docs/updates.md) — install it.

Where the code comes from is the deployment's word, never the user's: ``SYNAPSE_SOURCE``
(``https://github.com/owner/repo.git#main``, the same value Docker builds from), the commit this
copy was built from (``SYNAPSE_COMMIT``, set by the image; else the checkout's ``git rev-parse
HEAD``), and the updater's address and token (``SYNAPSE_UPDATER``, ``SYNAPSE_UPDATER_TOKEN``).

Synapse never builds or restarts anything itself. Installing asks the updater, a container of its
own and the only one with access to Docker, which looks the branch up on its own and builds exactly
what is there. So whoever could misuse this server could at most install what is already on that
branch.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import http as online_http

log = logging.getLogger(__name__)

CHECK_EVERY = 6 * 3600.0  # seconds between automatic checks: main changes only when something is merged
RETRY_AFTER = 3600.0  # ... and after a failed one (offline, GitHub's rate limit)
MIN_GAP = 30.0  # Check for Updates pressed again within this many seconds answers the last result
MAX_COMMITS = 20  # new commits listed in Settings
UPDATER_TIMEOUT = (3.0, 30.0)  # (connect, read): the updater asks Git for the branch before it answers
GITHUB = "https://api.github.com"
COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")
SOURCE_RE = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?(?:#([^:\s]+))?")


class UpdateError(Exception):
    """Updating cannot start; the message can be shown as it is."""


@dataclass(frozen=True)
class Source:
    owner: str
    repo: str
    branch: str

    @property
    def name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.name}/tree/{self.branch}"


def parse_source(text: str | None) -> Source | None:
    """``https://github.com/owner/repo.git#branch`` (the branch defaults to main); None when unset.
    Raises UpdateError for anything else: update checks need a GitHub repository."""
    text = (text or "").strip()
    if not text:
        return None
    m = SOURCE_RE.fullmatch(text)
    if not m:
        raise UpdateError(
            f"SYNAPSE_SOURCE is {text!r}: update checks need a GitHub repository, like "
            "https://github.com/owner/Synapse.git#main."
        )
    return Source(m.group(1), m.group(2), m.group(3) or "main")


def checkout_commit(app_root: Path) -> str | None:
    """The commit of a Git checkout Synapse runs from (not in Docker, whose image has no .git)."""
    if not (app_root / ".git").exists():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(app_root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = r.stdout.strip().lower()
    return sha if r.returncode == 0 and COMMIT_RE.fullmatch(sha) else None


def _github_json(url: str) -> dict:
    """One GitHub API answer, through the checked public transport (backend/online/http.py)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Synapse (update check)"}
    try:
        r = online_http.public_request("GET", url, headers=headers, timeout=online_http.TIMEOUT)
    except (online_http.PublicAddressError, requests.RequestException) as e:
        raise UpdateError(f"GitHub could not be reached ({type(e).__name__}).") from e
    try:
        if r.status_code in (403, 429):
            raise UpdateError("GitHub's rate limit was reached; Synapse looks again later.")
        if r.status_code == 404:
            raise UpdateError("GitHub does not know this repository, branch or commit.")
        if r.status_code >= 400:
            raise UpdateError(f"GitHub answered {r.status_code}.")
        data = r.json()
    except ValueError as e:
        raise UpdateError("GitHub's answer could not be read.") from e
    finally:
        r.close()
    if not isinstance(data, dict):
        raise UpdateError("GitHub's answer could not be read.")
    return data


def _commit(entry: dict) -> dict:
    sha = str(entry.get("sha") or "")
    info = entry.get("commit")
    info = info if isinstance(info, dict) else {}
    message = str(info.get("message") or "")
    who = info.get("committer")
    who = who if isinstance(who, dict) else {}
    return {
        "commit": sha,
        "short": sha[:7],
        "title": message.splitlines()[0] if message else "",
        "date": str(who.get("date") or ""),
        "url": str(entry.get("html_url") or ""),
    }


class Updates:
    """What Settings → Updates shows and does. ``before_install`` makes the backup taken first
    (it raises when that fails, and nothing is installed)."""

    def __init__(
        self,
        source: str | None,
        commit: str | None,
        updater_url: str | None,
        updater_token: str | None,
        *,
        before_install: Callable[[], object],
        app_root: Path | None = None,
        container: bool = False,
        github: Callable[[str], dict] = _github_json,
    ):
        self._source_text = source
        self._commit = (commit or "").strip().lower() or None
        self._app_root = app_root
        self.updater_url = (updater_url or "").strip().rstrip("/") or None
        self.updater_token = (updater_token or "").strip() or None
        self.before_install = before_install
        self.container = container
        self._github = github
        self._lock = threading.Lock()
        self._result: dict = {}
        self._checked: float | None = None
        self._checked_at: str | None = None
        self._error: str | None = None
        self._timer: threading.Timer | None = None
        self._stopped = threading.Event()

    # ------------------------------------------------------------------ what is there
    def source(self) -> Source | None:
        return parse_source(self._source_text)

    def commit(self) -> str | None:
        if self._commit is None and self._app_root is not None:
            self._commit = checkout_commit(self._app_root) or ""
        return self._commit or None

    def check(self) -> dict:
        """Ask GitHub now. Keeps the last good answer when this one fails."""
        try:
            source = self.source()
            if source is None:
                raise UpdateError("This Synapse does not say where its code comes from (SYNAPSE_SOURCE).")
            result = self._ask(source, self.commit())
            error = None
        except UpdateError as e:
            result, error = None, str(e)
        with self._lock:
            if result is not None:
                self._result = result
            self._error = error
            self._checked = time.monotonic()
            self._checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return self.status()

    def _ask(self, source: Source, commit: str | None) -> dict:
        base = f"{GITHUB}/repos/{source.owner}/{source.repo}"
        if commit:
            data = self._github(f"{base}/compare/{commit}...{source.branch}")
            commits = [_commit(c) for c in data.get("commits") or [] if isinstance(c, dict)]
            behind = int(data.get("ahead_by") or 0)
            if data.get("status") == "diverged":
                # built from a commit that is not on the branch (another branch, a local change)
                behind = max(behind, 1)
            newest = list(reversed(commits))[:MAX_COMMITS]
            latest = newest[0] if newest else None
            if latest is None and data.get("status") == "identical":
                latest = {"commit": commit, "short": commit[:7], "title": "", "date": "", "url": ""}
            return {
                "behind": behind,
                "commits": newest if behind else [],
                "latest": latest,
                "diverged": data.get("status") == "diverged",
                "compare_url": str(data.get("html_url") or ""),
            }
        latest = _commit(self._github(f"{base}/commits/{source.branch}"))
        return {"behind": None, "commits": [], "latest": latest, "diverged": False, "compare_url": ""}

    def status(self, refresh: bool = False) -> dict:
        """For Settings. ``refresh`` asks GitHub first, unless it was asked moments ago."""
        if refresh:
            with self._lock:
                recent = self._checked is not None and time.monotonic() - self._checked < MIN_GAP
            if not recent:
                return self.check()
        try:
            source = self.source()
            source_error = None
        except UpdateError as e:
            source, source_error = None, str(e)
        commit = self.commit()
        with self._lock:
            result, error, checked = dict(self._result), self._error, self._checked_at
        return {
            "available": source is not None,
            "source": {"repo": source.name, "branch": source.branch, "url": source.url} if source else None,
            "current": {"commit": commit, "short": commit[:7]} if commit else None,
            "container": self.container,
            "behind": result.get("behind"),
            "commits": result.get("commits") or [],
            "latest": result.get("latest"),
            "diverged": bool(result.get("diverged")),
            "compare_url": result.get("compare_url") or "",
            "checked": checked,
            "error": source_error or error,
            "can_install": self.updater_url is not None,
            "install": self._updater_state() if self.updater_url else None,
        }

    # ------------------------------------------------------------------ installing
    def _updater(self, method: str, path: str, timeout: tuple[float, float] = UPDATER_TIMEOUT) -> dict:
        if not self.updater_url:
            raise UpdateError("This Synapse has no updater to install updates with (docs/updates.md).")
        if not self.updater_token:
            raise UpdateError("The updater is set up without its token (SYNAPSE_UPDATER_TOKEN).")
        try:
            with requests.Session() as session:
                session.trust_env = False  # the updater is on the deployment's own network, never behind a proxy
                r = session.request(
                    method,
                    self.updater_url + path,
                    headers={"Authorization": f"Bearer {self.updater_token}"},
                    timeout=timeout,
                    allow_redirects=False,
                )
        except requests.RequestException as e:
            raise UpdateError(f"The updater could not be reached at {self.updater_url}.") from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            message = data.get("error") if isinstance(data, dict) else None
            raise UpdateError(message or f"The updater answered {r.status_code}.")
        return data if isinstance(data, dict) else {}

    def _updater_state(self) -> dict:
        try:
            return self._updater("GET", "/status", timeout=(3.0, 5.0))
        except UpdateError as e:
            return {"state": "unknown", "message": str(e)}

    def install(self) -> dict:
        """Back up, then ask the updater to install the branch's newest commit. It answers at once
        and restarts Synapse within about a minute."""
        state = self._updater("GET", "/status")
        if state.get("state") == "running":
            raise UpdateError("An update is being installed already.")
        try:
            self.before_install()
        except Exception as e:
            raise UpdateError(f"The backup before updating failed, so nothing was changed: {e}") from e
        return self._updater("POST", "/update")

    # ------------------------------------------------------------------ automatic checks
    def start(self) -> None:
        """Check now (in the background), then every few hours (the web app only)."""
        if self._source_text is None or not self._source_text.strip():
            return

        def tick():
            if self._stopped.is_set():
                return
            try:
                self.check()
            except Exception:
                log.exception("Checking for updates failed")
            with self._lock:
                wait = RETRY_AFTER if self._error else CHECK_EVERY
            self._timer = threading.Timer(wait, tick)
            self._timer.daemon = True
            self._timer.start()

        threading.Thread(target=tick, name="update-check", daemon=True).start()

    def stop(self) -> None:
        self._stopped.set()
        if self._timer:
            self._timer.cancel()
