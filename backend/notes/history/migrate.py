"""Bringing the history kept before Git (one JSON file per AI change, ``notes/.history/``) into a
new Git history, as commits dated when the changes were made.

For each note the JSON files hold the text before every AI change and a hash of the text after it.
That gives, in time order across all notes:

* the note before its first AI change ("Earlier history"),
* each AI change, when the text it left is known: it is the text before the next change (same
  hash), or the note now,
* otherwise one commit for the AI change and whatever the user did after it, up to the next
  change, saying the AI's own version was not kept.

The commits are written with ``git fast-import`` (one call, no work tree involved); the note as it
is now then follows in the "Start of the history" commit. The JSON files are left where they are.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path

from .git import Repo
from .legacy import LegacyHistory

log = logging.getLogger(__name__)
BRANCH = "refs/heads/main"


def _when(at: str) -> tuple[int, str]:
    """(unix time, "+0200") of an ISO time."""
    try:
        d = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        d = datetime.now().astimezone()
    if d.tzinfo is None:
        d = d.astimezone()
    off = d.utcoffset()
    mins = int(off.total_seconds() // 60) if off else 0
    sign = "+" if mins >= 0 else "-"
    return int(d.timestamp()), f"{sign}{abs(mins) // 60:02d}{abs(mins) % 60:02d}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def events(legacy: LegacyHistory, root: Path, valid) -> list[dict]:
    """The commits to make, oldest first: {ts, tz, path, text, by, kind, agent, summary, body}."""
    by_path: dict[str, list[dict]] = {}
    for _, e in legacy._all():
        p = e.get("path")
        if isinstance(p, str) and isinstance(e.get("before"), str) and valid(p):
            by_path.setdefault(p, []).append(e)
    out: list[dict] = []
    for path, es in by_path.items():
        es.sort(key=lambda e: str(e.get("id")))
        try:
            now_text: str | None = (root / path).read_bytes().decode("utf-8", "replace")
        except OSError:
            now_text = None
        for k, e in enumerate(es):
            ts, tz = _when(e.get("at") or "")
            agent = e.get("agent") or "an AI app"
            summary = (e.get("summary") or "").strip() or f"Edited by {agent}"
            if k == 0:
                out.append(
                    {"ts": ts - 1, "tz": tz, "path": path, "text": e["before"], "by": "human", "kind": "earlier",
                     "summary": "Earlier history", "body": "The note before the first change recorded for it."}
                )  # fmt: skip
            nxt = es[k + 1] if k + 1 < len(es) else None
            after: str | None = None
            if nxt is not None and nxt.get("before_hash") == e.get("after_hash"):
                after = nxt["before"]
            elif nxt is None and now_text is not None and _sha(now_text) == e.get("after_hash"):
                after = now_text
            if after is not None:
                out.append(
                    {"ts": ts, "tz": tz, "path": path, "text": after, "by": "ai", "kind": "ai",
                     "agent": e.get("agent"), "summary": summary, "body": ""}
                )  # fmt: skip
            elif nxt is not None:
                nts, ntz = _when(nxt.get("at") or "")
                out.append(
                    {"ts": max(ts, nts - 1), "tz": ntz, "path": path, "text": nxt["before"], "by": "human",
                     "kind": "earlier", "agent": e.get("agent"),
                     "summary": f"{agent}'s change “{summary}” and the edits after it",
                     "body": "The exact version the AI app left was not kept by earlier versions of Synapse."}
                )  # fmt: skip
    out.sort(key=lambda x: x["ts"])
    return out


def _data(b: bytes) -> bytes:
    return b"data %d\n" % len(b) + b + b"\n"


def import_legacy(repo: Repo, legacy: LegacyHistory, root: Path, valid, message) -> int:
    """Write the old changes as commits on the (empty) main branch. Returns how many."""
    evs = events(legacy, root, valid)
    if not evs:
        return 0
    stream = bytearray()
    for k, ev in enumerate(evs):
        name, email = (
            (ev.get("agent") or "AI app", "ai@synapse.local")
            if ev["by"] == "ai"
            else ("Earlier history", "you@synapse.local")
        )
        msg = message(
            ev["summary"],
            body=ev["body"],
            by=ev["by"],
            kind=ev["kind"],
            agent=ev.get("agent") if ev["by"] == "ai" else None,
            device=None,
        ).encode("utf-8")
        who = f"{name.replace('<', '').replace('>', '')} <{email}> {ev['ts']} {ev['tz']}".encode()
        stream += f"commit {BRANCH}\nmark :{k + 1}\n".encode()
        stream += b"author " + who + b"\ncommitter " + who + b"\n"
        stream += _data(msg)
        stream += b"M 100644 inline " + ev["path"].encode("utf-8") + b"\n"
        stream += _data(ev["text"].encode("utf-8"))
        stream += b"\n"
    stream += b"done\n"
    repo.run("fast-import", "--quiet", "--done", input=bytes(stream), timeout=600)
    log.info("Notes history: brought in %d earlier changes", len(evs))
    return len(evs)
