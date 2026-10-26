"""Running Git for the notes history, isolated from the user's own Git setup.

The repository is ours alone: its Git directory sits next to the notes folder
(``data/notes-history.git``) and the notes folder is its work tree, passed on every call, so the
notes folder never gets a ``.git`` inside. Every call runs with the user's global and system
configuration switched off: a global ``commit.gpgsign``, hooks path, ``autocrlf`` or an alias must
not change what is recorded or make a commit fail. Paths are literal (a note may be called
``a*b.md``) and output is byte-exact (``-z``, no quoting).

``FileLock`` serialises the history between the web app and the MCP server, which are separate
processes writing the same notes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

# Tracked: Markdown notes only. Images, the notes trash, the old JSON history, folder colours and
# every other dot file stay out (the last matching line wins).
EXCLUDE = "*\n!*/\n!*.md\n!*.MD\n!*.Md\n!*.mD\n.*\n/attachments/\n"
NO_HOOKS = "synapse-no-hooks"
MIN_VERSION = (2, 25)  # --pathspec-from-file


class GitError(Exception):
    def __init__(self, args: Sequence[str], code: int, stderr: str):
        super().__init__(f"git {' '.join(args[:2])} failed ({code}): {stderr.strip()[:500]}")
        self.code = code
        self.stderr = stderr


def find_git() -> str | None:
    """The git executable (``SYNAPSE_GIT`` overrides, e.g. to test without Git)."""
    forced = os.environ.get("SYNAPSE_GIT")
    if forced is not None:
        return forced or None  # SYNAPSE_GIT="" means: behave as if Git were missing
    return shutil.which("git")


def git_version(exe: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, timeout=10, **_hidden()).stdout.decode()
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.strip().split()
    if len(parts) < 3:
        return None
    nums = []
    for p in parts[2].split("."):
        if not p.isdigit():
            break
        nums.append(int(p))
    return tuple(nums) or None


def _hidden() -> dict:
    """No console window flashing up on Windows for each call."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}  # type: ignore[attr-defined]


class Repo:
    def __init__(self, git_dir: Path, work_tree: Path, exe: str):
        self.git_dir = Path(git_dir)
        self.work_tree = Path(work_tree)
        self.exe = exe
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            GIT_DIR=str(self.git_dir),
            GIT_WORK_TREE=str(self.work_tree),
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_LITERAL_PATHSPECS="1",
            GIT_TERMINAL_PROMPT="0",
            GIT_OPTIONAL_LOCKS="0",  # status must not take the index lock behind a reader's back
            # Git before 2.32 ignores GIT_CONFIG_GLOBAL and reads ~/.gitconfig: point it elsewhere.
            HOME=str(self.git_dir),
            XDG_CONFIG_HOME=str(self.git_dir / "xdg"),
            LC_ALL="C",
            LANG="C",
        )
        self.env = env
        self.overrides = [
            "-c",
            f"core.hooksPath={self.git_dir / NO_HOOKS}",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.safecrlf=false",
            "-c",
            "core.quotepath=false",
            "-c",
            "core.precomposeunicode=true",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "advice.detachedHead=false",
            "-c",
            "i18n.logOutputEncoding=UTF-8",
        ]

    def run(
        self,
        *args: str,
        input: bytes | None = None,
        ok: tuple[int, ...] = (0,),
        env: dict[str, str] | None = None,
        timeout: float = 120,
    ) -> subprocess.CompletedProcess[bytes]:
        e = self.env if not env else {**self.env, **env}
        try:
            r = subprocess.run(
                [self.exe, *self.overrides, *args],
                input=input,
                capture_output=True,
                env=e,
                cwd=str(self.work_tree),
                timeout=timeout,
                **_hidden(),
            )
        except (OSError, subprocess.SubprocessError) as err:
            raise GitError(args, -1, str(err)) from err
        if r.returncode not in ok:
            raise GitError(args, r.returncode, r.stderr.decode("utf-8", "replace"))
        return r

    def out(self, *args: str, **kw) -> str:
        return self.run(*args, **kw).stdout.decode("utf-8", "replace")

    # ------------------------------------------------------------------ setup
    def exists(self) -> bool:
        return (self.git_dir / "HEAD").is_file()

    def init(self) -> None:
        self.git_dir.mkdir(parents=True, exist_ok=True)
        self.run("init", "-q")
        self.configure()
        self.run("symbolic-ref", "HEAD", "refs/heads/main")

    def configure(self) -> None:
        """Settings stored in the repository itself, so plain ``git`` run by hand sees the same."""
        (self.git_dir / NO_HOOKS).mkdir(exist_ok=True)
        (self.git_dir / "info").mkdir(exist_ok=True)
        (self.git_dir / "info" / "exclude").write_text(EXCLUDE, "utf-8")
        for key, value in (
            ("core.bare", "false"),
            ("core.autocrlf", "false"),
            ("core.quotepath", "false"),
            ("core.precomposeunicode", "true"),
            ("core.hooksPath", str(self.git_dir / NO_HOOKS)),
            ("commit.gpgsign", "false"),
            ("tag.gpgsign", "false"),
            ("gc.auto", "2000"),
        ):
            self.run("config", key, value)
        # The work tree is passed on every call; a stored one would break when data/ moves.
        self.run("config", "--unset", "core.worktree", ok=(0, 5))

    def head(self) -> str | None:
        r = self.run("rev-parse", "--verify", "-q", "HEAD^{commit}", ok=(0, 1))
        return r.stdout.decode().strip() or None

    def healthy(self) -> bool:
        if not self.exists():
            return False
        try:
            self.run("rev-parse", "--git-dir")
            head = self.head()
            if head:
                self.run("cat-file", "-e", f"{head}^{{tree}}")
            return True
        except GitError:
            return False


class FileLock:
    """One holder at a time, across threads (re-entrant) and processes (an OS lock on a file)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._mutex = threading.RLock()
        self._depth = 0
        self._fh: BinaryIO | None = None

    def _try_os_lock(self) -> bool:
        fh = self._fh
        assert fh is not None
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def acquire(self, timeout: float | None = None) -> bool:
        end = None if timeout is None else time.monotonic() + timeout
        if not self._mutex.acquire(timeout=-1 if timeout is None else max(0.0, timeout)):
            return False
        if self._depth:
            self._depth += 1
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "a+b")
            if sys.platform == "win32" and self._fh.tell() == 0:
                self._fh.write(b"\0")
                self._fh.flush()
            while not self._try_os_lock():
                if end is not None and time.monotonic() >= end:
                    self._fh.close()
                    self._fh = None
                    self._mutex.release()
                    return False
                time.sleep(0.02)
        except BaseException:
            if self._fh:
                self._fh.close()
                self._fh = None
            self._mutex.release()
            raise
        self._depth = 1
        return True

    def release(self) -> None:
        self._depth -= 1
        if not self._depth and self._fh:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
        self._mutex.release()

    @contextmanager
    def held(self, timeout: float | None = None) -> Iterator[bool]:
        got = self.acquire(timeout)
        try:
            yield got
        finally:
            if got:
                self.release()


_locks: dict[Path, FileLock] = {}
_locks_guard = threading.Lock()


def lock_for(path: Path) -> FileLock:
    """The lock of ``path``, one object per file in this process (two would block each other)."""
    p = Path(path).resolve()
    with _locks_guard:
        lock = _locks.get(p)
        if lock is None:
            lock = _locks[p] = FileLock(p)
        return lock
