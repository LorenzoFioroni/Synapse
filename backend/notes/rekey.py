"""Renaming a citation key in the notes.

When an item's key changes from ``old`` to ``new``, every note is updated:

* citations ``[@old]`` → ``[@new]`` (only real citations: never inside code, math, comments);
* ``paper: old`` in the front-matter of paper notes and cards;
* the paper note file ``papers/<old>.md`` → ``papers/<new>.md`` (only if it still has the
  automatic name and the new name is free), and ``[[old]]`` links to it.

``plan()`` computes everything without writing (the preview shown before a key changes);
``apply()`` first makes a backup of the notes folder, then writes each note with a
hash check, so a note edited at the same moment is never overwritten blindly.
"""

from __future__ import annotations

import logging
import tarfile
import time
from datetime import datetime
from pathlib import Path

from . import frontmatter, parser
from .vault import Conflict, NoteExists, slugify

log = logging.getLogger(__name__)

BACKUP_EVERY_S = 60  # one backup covers a burst of renames (e.g. "Regenerate all keys")
BACKUPS_KEPT = 20


class Rekeyer:
    def __init__(self, vault, index, backups_dir: Path):
        self.vault = vault
        self.index = index
        self.backups_dir = Path(backups_dir)
        self._last_backup = None  # not 0: time.monotonic() can itself be < 60 s just after boot

    # ------------------------------------------------------------------ backups
    def backup(self, force: bool = False) -> Path | None:
        """tar.gz of every note (attachments and trash excluded: renames never touch them)."""
        if not force and self._last_backup is not None and time.monotonic() - self._last_backup < BACKUP_EVERY_S:
            return None
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        dest = self.backups_dir / f"notes-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.tar.gz"
        with tarfile.open(dest, "w:gz") as tar:
            for rel, _ in self.vault.iter_notes():
                tar.add(self.vault.root / rel, arcname=f"notes/{rel}")
        self._last_backup = time.monotonic()
        old = sorted(self.backups_dir.glob("notes-*.tar.gz"))
        for p in old[:-BACKUPS_KEPT]:
            p.unlink(missing_ok=True)
        return dest

    # ------------------------------------------------------------------ planning
    def _paper_note_moves(self, old: str, new: str) -> dict[str, str]:
        moves = {}
        for n in self.index.paper_notes(old):
            path = n["path"]
            if path.lower() == f"papers/{slugify(old)}.md".lower():
                dst = f"papers/{slugify(new)}.md"
                if dst != path and not self.vault.exists(dst):
                    moves[path] = dst
        return moves

    def _affected_paths(self, old: str, moved: dict[str, str]) -> set[str]:
        paths = {c["path"] for c in self.index.citing(old)}
        paths |= {n["path"] for n in self.index.paper_notes(old)}
        for src in moved:
            paths |= {b["path"] for b in self.index.backlinks(src)}
        return paths

    def _edits_for(self, path: str, text: str, old: str, new: str, moved: dict[str, str]) -> tuple[str, list[dict]]:
        edits, changes = [], []
        lines = text.split("\n")
        for s in parser.link_spans(text):
            rep = None
            if s["kind"] == "cite" and s["target"] == old:
                rep = new
            elif s["kind"] in ("note", "embed") and moved:
                hit = (self.index.resolve(s["target"], from_path=path) or [None])[0]
                if hit in moved:
                    t = s["target"]
                    old_stem = Path(hit).stem
                    new_stem = Path(moved[hit]).stem
                    rep = t[: len(t) - len(old_stem)] + new_stem if t.lower().endswith(old_stem.lower()) else new_stem
            if rep is not None and rep != s["target"]:
                edits.append((s["start"], s["end"], rep))
        new_text = parser.replace_spans(text, edits)
        if str(frontmatter.read(new_text).get("paper") or "").strip() == old:
            new_text = frontmatter.set_fields(new_text, {"paper": new})
        if new_text != text:
            new_lines = new_text.split("\n")
            if len(new_lines) == len(lines):
                for i, (a, b) in enumerate(zip(lines, new_lines)):
                    if a != b:
                        changes.append({"line": i + 1, "before": a.rstrip("\r"), "after": b.rstrip("\r")})
            else:  # front-matter grew (cannot happen today, but stay correct)
                changes.append({"line": 1, "before": "(front-matter)", "after": "(front-matter updated)"})
        return new_text, changes

    def plan(self, old: str, new: str) -> dict:
        """What renaming ``old`` → ``new`` would change. Writes nothing."""
        old, new = (old or "").strip(), (new or "").strip()
        if not old or not new or old == new:
            return {"old": old, "new": new, "notes": [], "moves": [], "count": 0}
        moved = self._paper_note_moves(old, new)
        notes = []
        for path in sorted(self._affected_paths(old, moved)):
            try:
                text, _ = self.vault.read(path)
            except Exception:
                continue
            _, changes = self._edits_for(path, text, old, new, moved)
            if changes:
                info = self.index.note(path) or {}
                notes.append({"path": path, "title": info.get("title") or Path(path).stem, "changes": changes})
        return {
            "old": old,
            "new": new,
            "notes": notes,
            "count": sum(len(n["changes"]) for n in notes),
            "moves": [{"from": a, "to": b} for a, b in moved.items()],
        }

    # ------------------------------------------------------------------ applying
    def apply(self, old: str, new: str) -> dict:
        p = self.plan(old, new)
        if not p["notes"] and not p["moves"]:
            return {**p, "backup": None}
        backup = self.backup()
        moved = {m["from"]: m["to"] for m in p["moves"]}
        written = []
        for n in p["notes"]:
            path = n["path"]
            for attempt in range(3):
                text, h = self.vault.read(path)
                new_text, _ = self._edits_for(path, text, old, new, moved)
                if new_text == text:
                    break
                try:
                    self.vault.write(path, new_text, expected_hash=h)
                    written.append(path)
                    break
                except Conflict:
                    if attempt == 2:
                        log.warning("Could not update %s after a key rename (kept changing)", path)
            self.index.update(path)
        for src, dst in moved.items():
            try:
                self.vault.move(src, dst)
                self.index.remove(src)
                self.index.update(dst)
            except (NoteExists, Exception):
                log.exception("Could not rename %s to %s", src, dst)
        return {**p, "backup": str(backup) if backup else None, "written": written}
