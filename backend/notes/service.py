"""Note operations used by the HTTP API (and later by the MCP server).

Keeps the vault (files) and the index (derived data) in step: every change goes to the file
first and the index is updated from the file afterwards.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..library.agent import current_agent, current_device
from . import frontmatter
from .folder_colors import FolderColorError, FolderColors
from .history import NoteHistory
from .history.reading import MILESTONE_PREFIX
from .index import NotesIndex
from .rekey import Rekeyer
from .relink import Relink, Relinker
from .vault import ATTACHMENTS, Conflict, NoteExists, Vault, slugify

log = logging.getLogger(__name__)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
IMAGE_TYPES = {  # extension -> magic-byte check. No SVG: it can carry scripts.
    "png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
    "jpg": lambda b: b.startswith(b"\xff\xd8\xff"),
    "gif": lambda b: b.startswith((b"GIF87a", b"GIF89a")),
    "webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
}
MAX_IMAGE_BYTES = 25 * 1024 * 1024


def ulid() -> str:
    """Sortable unique id (26 chars, Crockford base32): 48-bit ms time + 80 random bits."""
    n = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 31])
        n >>= 5
    return "".join(reversed(out))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sniff_image(data: bytes) -> str | None:
    for ext, check in IMAGE_TYPES.items():
        if check(data):
            return ext
    return None


class NoteTextMatcher:
    """Lets the library search find items through what the notes say about them.

    ``match(term)`` returns {item_id: snippet} like the full-text index does, so search.py can
    treat it as one more text source.
    """

    def __init__(self, texts: dict[str, list[tuple[str, str]]], key_to_id: dict[str, str]):
        from ..library.search import normalize

        self._normalize = normalize
        self._entries = []  # (item_id, title, original text, normalised text)
        for key, items in texts.items():
            pid = key_to_id.get(key)
            if pid:
                for title, text in items:
                    self._entries.append((pid, title, text, normalize(text)))

    def match(self, term: str) -> dict[str, str]:
        import html
        import re

        t = self._normalize(term)
        out: dict[str, str] = {}
        if not t:
            return out
        for pid, title, text, norm in self._entries:
            if pid in out or t not in norm:
                continue
            flat = " ".join(text.split())
            m = re.search(re.escape(term.strip()), flat, re.I)
            if m:  # the term as typed: show it highlighted in context
                a = max(0, m.start() - 60)
                piece = (
                    html.escape(flat[a : m.start()])
                    + "<mark>"
                    + html.escape(m.group(0))
                    + "</mark>"
                    + html.escape(flat[m.end() : m.end() + 80])
                )
            else:  # matched only after removing accents/case: show the start of the text
                a = 0
                piece = html.escape(flat[:140])
            out[pid] = f'<span class="hit-src">Note · {html.escape(title)}</span> {"…" if a else ""}{piece}…'
        return out


class NotesService:
    def __init__(self, vault: Vault, index: NotesIndex, papers, backups_dir=None, history_dir=None):
        """``backups_dir``: where key renames back up the notes (a path, or a function giving it);
        ``history_dir``: the Git directory of the notes history (default: next to the notes)."""
        self.vault = vault
        self.index = index
        self.papers = papers
        default = vault.root.parent / "backups"  # data/backups
        self.rekeyer = Rekeyer(vault, index, backups_dir or default, fallback_dir=default)
        self.relinker = Relinker(vault, index, self.rekeyer.backup)  # links of moved notes
        self.on_change: Callable[[], None] | None = None  # called after notes are saved, deleted or restored
        # called with (path, hash, by) after a note is written: by is "app" or "ai" (plugins hear it)
        self.saved_listeners: list[Callable[[str, str, str], None]] = []
        # every version of the notes' text (Git; see history/recorder.py)
        self.history = NoteHistory(
            vault.root, history_dir or vault.root.parent / "notes-history.git", valid_path=self._valid_path
        )
        self.folder_colors = FolderColors(vault.root)  # colours given to notes folders

    # ------------------------------------------------------------------ citation keys
    def text_matcher(self) -> NoteTextMatcher:
        """For the library search (cached until the notes or the keys change)."""
        keys = {p["citation_key"]: p["id"] for p in self.papers.list() if p.get("citation_key")}
        sig = (self.index.signature(), tuple(sorted(keys.items())))
        if getattr(self, "_matcher_sig", None) != sig:
            self._matcher = NoteTextMatcher(self.index.texts_by_key(), keys)
            self._matcher_sig = sig
        return self._matcher

    def rename_plan(self, old: str, new: str) -> dict:
        return self.rekeyer.plan(old, new)

    def _valid_path(self, rel: str) -> bool:
        try:
            self.vault.clean_rel(rel)
            return True
        except Exception:
            return False

    def rename_key(self, old: str, new: str) -> dict:
        with self.history.change(kind="bulk", subject=f"Citation key {old} became {new}", device=current_device()):
            res = self.rekeyer.apply(old, new)
        self.history.legacy.moved([(m["from"], m["to"]) for m in res.get("moves") or []])
        return res

    # ------------------------------------------------------------------ helpers
    def _library_keys(self) -> dict[str, dict]:
        """citation_key -> compact item info, for resolving [@key] (includes trashed items)."""
        out = {}
        notes = self.index.paper_note_map()
        for view in ("library", "trash"):
            for p in self.papers.list(view):
                k = p.get("citation_key")
                if k and k not in out:
                    pdfs = [a for a in p.get("attachments") or [] if (a.get("type") or "") == "pdf"]
                    pdf = pdfs[0] if pdfs else None  # the default PDF of a citation without att:<id>
                    out[k] = {
                        "id": p["id"],
                        "title": p.get("title") or "",
                        "authors": p.get("authors") or "",
                        "year": p.get("year"),
                        "journal": p.get("journal") or "",
                        "trashed": view == "trash",
                        "has_pdf": pdf is not None,
                        "pdf": {"id": pdf["id"], "url": pdf["url"], "name": pdf.get("name") or ""} if pdf else None,
                        "pdfs": [{"id": a["id"], "url": a["url"], "name": a.get("name") or ""} for a in pdfs],
                        "note": notes.get(k),
                    }
        return out

    def cite_index(self) -> list[dict]:
        """Every item with a citation key (for autocomplete and citation cards), trashed ones flagged."""
        return [{"key": k, **v} for k, v in sorted(self._library_keys().items())]

    def _unique_path(self, rel: str) -> str:
        rel = self.vault.clean_rel(rel)
        if not self.vault.exists(rel):
            return rel
        base = rel[:-3]
        n = 2
        while self.vault.exists(f"{base} {n}.md"):
            n += 1
        return f"{base} {n}.md"

    # ------------------------------------------------------------------ reads
    def overview(self) -> dict:
        folders = self.vault.folders()
        shown = set(folders)
        colors = {f: c for f, c in self.folder_colors.all().items() if f in shown}
        return {"notes": self.index.list(), "folders": folders, "folder_colors": colors, "stats": self.index.stats()}

    def set_folder_color(self, path: str, color: str | None) -> dict:
        """Give a notes folder a colour (``#rrggbb``) or none; answers all the folders' colours."""
        folder = self.vault.clean_rel(path, suffix=None)
        if folder not in set(self.vault.folders()):
            raise FolderColorError(f"No folder {folder}")
        self.folder_colors.set(folder, color)
        return {"folder_colors": self.overview()["folder_colors"]}

    def get(self, path: str, unless: str | None = None) -> dict:
        """The note's text, hash and index entry. With ``unless`` (the hash the caller already
        has), an unchanged note answers with its hash only: open windows ask every few seconds
        whether their note changed elsewhere, and long notes would otherwise be sent each time."""
        path = self.vault.clean_rel(path)
        text, h = self.vault.read(path)
        if unless and unless == h:
            return {"path": path, "hash": h, "unchanged": True}
        info = self.index.note(path)
        if info is None or info["hash"] != h:  # edited outside the app since the last sync
            self.index.update(path)
            info = self.index.note(path)
        return {"path": path, "text": text, "hash": h, "info": info}

    def links(self, path: str) -> dict:
        path = self.vault.clean_rel(path)
        out = self.index.outgoing(path)
        keys = self._library_keys()
        cited: dict[str, dict] = {}
        for l in out:
            if l["kind"] == "cite":
                item = keys.get(l["target"])
                l["item"] = item
                entry = cited.setdefault(l["target"], {"key": l["target"], "item": item, "count": 0, "lines": []})
                entry["count"] += 1
                entry["lines"].append(l["line"])
        return {
            "outgoing": out,
            "cited": list(cited.values()),
            "unresolved": [
                l
                for l in out
                if (l["kind"] in ("note", "embed") and not l["resolved"]) or (l["kind"] == "cite" and not l["item"])
            ],
            "backlinks": self.index.backlinks(path),
        }

    def item_notes(self, item_id: str) -> dict:
        item = self.papers.get(item_id)
        key = item.get("citation_key") or ""
        if not key:
            return {"key": "", "paper_notes": [], "citing": []}
        return {
            "key": key,
            "paper_notes": self.index.paper_notes(key),
            "citing": self.index.citing(key),
            "suggested_path": f"papers/{slugify(key)}.md",
        }

    def search(self, q: str) -> list[dict]:
        return self.index.search(q)

    # ------------------------------------------------------------------ writes
    def create(
        self,
        *,
        path: str | None = None,
        folder: str = "",
        title: str = "",
        item_id: str | None = None,
        text: str | None = None,
    ) -> dict:
        meta = {"id": ulid(), "title": title or None, "type": "note", "author": "human", "created": now_iso()}
        if item_id:
            item = self.papers.get(item_id)
            key = item.get("citation_key")
            if not key:
                raise ValueError("This item has no citation key yet.")
            existing = self.index.paper_notes(key)
            if existing:
                raise NoteExists(existing[0]["path"])
            meta.update(type="paper", paper=key, title=title or item.get("title") or key)
            path = path or f"papers/{slugify(key)}.md"
            body = f"# {meta['title']}\n\n[@{key}]\n\n"
        else:
            body = f"# {title}\n\n" if title else ""
        if not path:
            name = slugify(title) if title else "Untitled"
            path = self._unique_path(f"{folder.strip('/')}/{name}.md" if folder.strip("/") else f"{name}.md")
        content = frontmatter.set_fields(text if text is not None else body, meta)
        path = self.vault.clean_rel(path)
        with self.history.saving([path], device=current_device()):
            h = self.vault.write(path, content, expected_hash=None)
        self.index.update(path)
        self._saved(path, h, "app")
        return {"path": path, "text": content, "hash": h}

    def _saved(self, path: str, h: str, by: str) -> None:
        for fn in list(self.saved_listeners):
            try:
                fn(path, h, by)
            except Exception:  # the note is saved; a listener must not undo that
                log.exception("note listener failed for %s", path)

    def _changed(self) -> None:
        """Notes were saved, deleted or restored: attachments may have become (un)used."""
        if self.on_change:
            self.on_change()

    def save(self, path: str, text: str, base_hash: str | None, *, by: str = "app", summary: str = "") -> dict:
        """Save over the version with ``base_hash``; ``None`` creates (fails if the file exists).
        ``by="ai"``: a note an AI app wrote, recorded in the history as its own change."""
        path = self.vault.clean_rel(path)
        if by == "ai":
            agent = current_agent()
            with self.history.change(
                kind="ai", by="ai", agent=agent, paths=[path], subject=summary or f"Wrote {Path(path).stem}"
            ):
                h = self.vault.write(path, text, expected_hash=base_hash)
        else:
            with self.history.saving([path], device=current_device()):
                h = self.vault.write(path, text, expected_hash=base_hash)
        self.index.update(path)
        self._changed()
        self._saved(path, h, by)
        return {"path": path, "hash": h, "info": self.index.note(path)}

    def edit(self, path: str, text: str, base_hash: str, *, by: str = "ai", summary: str = "") -> dict:
        """Save a change made for someone else than the editor (an AI app), keeping the version it
        replaces in the history so the app can show the change, who made it, and undo it."""
        path = self.vault.clean_rel(path)
        agent = current_agent() if by == "ai" else None
        with self.history.change(
            kind="ai" if by == "ai" else "edit",
            by="ai" if by == "ai" else "human",
            agent=agent,
            device=current_device(),
            paths=[path],
            subject=summary or (f"Edited by {agent}" if agent else "Edited"),
        ) as rec:
            before, h = self.vault.read(path)
            if h != base_hash:
                raise Conflict(path, h)
            h2 = self.vault.write(path, text, expected_hash=base_hash)
        version = rec.commit
        if not self.history.enabled:  # without Git the change is still kept, the old way
            version = self.history.legacy.record(
                path, before=before, before_hash=h, after_hash=h2, by=by, agent=agent, summary=summary
            )["id"]
        self.index.update(path)
        self._changed()
        self._saved(path, h2, by)
        return {"path": path, "hash": h2, "version": version}

    # ------------------------------------------------------------------ history
    def history_status(self) -> dict:
        return self.history.status()

    def history_of(self, path: str, limit: int = 200) -> dict:
        """A note's versions, newest first (following renames): who changed it, when and how,
        with the hashes of the text before and after each change; ``current`` marks the version
        the note on disk still is. Without Git: the AI changes kept the old way."""
        path = self.vault.clean_rel(path)
        h = self.vault.current_hash(path)
        limit = max(1, min(limit, 1000))
        if self.history.enabled:
            entries = self.history.note_log(path, limit=limit)
        else:
            entries = self.history.legacy.list(path, limit=limit)
        for e in entries:
            e["current"] = e.get("after_hash") == h
        return {"path": path, "hash": h, "enabled": self.history.enabled, "entries": entries}

    def history_version(self, entry_id: str, path: str | None = None, from_path: str | None = None) -> dict:
        """One version: the note's text after the change ``entry_id`` and before it."""
        if not self.history.enabled or re.fullmatch(r"[0-9A-Z]{26}", entry_id or ""):
            e = self.history.legacy.get(entry_id)
            if not e:
                raise KeyError(entry_id)
            return e
        if not re.fullmatch(r"[0-9a-f]{7,64}", entry_id or "") or not path:
            raise KeyError(entry_id)
        path = self.vault.clean_rel(path)
        from_path = self.vault.clean_rel(from_path) if from_path else path
        v = self.history.version(entry_id, path, from_path)
        if v["after"] is None and v["before"] is None:
            raise KeyError(entry_id)
        return v

    def history_log(self, limit: int = 100, skip: int = 0) -> dict:
        """Every recorded change to the notes, newest first, with the notes each touched."""
        if not self.history.enabled:
            return {**self.history.status(), "entries": [], "milestones": []}
        self.history.checkpoint()
        return {
            **self.history.status(),
            "entries": self.history.log(limit=max(1, min(limit, 500)), skip=max(0, skip)),
            "milestones": self.history.milestones(),
        }

    def history_diff(self, a: str, b: str) -> dict:
        if not self.history.enabled:
            raise ValueError(self.history.reason or "The notes history is off.")
        if b == "now":
            self.history.checkpoint()
        return self.history.diff(a, b)

    def checkpoint(self, path: str | None = None, restored_from: str | None = None) -> dict:
        """Commit now what is waiting (the app calls it when a note is closed, and after putting
        an earlier version back, with the version it came from)."""
        paths = [self.vault.clean_rel(path)] if path else None
        if restored_from is not None and not re.fullmatch(r"[0-9a-f]{7,64}", restored_from):
            raise ValueError("Bad version")
        return {"commits": self.history.checkpoint(paths, restored=restored_from)}

    def add_milestone(self, label: str) -> dict:
        return self.history.add_milestone(label)

    def rename_milestone(self, mid: str, label: str) -> dict:
        return self.history.rename_milestone(mid, label)

    def delete_milestone(self, mid: str) -> dict:
        self.history.delete_milestone(mid)
        return {"deleted": mid}

    def restore_milestone(self, mid: str) -> dict:
        """Put every note back as it was at a milestone: changed notes get their text back, notes
        deleted since come back, notes made since go to the notes trash (one Undo brings them
        back). One commit; the history keeps everything in between."""
        if not re.fullmatch(rf"{MILESTONE_PREFIX}[0-9\-]+", mid or ""):
            raise ValueError("No such milestone.")
        m = next((x for x in self.history.milestones() if x["id"] == mid), None)
        if m is None:
            raise ValueError("No such milestone.")
        self.history.checkpoint()
        with self.history.change(
            kind="restore", subject=f"Restored the milestone “{m['label']}”", device=current_device()
        ) as rec:
            d = self.history.diff("now", mid, texts=False)
            back: list[str] = []
            gone: list[str] = []
            for f in d["files"]:
                if f["status"] == "R":
                    gone.append(f["from"])
                if f["status"] in ("M", "A", "R"):
                    text = self.history.text_at(mid, f["path"])
                    if text is not None and self._valid_path(f["path"]):
                        self.vault._atomic_write(self.vault.root / f["path"], text.encode("utf-8"))
                        back.append(f["path"])
                elif f["status"] == "D":
                    gone.append(f["path"])
            gone = [p for p in gone if self.vault.exists(p) and p not in back]
            trash_id = self.vault.trash_batch(gone, [])[0] if gone else None
        for p in gone:
            self.index.remove(p)
        self._reindex(back)
        self._changed()
        return {"restored": back, "trashed": gone, "trash_id": trash_id, "commit": rec.commit}

    def _link_plan(self, moves: dict[str, str]) -> Relink:
        """The links the move would break (a move must not fail because of them)."""
        try:
            return self.relinker.plan(moves)
        except Exception:
            log.exception("Could not look for links to the moved notes")
            return Relink(moves)

    def _fix_links(self, plan: Relink, rec) -> dict:
        """After a move and its re-indexing: rewrite the links it broke, record the notes in the
        history entry and report ``updated_links`` and the ``broken_links`` left."""
        try:
            updated, unfixed = self.relinker.apply(plan)
        except Exception:
            log.exception("Could not update the links to the moved notes")
            updated, unfixed = [], [{"path": p, "target": "", "line": 0} for p in plan.sources]
        if updated:
            rec.paths = [*(rec.paths or []), *(u["path"] for u in updated)]
            n = sum(u["count"] for u in updated)
            rec.body = (
                f"Updated {n} link{'s' if n != 1 else ''} in {len(updated)} note{'s' if len(updated) != 1 else ''}."
            )
        return {"updated_links": updated, "broken_links": unfixed}

    def move(self, path: str, to: str, base_hash: str | None = None) -> dict:
        src = self.vault.clean_rel(path)
        dst = self.vault.clean_rel(to)
        moved = Path(src).parent != Path(dst).parent
        plan = self._link_plan({src: dst}) if src != dst else Relink({})
        with self.history.change(
            kind="rename",
            subject=f"{'Moved' if moved else 'Renamed'} {Path(src).stem} to {dst if moved else Path(dst).stem}",
            device=current_device(),
            paths=[src, dst, *plan.sources],  # notes with links to fix: what waits for them is saved first
        ) as rec:
            self.vault.move(src, dst, expected_hash=base_hash)
            self.index.remove(src)
            self.index.update(dst)
            rec.paths = [src, dst]
            fixed = self._fix_links(plan, rec)
        self.history.legacy.moved([(src, dst)])
        if fixed["updated_links"]:
            self._changed()
        return {"path": dst, **fixed}

    def make_folder(self, path: str) -> dict:
        return {"path": self.vault.make_folder(path)}

    def trash(self, path: str) -> dict:
        path = self.vault.clean_rel(path)
        with self.history.change(
            kind="delete", subject=f"Deleted {Path(path).stem}", device=current_device(), paths=[path]
        ):
            tid = self.vault.trash(path)
        self.index.remove(path)
        self._changed()
        return {"trash_id": tid, "path": path}

    def trash_many(self, paths: list[str], folders: list[str]) -> dict:
        inside = [n for f in folders or [] for n in self._notes_in_quietly(f)]
        notes = [self.vault.clean_rel(p) for p in paths or []] + inside
        subject = f"Deleted {Path(notes[0]).stem}" if len(notes) == 1 else f"Deleted {len(notes)} notes"
        with self.history.change(kind="delete", subject=subject, device=current_device(), paths=notes):
            tid, moved = self.vault.trash_batch(paths or [], folders or [])
        for p in moved:
            self.index.remove(p)
        self._changed()
        return {"trash_id": tid, "paths": moved}

    def _reindex(self, paths) -> None:
        """Index notes that just appeared; one unreadable file must not stop the others."""
        for p in paths:
            try:
                self.index.update(p)
            except Exception:
                log.exception("Could not index %s", p)

    def _notes_in_quietly(self, folder: str) -> list[str]:
        try:
            return self.vault.notes_in(folder)
        except Exception:  # the vault reports a bad folder itself
            return []

    def restore_batch(self, trash_id: str) -> dict:
        with self.history.change(kind="restore", subject="Restored from the trash", device=current_device()) as rec:
            restored, skipped = self.vault.restore_batch(trash_id)
            rec.paths = restored
        self._reindex(restored)
        self._changed()
        return {"paths": restored, "skipped": skipped}

    def move_folder(self, path: str, to: str) -> dict:
        src = self.vault.clean_rel(path, suffix=None)
        dst = self.vault.clean_rel(to, suffix=None)
        inside = self.vault.notes_in(src)
        plan = self._link_plan({n: dst + n[len(src) :] for n in inside}) if dst != src else Relink({})
        with self.history.change(
            kind="rename",
            subject=f"Moved the folder {src} to {to}",
            device=current_device(),
            paths=[*inside, *plan.sources],
        ) as rec:
            moved = self.vault.move_folder(src, to)
            self.history.legacy.moved(moved)
            self.folder_colors.moved(src, dst)
            for old, _ in moved:
                self.index.remove(old)
            self._reindex(new for _, new in moved)
            rec.paths = [p for pair in moved for p in pair]
            fixed = self._fix_links(plan, rec)
        if fixed["updated_links"]:
            self._changed()
        return {"path": dst, "moved": [{"from": o, "to": n} for o, n in moved], **fixed}

    def key_summary(self) -> dict:
        return self.index.key_summary()

    def restore(self, trash_id: str, path: str) -> dict:
        with self.history.change(
            kind="restore", subject=f"Restored {Path(path).stem} from the trash", device=current_device()
        ) as rec:
            path = self.vault.restore(trash_id, path)
            rec.paths = [path]
        self.index.update(path)
        self._changed()
        return {"path": path}

    def add_image(self, filename: str, data: bytes) -> dict:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Image is larger than 25 MB.")
        ext = sniff_image(data)
        if not ext:
            raise ValueError("Only PNG, JPEG, GIF and WebP images can be added.")
        stem = slugify(os.path.splitext(os.path.basename(filename or ""))[0], "image").replace(" ", "-")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rel = self.vault.write_bytes(f"{ATTACHMENTS}/{stamp}-{stem}.{ext}", data)
        return {"path": rel, "markdown": f"![]({rel})"}

    def append_to_paper_note(self, item_id: str, markdown: str) -> dict:
        """Append a block (a quote, a figure) to the item's paper note, creating the note if needed."""
        markdown = (markdown or "").strip("\n")
        if not markdown.strip():
            raise ValueError("Nothing to add.")
        item = self.papers.get(item_id)
        key = item.get("citation_key")
        if not key:
            raise ValueError("This item has no citation key yet.")
        existing = self.index.paper_notes(key)
        path = existing[0]["path"] if existing else self.create(item_id=item_id)["path"]
        for _ in range(3):  # someone may be saving the same note right now: re-read and retry
            text, h = self.vault.read(path)
            nl = "\r\n" if "\r\n" in text else "\n"
            new = text.rstrip("\r\n") + nl + nl + markdown.replace("\n", nl) + nl
            try:
                with self.history.saving([path], device=current_device()):
                    h2 = self.vault.write(path, new, expected_hash=h)
                break
            except Conflict:
                continue
        else:
            raise Conflict(path, self.vault.current_hash(path))
        self.index.update(path)
        return {"path": path, "hash": h2, "created": not existing}

    def rescan(self) -> dict:
        self.history.checkpoint()  # notes changed by other programs: recorded as such
        return self.index.sync()
