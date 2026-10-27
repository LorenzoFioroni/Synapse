"""Reading the notes history: commits, the text of a note at a commit, differences, milestones.

Commits carry who made them in trailers at the end of the message, so the app does not have to
guess from author names:

    Synapse-By: human | ai | outside
    Synapse-Kind: start | edit | ai | outside | rename | delete | restore | bulk | earlier
    Synapse-Agent: Claude Code          (AI changes)
    Synapse-Device: iPad                (the user's changes)
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .git import GitError, Repo

TRAILERS = ("Synapse-By", "Synapse-Kind", "Synapse-Agent", "Synapse-Device", "Synapse-Restored")
LOG_FORMAT = "%x1e%H%x1f%T%x1f%P%x1f%at%x1f%B%x1f"
MILESTONE_PREFIX = "m-"
MAX_DIFF_FILES = 60
# Renames Synapse makes are exact; a lower threshold pairs up two different short notes that share
# most of their front matter.
RENAMES = "-M90%"
MAX_DIFF_BYTES = 4 * 1024 * 1024


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def sha256(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def split_message(msg: str) -> tuple[str, str, dict[str, str]]:
    """(subject, body, trailers) of a commit message."""
    lines = msg.rstrip("\n").split("\n")
    trailers: dict[str, str] = {}
    while lines and ":" in lines[-1] and lines[-1].split(":", 1)[0] in TRAILERS:
        k, v = lines.pop().split(":", 1)
        trailers[k] = v.strip()
    while lines and not lines[-1].strip():
        lines.pop()
    subject = lines[0] if lines else ""
    body = "\n".join(lines[1:]).strip("\n")
    return subject, body, trailers


def parse_log(raw: bytes) -> list[dict]:
    """``git log -z --name-status --format=LOG_FORMAT`` → entries, newest first."""
    out = []
    for chunk in raw.decode("utf-8", "replace").split("\x1e")[1:]:
        head, _, rest = chunk.rpartition("\x1f")
        sha, tree, parents, at, msg = (head.split("\x1f", 4) + ["", "", "", "", ""])[:5]
        subject, body, tr = split_message(msg)
        fields = rest.lstrip("\0\n").split("\0")
        files = []
        k = 0
        while k < len(fields):
            st = fields[k].strip()
            if not st:
                k += 1
                continue
            if st[0] in "RC" and k + 2 < len(fields):
                files.append({"status": st[0], "from": fields[k + 1], "path": fields[k + 2]})
                k += 3
            elif k + 1 < len(fields):
                files.append({"status": st[0], "path": fields[k + 1]})
                k += 2
            else:
                break
        ts = int(at) if at.isdigit() else 0
        out.append(
            {
                "id": sha,
                "tree": tree,
                "parents": parents.split(),
                "at": iso(ts),
                "ts": ts,
                "by": tr.get("Synapse-By") or "outside",
                "kind": tr.get("Synapse-Kind") or "outside",
                "agent": tr.get("Synapse-Agent"),
                "device": tr.get("Synapse-Device"),
                "restored_from": tr.get("Synapse-Restored"),
                "summary": subject,
                "body": body,
                "files": files,
            }
        )
    return out


class HistoryReader:
    """What the history holds (mixed into NoteHistory, which provides ``repo``)."""

    repo: Repo | None
    root: Path

    def _r(self) -> Repo:
        assert self.repo is not None
        return self.repo

    def _prepare(self) -> None:
        """Make sure the repository exists before reading it (NoteHistory creates it)."""

    def _blobs(self, specs: list[str]) -> list[bytes | None]:
        """Contents of ``<rev>:<path>`` objects in one call; None for those that do not exist."""
        if not specs:
            return []
        r = self._r().run("cat-file", "--batch", input=("\n".join(specs) + "\n").encode("utf-8"))
        data = r.stdout
        out: list[bytes | None] = []
        pos = 0
        for _ in specs:
            nl = data.index(b"\n", pos)
            header = data[pos:nl].rsplit(b" ", 2)  # "<oid> <type> <size>", or "<spec> missing"
            pos = nl + 1
            if len(header) == 3 and header[2].isdigit():
                size = int(header[2])
                out.append(data[pos : pos + size] if header[1] == b"blob" else None)
                pos += size + 1
            else:
                out.append(None)
        return out

    def text_at(self, rev: str, path: str) -> str | None:
        self._prepare()
        got = self._blobs([f"{rev}:{path}"])[0]
        return got.decode("utf-8", "replace") if got is not None else None

    def _has_head(self) -> bool:
        return self._r().head() is not None

    def milestones(self) -> list[dict]:
        self._prepare()
        if not self._has_head():
            return []
        raw = self._r().out(
            "for-each-ref",
            "--sort=-creatordate",
            "--format=%(refname:short)%00%(contents:subject)%00%(creatordate:unix)%00%(*objectname)%00%(objectname)%00",
            f"refs/tags/{MILESTONE_PREFIX}*",
        )
        out = []
        for line in raw.splitlines():
            parts = line.split("\0")
            if len(parts) < 5:
                continue
            name, label, at, peeled, obj = parts[:5]
            ts = int(at) if at.isdigit() else 0
            out.append({"id": name, "label": label, "at": iso(ts), "commit": peeled or obj})
        return out

    def log(self, path: str | None = None, *, limit: int = 100, skip: int = 0) -> list[dict]:
        """Commits, newest first; with ``path``, those changing that note (following renames)."""
        self._prepare()
        if not self._has_head():
            return []
        args = [
            "log",
            RENAMES,
            "--name-status",
            "-z",
            f"--max-count={limit}",
            f"--skip={skip}",
            f"--format={LOG_FORMAT}",
        ]
        if path:
            args += ["--follow", "--", path]
        entries = parse_log(self._r().run(*args).stdout)
        # The state before each commit, so the app can tell edits that end where they began.
        trees = {e["id"]: e["tree"] for e in entries}
        missing = sorted({e["parents"][0] for e in entries if e["parents"] and e["parents"][0] not in trees})
        if missing:
            got = self._r().out("rev-parse", *(f"{p}^{{tree}}" for p in missing)).split()
            trees.update(zip(missing, got))
        for e in entries:
            e["parent_tree"] = trees.get(e["parents"][0]) if e["parents"] else None
        tags: dict[str, list[dict]] = {}
        for m in self.milestones():
            tags.setdefault(m["commit"], []).append({"id": m["id"], "label": m["label"]})
        for e in entries:
            e["milestones"] = tags.get(e["id"], [])
        return entries

    def note_log(self, path: str, *, limit: int = 200) -> list[dict]:
        """The versions of one note: each commit with the note's path before and after it and the
        hashes of both texts (the app compares them with the note it has open)."""
        entries = self.log(path, limit=limit)
        specs: list[str] = []
        for e in entries:
            f = next((x for x in e["files"]), None) or {"status": "M", "path": path}
            e["status"] = f["status"]
            e["path"] = f["path"]
            e["from_path"] = f.get("from") or f["path"]
            e.pop("files", None)
            specs.append(f"{e['id']}:{e['path']}")
            specs.append(f"{e['id']}^:{e['from_path']}" if e["parents"] else "")
        blobs = self._blobs([s or "0000000:missing" for s in specs])
        for k, e in enumerate(entries):
            after, before = blobs[2 * k], blobs[2 * k + 1]
            e["after_hash"] = sha256(after) if e["status"] != "D" else None
            e["before_hash"] = sha256(before) if e["status"] != "A" else None
        return entries

    def version(self, rev: str, path: str, from_path: str | None = None) -> dict:
        """A note's text after commit ``rev`` and before it (at its parent, under ``from_path``)."""
        self._prepare()
        after, before = self._blobs([f"{rev}:{path}", f"{rev}^:{from_path or path}"])
        dec = lambda b: b.decode("utf-8", "replace") if b is not None else None  # noqa: E731
        return {"id": rev, "path": path, "from_path": from_path or path, "after": dec(after), "before": dec(before)}

    def snapshot_tree(self) -> str:
        """The tree of the notes as they are on disk now, without committing (a scratch index)."""
        fd, idx = tempfile.mkstemp(prefix="index-", dir=str(self._r().git_dir))
        os.close(fd)
        os.unlink(idx)  # git makes it
        env = {"GIT_INDEX_FILE": idx}
        try:
            if self._has_head():
                self._r().run("read-tree", "HEAD", env=env)
            self._r().run("add", "-A", ".", env={**env, "GIT_LITERAL_PATHSPECS": "0"})
            return self._r().out("write-tree", env=env).strip()
        finally:
            Path(idx).unlink(missing_ok=True)

    def resolve(self, rev: str) -> str:
        """A commit id, milestone id or "now" (the notes on disk) as something git diff takes."""
        if rev == "now":
            return self.snapshot_tree()
        if rev.endswith("^") and len(rev) > 1:  # the state before a commit: nothing before the first
            base = self.resolve(rev[:-1])
            parent = self._r().run("rev-parse", "--verify", "-q", f"{base}^", ok=(0, 1)).stdout.decode().strip()
            return parent or self.empty_tree()
        if rev.startswith(MILESTONE_PREFIX):
            return self._r().out("rev-parse", "--verify", f"refs/tags/{rev}^{{commit}}").strip()
        if not all(c in "0123456789abcdef^~" for c in rev) or not rev:
            raise GitError(["rev-parse", rev], 128, "bad revision")
        return self._r().out("rev-parse", "--verify", f"{rev}^{{object}}").strip()

    def empty_tree(self) -> str:
        return self._r().out("hash-object", "-t", "tree", "--stdin", input=b"").strip()

    def diff(self, a: str, b: str, *, texts: bool = True) -> dict:
        """The notes that differ between two states (commit ids, milestone ids, "now", or ``<id>^``
        for the state before a commit), with both texts of each while the answer stays small."""
        self._prepare()
        ra = self.resolve(a)
        rb = self.resolve(b)
        raw = self._r().run("diff", RENAMES, "--name-status", "-z", ra, rb).stdout.decode("utf-8", "replace")
        fields = raw.split("\0")
        files: list[dict] = []
        k = 0
        while k < len(fields):
            st = fields[k]
            if not st:
                k += 1
                continue
            if st[0] in "RC":
                files.append({"status": st[0], "from": fields[k + 1], "path": fields[k + 2]})
                k += 3
            else:
                files.append({"status": st[0], "path": fields[k + 1]})
                k += 2
        files.sort(key=lambda f: f["path"].lower())
        cut = False
        if texts:
            shown = files[:MAX_DIFF_FILES]
            specs = []
            for f in shown:
                specs += [f"{ra}:{f.get('from') or f['path']}", f"{rb}:{f['path']}"]
            blobs = self._blobs(specs)
            used = 0
            for k, f in enumerate(shown):
                before, after = blobs[2 * k], blobs[2 * k + 1]
                used += len(before or b"") + len(after or b"")
                if used > MAX_DIFF_BYTES:
                    cut = True
                    break
                f["before"] = before.decode("utf-8", "replace") if before is not None and f["status"] != "A" else None
                f["after"] = after.decode("utf-8", "replace") if after is not None and f["status"] != "D" else None
            cut = cut or len(files) > MAX_DIFF_FILES
        return {"from": a, "to": b, "files": files, "cut": cut}
