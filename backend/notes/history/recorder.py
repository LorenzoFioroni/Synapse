"""The history of the notes' text, kept in a Git repository of its own.

Every change to a note ends up in one linear, vault-wide history, like an Overleaf project's: the
history of one note is that history filtered to it. Who made each change is recorded:

* **the user** — edits saved by the app become *pending* (``synapse-pending.json`` in the Git
  directory, so the MCP server process sees them too) and are committed as a checkpoint once the
  note has been left alone for a while (``IDLE``), when the app asks (a note was closed or
  switched), when the same note is next saved from another device, and before anything else is
  recorded for it;
* **an AI app** — each change is its own commit, made right after it, with the app's summary;
  the note's pending edits are committed just before, so the AI's commit holds only its change;
* **Synapse itself** — renames (committed as pure renames, so Git always follows them), deletes,
  restores from the trash, citation-key rewrites: one commit each, after a checkpoint;
* **outside Synapse** — a note that differs from the history without being pending was changed by
  another program; that difference is committed first, before anything else touches the note.

``FileLock`` makes the app and the MCP server take turns; a change is written to disk and
committed under the lock, so neither process can mistake the other's change for an outside one.
The history must never stop a note from being saved: a failing Git call is logged and the note is
saved anyway (it shows up as part of a later checkpoint).

Only text is kept: images are not versioned (an old version may show an image that is gone), and
nothing is ever pruned.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ...library.agent import THIS_COMPUTER
from .git import MIN_VERSION, GitError, Repo, find_git, git_version, lock_for
from .legacy import LegacyHistory
from .migrate import import_legacy
from .reading import MILESTONE_PREFIX, HistoryReader, split_message

log = logging.getLogger(__name__)

IDLE = 120  # seconds without a save before a note's edits become a checkpoint
TICK = 30  # how often the app looks for idle edits and changes made outside
PENDING = "synapse-pending.json"
EMAILS = {"human": "you@synapse.local", "ai": "ai@synapse.local", "outside": "outside@synapse.local"}
OUTSIDE = "Outside Synapse"
# Your edits that end where they began within a session leave no versions (_drop_detour): the
# commits are this far apart at most (as the app groups them), and so many are looked at.
DETOUR_GAP = 30 * 60
DETOUR_SCAN = 200


@dataclass
class Change:
    """What ``NoteHistory.change`` records: callers add the paths the operation touched."""

    paths: list[str] | None
    commit: str | None = None
    extra: dict = field(default_factory=dict)


def _title(path: str) -> str:
    return Path(path).stem


def _subject(paths: list[str], status: dict[str, str], kind: str) -> str:
    """ "Edited Alpha", "Created Beta", "Edited 3 notes"; "… outside Synapse" for outside changes."""
    states = {status.get(p) for p in paths}
    verb = (
        "Created"
        if states == {"new"}
        else "Deleted"
        if states == {"gone"}
        else "Changed"
        if kind == "outside"
        else "Edited"
    )
    what = _title(paths[0]) if len(paths) == 1 else f"{len(paths)} notes"
    return f"{verb} {what}" + (" outside Synapse" if kind == "outside" else "")


def message(
    subject: str,
    *,
    body: str = "",
    by: str,
    kind: str,
    agent: str | None = None,
    device: str | None = None,
    restored: str | None = None,
) -> str:
    subject = " ".join((subject or "").split())[:200] or "Changed"
    lines = [f"Synapse-By: {by}", f"Synapse-Kind: {kind}"]
    if agent:
        lines.append(f"Synapse-Agent: {' '.join(agent.split())}")
    if device:
        lines.append(f"Synapse-Device: {' '.join(device.split())}")
    if restored:
        lines.append(f"Synapse-Restored: {restored}")
    return subject + "\n\n" + (body.strip() + "\n\n" if body.strip() else "") + "\n".join(lines) + "\n"


class NoteHistory(HistoryReader):
    def __init__(self, vault_root: Path | str, git_dir: Path | str, *, valid_path: Callable[[str], bool] | None = None):
        self.root = Path(vault_root)
        self.git_dir = Path(git_dir)
        self.legacy = LegacyHistory(self.root)  # written only while Git is missing
        self.valid_path = valid_path or (lambda p: True)
        self.lock = lock_for(self.git_dir.parent / f".{self.git_dir.name}.lock")
        self.repo: Repo | None = None
        self.reason: str | None = None  # why there is no history, for Settings
        self.last_error: str | None = None
        self.git_version: str | None = None
        exe = find_git()
        if not exe:
            self.reason = "Git is not installed."
        else:
            v = git_version(exe)
            if v is None:
                self.reason = "Git could not be run."
            elif v < MIN_VERSION:
                self.reason = (
                    f"Git {'.'.join(map(str, v))} is too old (needs {'.'.join(map(str, MIN_VERSION))} or newer)."
                )
            else:
                self.git_version = ".".join(map(str, v))
                self.repo = Repo(self.git_dir, self.root, exe)
        self._ready = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.on_tick: Callable[[], object] | None = None
        self.on_commit: Callable[[], object] | None = None  # the change feed: history panels refresh

    # ------------------------------------------------------------------ state
    @property
    def enabled(self) -> bool:
        return self.repo is not None

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "git": self.git_version,
            "reason": self.reason,
            "error": self.last_error,
            "folder": str(self.git_dir),
        }

    def _safe(self, fn: Callable[[], object], what: str) -> bool:
        try:
            fn()
            self.last_error = None
            return True
        except (GitError, OSError, ValueError) as e:
            self.last_error = f"{what}: {e}"
            log.warning("Notes history: %s failed: %s", what, e)
            return False

    def _ensure(self) -> None:
        """Create the repository the first time (bringing in the old JSON history), or a new one
        when it is damaged (the damaged one is kept aside)."""
        if self._ready:
            return
        r = self.repo
        assert r is not None
        if r.exists() and not r.healthy():
            aside = self.git_dir.with_name(f"{self.git_dir.name}.damaged-{datetime.now():%Y%m%d-%H%M%S}")
            log.warning(
                "Notes history at %s is damaged; starting a new one (the old one is kept as %s)", r.git_dir, aside
            )
            os.replace(self.git_dir, aside)
        if not r.exists():
            r.init()
            import_legacy(r, self.legacy, self.root, self.valid_path, message)
            self._clean_index(force=True)
            self._commit(None, by="human", kind="start", name=THIS_COMPUTER, subject="Start of the history",
                         body="The notes as they were when Synapse started keeping their history.")  # fmt: skip
        else:
            r.configure()  # excludes and settings of this version
        self._ready = True

    def _prepare(self) -> None:
        if not self._ready and self.enabled:
            with self.lock.held():
                self._ensure()

    def lost(self) -> bool:
        """No usable history: missing, damaged or without a single commit (a new data folder)."""
        r = self.repo
        if r is None:
            return False
        with self.lock.held():
            try:
                return not r.healthy() or r.head() is None
            except GitError:
                return True

    # ------------------------------------------------------------------ pending edits
    def _pending_path(self) -> Path:
        return self.git_dir / PENDING

    def _pending(self) -> dict[str, dict]:
        try:
            d = json.loads(self._pending_path().read_text("utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_pending(self, d: dict[str, dict]) -> None:
        p = self._pending_path()
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        os.replace(tmp, p)

    # ------------------------------------------------------------------ git plumbing
    def _status(self) -> dict[str, str]:
        """Notes that differ from the history, as {vault-relative path: "new" | "gone" | "changed"}."""
        raw = (
            self._r()
            .run("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", "--ignore-submodules")
            .stdout.decode("utf-8", "replace")
        )
        out: dict[str, str] = {}
        for rec in raw.split("\0"):
            if len(rec) > 3:
                xy = rec[:2]
                out[rec[3:]] = "new" if xy == "??" or "A" in xy else "gone" if "D" in xy else "changed"
        return out

    def _clean_index(self, force: bool = False) -> None:
        """Nothing staged: a commit holds exactly the paths it was asked for."""
        r = self._r()
        if r.head() is None:
            r.run("read-tree", "--empty")
            return
        if force or r.run("diff", "--cached", "--quiet", ok=(0, 1)).returncode:
            r.run("reset", "-q")

    def _commit(
        self,
        paths: list[str] | None,
        *,
        by: str,
        kind: str,
        name: str,
        subject: str,
        body: str = "",
        agent: str | None = None,
        device: str | None = None,
        restored: str | None = None,
        when: float | None = None,
    ) -> str | None:
        """Commit the current text of ``paths`` (None: of every changed note). None if nothing changed."""
        r = self._r()
        changed = list(self._status())
        if paths is not None:
            want = set(paths)
            changed = [p for p in changed if p in want]
        if not changed:
            return None
        self._clean_index()
        r.run("add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul", input="\0".join(changed).encode("utf-8"))
        if r.run("diff", "--cached", "--quiet", ok=(0, 1)).returncode == 0:
            return None
        clean = " ".join(name.replace("<", "").replace(">", "").split()) or "Synapse"
        env = {
            "GIT_AUTHOR_NAME": clean,
            "GIT_AUTHOR_EMAIL": EMAILS.get(by, EMAILS["human"]),
            "GIT_COMMITTER_NAME": "Synapse",
            "GIT_COMMITTER_EMAIL": "synapse@synapse.local",
        }
        if when:
            off = datetime.fromtimestamp(when).astimezone().utcoffset()
            mins = int(off.total_seconds() // 60) if off else 0
            env["GIT_AUTHOR_DATE"] = f"{int(when)} {'+' if mins >= 0 else '-'}{abs(mins) // 60:02d}{abs(mins) % 60:02d}"
        msg = message(subject, body=body, by=by, kind=kind, agent=agent, device=device, restored=restored)
        r.run("commit", "-q", "--no-verify", "-F", "-", input=msg.encode("utf-8"), env=env)
        head = r.head()
        if self.on_commit:
            try:
                self.on_commit()
            except Exception:
                log.exception("history listener failed")
        return head

    def _checkpoint(self, paths: list[str] | None = None, *, restored: str | None = None) -> list[str]:
        """Commit what is waiting for ``paths`` (None: every note): first changes made outside
        Synapse, then the user's edits, one commit per device. Holds the lock."""
        status = self._status()
        changed = list(status) if paths is None else [p for p in status if p in set(paths)]
        pend = self._pending()
        made: list[str] = []
        outside = sorted(p for p in changed if p not in pend)
        if outside:
            c = self._commit(
                outside,
                by="outside",
                kind="outside",
                name=OUTSIDE,
                subject=_subject(outside, status, "outside"),
            )
            made += [c] if c else []
        groups: dict[str, list[str]] = {}
        for p in changed:
            if p in pend:
                groups.setdefault(pend[p].get("device") or THIS_COMPUTER, []).append(p)
        for device, ps in groups.items():
            ps.sort()
            if restored:
                subject = f"Restored an earlier version of {_title(ps[0])}"
            else:
                subject = _subject(ps, status, "edit")
            c = self._commit(
                ps,
                by="human",
                kind="restore" if restored else "edit",
                name=device,
                device=device,
                subject=subject,
                restored=restored,
                when=max(float(pend[p].get("last") or 0) for p in ps) or None,
            )
            if c and not restored and self._drop_detour(device):
                c = None
            made += [c] if c else []
        # Forget what was committed, and saves that changed nothing a while ago.
        still = set(self._status()) if made else set(changed)  # what committing left (normally nothing)
        now = time.time()
        scope = set(paths) if paths is not None else set(pend)
        keep = {
            p: e
            for p, e in pend.items()
            if p not in scope or (p in still) or (now - float(e.get("last") or 0) < 60 and p not in changed)
        }
        if keep != pend:
            self._save_pending(keep)
        return made

    def _drop_detour(self, device: str) -> bool:
        """The edit just committed brought the notes back to a state of a moment ago — you changed
        something and undid it, and a checkpoint fell in between. The detour is dropped (the
        branch goes back to that state) rather than kept as versions that change nothing in the
        end. Only your own edits on this device, close together and without a milestone on them,
        are dropped; anything else in between keeps them. Returns whether it dropped any."""
        r = self._r()
        raw = r.out("log", f"--max-count={DETOUR_SCAN}", "--format=%x1e%H%x1f%T%x1f%at%x1f%B", "HEAD")
        commits: list[tuple[str, str, int, dict[str, str]]] = []
        for chunk in raw.split("\x1e")[1:]:
            sha, tree, at, msg = (chunk.split("\x1f", 3) + ["", "", "", ""])[:4]
            commits.append((sha, tree, int(at) if at.isdigit() else 0, split_message(msg)[2]))
        if len(commits) < 2:
            return False
        tagged = set(r.out("for-each-ref", "--format=%(*objectname) %(objectname)", "refs/tags").split())
        head_tree = commits[0][1]
        for k in range(1, len(commits)):
            c_sha, c_tree, c_at, tr = commits[k]
            if c_tree == head_tree:
                r.run("reset", "-q", "--soft", c_sha)  # the work tree and index already are that state
                log.info("Notes history: dropped %d versions that ended where they began", k)
                return True
            mine = tr.get("Synapse-By") == "human" and tr.get("Synapse-Kind") == "edit"
            if not mine or tr.get("Synapse-Device", THIS_COMPUTER) != " ".join(device.split()) or c_sha in tagged:
                return False
            if commits[k - 1][2] - c_at > DETOUR_GAP:
                return False
        return False

    # ------------------------------------------------------------------ recording
    @contextmanager
    def saving(self, paths: list[str], *, device: str | None = None) -> Iterator[None]:
        """Wrap the app saving notes (the user's edits): they become pending, to be committed
        later as one checkpoint. A save from another device first commits what the previous one left."""
        device = device or THIS_COMPUTER
        if not self.enabled:
            yield
            return
        with self.lock.held(timeout=10) as got:
            ok = got and self._safe(lambda: self._before_save(paths, device), "checkpoint before saving")
            yield
            if ok:
                self._safe(lambda: self._mark(paths, device), "noting a save")

    def _before_save(self, paths: list[str], device: str) -> None:
        self._ensure()
        pend = self._pending()
        other = [p for p in paths if p in pend and (pend[p].get("device") or THIS_COMPUTER) != device]
        unknown = [p for p in paths if p not in pend]
        if other or unknown:
            self._checkpoint(other + unknown)  # another device's edits; or changes made outside

    def _mark(self, paths: list[str], device: str) -> None:
        pend = self._pending()
        now = time.time()
        changed = False
        for p in paths:
            e = pend.get(p)
            if e is None or e.get("device") != device:
                pend[p] = {"device": device, "first": now, "last": now}
                changed = True
            elif now - float(e.get("last") or 0) > 5:  # idle time needs only coarse precision
                e["last"] = now
                changed = True
        if changed:
            self._save_pending(pend)

    @contextmanager
    def change(
        self,
        *,
        kind: str,
        subject: str,
        by: str = "human",
        agent: str | None = None,
        device: str | None = None,
        paths: list[str] | None = None,
        body: str = "",
    ) -> Iterator[Change]:
        """Wrap one operation that is recorded as a commit of its own (an AI change, a rename,
        a delete …). What was waiting for the same notes (``paths``; None: every note) is
        committed first. The operation adds the paths it ended up touching to ``Change.paths``;
        with None, everything that changed is its commit."""
        rec = Change(paths=list(paths) if paths is not None else None)
        if not self.enabled:
            yield rec
            return
        with self.lock.held():

            def before() -> None:
                self._ensure()
                self._checkpoint(rec.paths)

            ok = self._safe(before, "checkpoint")
            yield rec
            if ok:
                if by == "ai":
                    name = agent or "AI app"
                else:
                    name = device or THIS_COMPUTER

                def commit():
                    rec.commit = self._commit(
                        rec.paths,
                        by=by,
                        kind=kind,
                        name=name,
                        subject=subject,
                        body=body,
                        agent=agent if by == "ai" else None,
                        device=(device or THIS_COMPUTER) if by == "human" else None,
                    )
                    if rec.paths:
                        pend = self._pending()
                        if any(p in pend for p in rec.paths):
                            self._save_pending({p: e for p, e in pend.items() if p not in set(rec.paths)})

                self._safe(commit, f"recording {kind}")

    def checkpoint(self, paths: list[str] | None = None, *, restored: str | None = None) -> list[str]:
        """Commit now what is waiting (for ``paths``; None: every note). ``restored`` names the
        commit an earlier version was put back from."""
        made: list[str] = []
        if not self.enabled:
            return made
        with self.lock.held(timeout=30) as got:
            if got:

                def run() -> None:
                    self._ensure()
                    made.extend(self._checkpoint(paths, restored=restored))

                self._safe(run, "checkpoint")
        return made

    def due(self) -> list[str]:
        """Commit edits left alone for ``IDLE`` seconds, and changes made outside Synapse."""
        made: list[str] = []
        if not self.enabled:
            return made
        with self.lock.held(timeout=1) as got:
            if not got:
                return made

            def run():
                self._ensure()
                pend = self._pending()
                changed = list(self._status())
                now = time.time()
                ready = [p for p in changed if p not in pend or now - float(pend[p].get("last") or 0) >= IDLE]
                if ready:
                    made.extend(self._checkpoint(ready))
                elif pend:
                    self._checkpoint([p for p in pend if p not in set(changed)])  # tidy saves that changed nothing

            self._safe(run, "checkpoint")
        return made

    def tick(self) -> None:
        """Checkpoint history and refresh external consumers, even without Git."""
        self.due()
        if self.on_tick:
            try:
                self.on_tick()
            except Exception:
                log.exception("Notes refresh failed")

    def start(self) -> None:
        """In the web app: record what changed while it was not running, then keep checkpointing."""
        if self._thread:
            return
        self._stop.clear()

        def loop():
            self.checkpoint()
            self.tick()
            while not self._stop.wait(TICK):
                self.tick()

        self._thread = threading.Thread(target=loop, name="notes-history", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    # ------------------------------------------------------------------ milestones
    def add_milestone(self, label: str) -> dict:
        label = " ".join((label or "").split())[:200]
        if not label:
            raise ValueError("Give the milestone a name.")
        if not self.enabled:
            raise ValueError(self.reason or "The notes history is off.")
        with self.lock.held():
            self._ensure()
            self._checkpoint()
            r = self._r()
            if r.head() is None:
                raise ValueError("There are no notes to mark yet.")
            base = f"{MILESTONE_PREFIX}{datetime.now():%Y%m%d-%H%M%S}"
            name, n = base, 2
            while r.run("rev-parse", "-q", "--verify", f"refs/tags/{name}", ok=(0, 1)).returncode == 0:
                name, n = f"{base}-{n}", n + 1
            env = {"GIT_COMMITTER_NAME": "Synapse", "GIT_COMMITTER_EMAIL": "synapse@synapse.local"}
            r.run("tag", "-a", name, "-F", "-", "HEAD", input=label.encode("utf-8"), env=env)
        if self.on_commit:
            self.on_commit()
        return next(m for m in self.milestones() if m["id"] == name)

    def rename_milestone(self, mid: str, label: str) -> dict:
        label = " ".join((label or "").split())[:200]
        if not label or not re.fullmatch(rf"{MILESTONE_PREFIX}[0-9\-]+", mid or ""):
            raise ValueError("Give the milestone a name.")
        with self.lock.held():
            r = self._r()
            commit = r.out("rev-parse", "--verify", f"refs/tags/{mid}^{{commit}}").strip()
            env = {"GIT_COMMITTER_NAME": "Synapse", "GIT_COMMITTER_EMAIL": "synapse@synapse.local"}
            r.run("tag", "-f", "-a", mid, "-F", "-", commit, input=label.encode("utf-8"), env=env)
        return next(m for m in self.milestones() if m["id"] == mid)

    def delete_milestone(self, mid: str) -> None:
        if not re.fullmatch(rf"{MILESTONE_PREFIX}[0-9\-]+", mid or ""):
            raise ValueError("No such milestone.")
        with self.lock.held():
            self._r().run("tag", "-d", mid)

    # ------------------------------------------------------------------ undoing an AI change
    def reverted(self, commit: str, path: str, current: str) -> str:
        """The note's text with the change of ``commit`` taken out and every later edit kept.
        Raises ValueError when later edits overlap it."""
        e = next((x for x in self.note_log(path, limit=500) if x["id"] == commit), None)
        if e is None:
            raise ValueError("That change is not in this note's history.")
        v = self.version(commit, e["path"], e["from_path"])
        before, after = v["before"] or "", v["after"] or ""
        if current == after:
            return before
        import tempfile

        with tempfile.TemporaryDirectory(dir=str(self.git_dir)) as tmp:
            files = []
            for name, text in (("now", current), ("change", after), ("before", before)):
                f = Path(tmp) / name
                f.write_bytes(text.encode("utf-8"))
                files.append(str(f))
            res = self._r().run("merge-file", "-p", "-q", *files, ok=tuple(range(0, 128)))
        if res.returncode:
            raise ValueError("The note was changed after it in the same places, so it cannot be taken out on its own.")
        return res.stdout.decode("utf-8", "replace")

    # ------------------------------------------------------------------ backups
    def bundle(self, dest: Path) -> bool:
        """The whole history in one file (``git bundle``), for backups. False when there is none."""
        if not self.enabled:
            return False
        with self.lock.held(timeout=60) as got:
            if not got:
                return False
            ok = [False]

            def run():
                self._ensure()
                self._checkpoint()
                if self._r().head() is None:
                    return
                self._r().run("bundle", "create", "-q", str(dest), "--all", timeout=600)
                ok[0] = True

            self._safe(run, "bundling the history")
            return ok[0]

    def after_restore(self, bundle: Path | None, label: str, *, rebuild: bool = False) -> None:
        """A backup's notes replaced the notes folder. The history keeps everything it had (the
        restore is one more commit); only a history that was ``lost`` before the restore began is
        rebuilt from the backup's bundle (the one made meanwhile is kept aside)."""
        if not self.enabled:
            return
        with self.lock.held():

            def run():
                r = self._r()
                if rebuild and bundle is not None and bundle.is_file():
                    if r.exists():
                        aside = self.git_dir.with_name(f"{self.git_dir.name}.replaced-{datetime.now():%Y%m%d-%H%M%S}")
                        os.replace(self.git_dir, aside)
                    r.init()
                    r.run(
                        "fetch",
                        "-q",
                        "--update-head-ok",
                        str(bundle),
                        "+refs/heads/*:refs/heads/*",
                        "+refs/tags/*:refs/tags/*",
                    )
                    self._clean_index(force=True)
                self._ready = False
                self._ensure()
                self._save_pending({})
                self._commit(None, by="human", kind="restore", name=THIS_COMPUTER, device=THIS_COMPUTER,
                             subject=f"Restored the backup {label}")  # fmt: skip

            self._safe(run, "recording the restored backup")
