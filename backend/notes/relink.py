"""Keeping [[links]] working when notes are moved or renamed.

A link by name (``[[Target]]``) survives a move to another folder, but a link by path
(``[[proj/Target]]``), a link to a note that was renamed, and a link that now resolves to a
different note with the same name do not. ``Relinker`` rewrites exactly those, in the notes that
hold them, as part of the move:

1. ``plan()`` runs *before* the move, while the index still describes the old paths. It records,
   for each note holding such a link, which targets point at which moved note.
2. ``apply()`` runs *after* the move and the re-indexing. It gives each of those targets the
   shortest form that resolves to the moved note again, in the form the link had (a name stays a
   name, a path stays a path, ``#heading`` and ``|alias`` are not touched), and writes the note
   with a hash check, like a citation-key rename (rekey.py).

Only link targets are edited, found with ``parser.link_spans``: code, math, comments and
front matter are never touched. A note that cannot be rewritten (edited at the same moment, or
no backup could be made) keeps its links and is reported, so the user is told which ones broke.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from . import parser
from .index import norm_target
from .vault import Conflict

log = logging.getLogger(__name__)
ATTEMPTS = 3


@dataclass
class Relink:
    """What one move needs from other notes: ``targets`` maps a normalised link target to the
    moved note (its old path) it pointed at, per note that holds links (old path of that note)."""

    moves: dict[str, str]
    targets: dict[str, dict[str, str]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.targets)

    @property
    def sources(self) -> list[str]:
        return sorted(self.targets)


class Relinker:
    def __init__(self, vault, index, backup: Callable[[], object] | None = None):
        self.vault = vault
        self.index = index
        self._backup = backup  # Rekeyer.backup: a tar.gz of the notes, at most one a minute

    # ------------------------------------------------------------------ before the move
    def plan(self, moves: dict[str, str]) -> Relink:
        """The links that point at the notes in ``moves`` ({old path: new path}), as they resolve now."""
        out = Relink(dict(moves))
        holders: set[str] = set()
        for old in moves:
            holders.add(old)  # a note may link to itself
            holders |= {b["path"] for b in self.index.backlinks(old)}
        for path in sorted(holders):
            try:
                text, _ = self.vault.read(path)
            except Exception:
                continue  # gone or unreadable: nothing to rewrite
            found: dict[str, str] = {}
            for s in parser.link_spans(text):
                if s["kind"] not in ("note", "embed"):
                    continue
                hit = next(iter(self.index.resolve(s["target"], from_path=path)), None)
                if hit in moves:
                    found[norm_target(s["target"])] = hit
            if found:
                out.targets[path] = found
        return out

    # ------------------------------------------------------------------ after the move
    def apply(self, plan: Relink) -> tuple[list[dict], list[dict]]:
        """Rewrite what the move broke. Returns ``(updated, unfixed)``: ``updated`` is
        ``[{"path", "count"}]`` (current paths of the rewritten notes), ``unfixed`` the links
        left as they were: ``[{"path", "target", "line"}]``."""
        updated: list[dict] = []
        unfixed: list[dict] = []
        todo = {p: t for p, t in plan.targets.items() if self._needs_fix(p, t, plan.moves)}
        if not todo:
            return updated, unfixed
        backed_up = True
        try:
            if self._backup:
                self._backup()
        except OSError:
            log.exception("Could not back up the notes; links to moved notes are left as they are")
            backed_up = False
        for old_path, targets in todo.items():
            path = plan.moves.get(old_path, old_path)
            count = self._rewrite(path, targets, plan.moves) if backed_up else None
            if count:
                updated.append({"path": path, "count": count})
            elif count is None:
                unfixed.extend(self._leftovers(path, targets, plan.moves))
        return updated, unfixed

    def _needs_fix(self, old_path: str, targets: dict[str, str], moves: dict[str, str]) -> bool:
        path = moves.get(old_path, old_path)
        return any(self._now(t, path) != moves[old] for t, old in targets.items())

    def _now(self, target: str, path: str) -> str | None:
        return next(iter(self.index.resolve(target, from_path=path)), None)

    def _rewrite(self, path: str, targets: dict[str, str], moves: dict[str, str]) -> int | None:
        """Rewrite ``path``; the number of links changed, or None when the note kept changing."""
        for _ in range(ATTEMPTS):
            try:
                text, h = self.vault.read(path)
            except Exception:
                return None
            edits = []
            for s in parser.link_spans(text):
                old = targets.get(norm_target(s["target"])) if s["kind"] in ("note", "embed") else None
                if old is None or self._now(s["target"], path) == moves[old]:
                    continue
                edits.append((s["start"], s["end"], self.target_for(s["target"], old, moves[old])))
            if not edits:
                return 0
            try:
                self.vault.write(path, parser.replace_spans(text, edits), expected_hash=h)
            except Conflict:
                continue
            self.index.update(path)
            return len(edits)
        log.warning("Could not update the links in %s after a move (it kept changing)", path)
        return None

    def _leftovers(self, path: str, targets: dict[str, str], moves: dict[str, str]) -> list[dict]:
        try:
            text, _ = self.vault.read(path)
        except Exception:
            return []
        out = []
        for s in parser.link_spans(text):
            old = targets.get(norm_target(s["target"])) if s["kind"] in ("note", "embed") else None
            if old is not None and self._now(s["target"], path) != moves[old]:
                out.append({"path": path, "target": s["target"], "line": text.count("\n", 0, s["start"]) + 1})
        return out

    def target_for(self, target: str, old: str, new: str) -> str:
        """The link target that reaches ``new`` in the form ``target`` had when it reached ``old``:
        the full path stays a full path, a name or a partial path keeps its number of folders as
        far as that resolves to ``new``, and gains folders when it would not."""
        segments = new[:-3].split("/") if new.lower().endswith(".md") else new.split("/")
        t = norm_target(target)
        if "/" in t and t == norm_target(old):
            count = len(segments)
        else:
            count = min(t.count("/") + 1, len(segments))
        suffix = ".md" if target.strip().lower().endswith(".md") else ""
        for n in range(count, len(segments) + 1):
            candidate = "/".join(segments[-n:])
            if self._now(candidate, "") == new:
                return candidate + suffix
        return "/".join(segments) + suffix
