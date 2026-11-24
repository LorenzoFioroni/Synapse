"""Note operations used by the HTTP API (and later by the MCP server).

Keeps the vault (files) and the index (derived data) in step: every change goes to the file
first and the index is updated from the file afterwards.
"""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from ..library.agent import current_agent, current_device
from . import frontmatter, locks
from .folder_colors import FolderColorError, FolderColors
from .history import NoteHistory
from .history.reading import MILESTONE_PREFIX
from .index import NotesIndex
from .locks import NoteLocked, UnlockGrants, is_locked_text
from .rekey import Rekeyer
from .relink import Relink, Relinker
from .vault import (
    ATTACHMENTS,
    PDF_SUFFIX,
    TRASH,
    Conflict,
    InvalidPath,
    NoteExists,
    Vault,
    VaultError,
    is_pdf_path,
    slugify,
)

log = logging.getLogger(__name__)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
IMAGE_TYPES = {  # extension -> magic-byte check. No SVG: it can carry scripts.
    "png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
    "jpg": lambda b: b.startswith(b"\xff\xd8\xff"),
    "gif": lambda b: b.startswith((b"GIF87a", b"GIF89a")),
    "webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_PDF_BYTES = 90 * 1024 * 1024  # below the 100 MB a Cloudflare Tunnel accepts in one request
LOCK_TIMEOUT = 10 * 60  # seconds an unlocked note stays open unused (Services reads it from Settings)


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

    def item_ids(self) -> set[str]:
        """Items a note is about or cites."""
        return {e[0] for e in self._entries}

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
    def __init__(self, vault: Vault, index: NotesIndex, papers, backups_dir=None, history_dir=None, pdfs=None):
        """``backups_dir``: where key renames back up the notes (a path, or a function giving it);
        ``history_dir``: the Git directory of the notes history (default: next to the notes)."""
        self.vault = vault
        self.index = index
        self.papers = papers
        self.pdfs = pdfs  # the PDFs beside the notes (NotePdfs), when the app keeps them
        self.pdf_search: Callable[[str], list[dict]] | None = None  # their text (Services): the notes search shows it
        self.pdf_pages: Callable[[int], int | None] | None = (
            None  # a PDF's page count by registry id, if known (Services)
        )
        self.on_pdfs_changed: Callable[[], None] | None = None  # PDFs came or came back: their text is indexed
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
        self.grants = UnlockGrants(lambda: LOCK_TIMEOUT)  # which pages unlocked which locked notes (locks.py)

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
        plan = self.rekeyer.plan(old, new)
        for n in plan["notes"]:  # the lines of a locked note are not shown, only that it changes
            if self.index.is_locked(n["path"]) and not self.grants.allows(n["path"]):
                n["locked"] = True
                n["changes"] = [{"line": c["line"], "before": "", "after": ""} for c in n["changes"]]
        return plan

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

    # ------------------------------------------------------------------ locks
    def _closed(self, path: str, text: str | None = None, *, extend: bool = False) -> bool:
        """Whether the note at ``path`` is locked and this request has not unlocked it. ``text``:
        the note as just read (the index may not have seen an edit made outside the app).
        ``extend``: a change by the user, which keeps the note open longer (reading does not)."""
        locked = is_locked_text(text) if text is not None else self.index.is_locked(path)
        return locked and not self.grants.allows(path, extend=extend)

    def require_open(self, path: str, text: str | None = None, *, extend: bool = False) -> None:
        """Raise NoteLocked unless this request may read the note at ``path``."""
        if self._closed(path, text, extend=extend):
            raise NoteLocked(path)

    def touch(self, paths: list[str]) -> dict:
        """The page reports that the notes were used (clicked, typed in, scrolled, touched): they
        stay open ``timeout`` from now. Answers how long each one still is, null when locked."""
        out = {}
        for p in paths:
            path = self.vault.clean_rel(p)
            out[path] = self.grants.remaining(path) if self.grants.allows(path, extend=True) else None
        return {"unlocked_for": out}

    def unlock(self, path: str) -> dict:
        """Open a locked note for the page asking (the API has checked the password or passkey)."""
        path = self.vault.clean_rel(path)
        text, _ = self.vault.read(path)
        if not is_locked_text(text):
            return {"path": path, "locked": False}
        return {"path": path, "locked": True, "unlocked_for": self.grants.grant(path)}

    def relock(self, paths: list[str] | None) -> dict:
        """Lock again the notes this page unlocked (``paths``, or all of them)."""
        self.grants.revoke([self.vault.clean_rel(p) for p in paths] if paths is not None else None)
        return {"ok": True}

    def set_locked(self, path: str, locked: bool) -> dict:
        """Lock a note (``locked: true`` in its front matter) or remove its lock. Removing it, like
        reading it, needs the note unlocked by this page first. The page that locks a note keeps it
        unlocked, so its open tab stays open until the note is left."""
        path = self.vault.clean_rel(path)
        for _ in range(3):  # someone may be saving the note right now: re-read and retry
            text, h = self.vault.read(path)
            if is_locked_text(text) == locked:
                break
            if not locked:
                self.require_open(path, text)
            new = frontmatter.set_fields(text, {locks.FIELD: True if locked else None})
            try:
                with self.history.saving([path], device=current_device()):
                    h = self.vault.write(path, new, expected_hash=h)
                break
            except Conflict:
                continue
        else:
            raise Conflict(path, self.vault.current_hash(path))
        self.index.update(path)
        if locked:
            if locks.has_viewer():
                self.grants.grant(path)
        else:
            self.grants.forget(path)
        self._saved(path, h, "app")
        return {"path": path, "hash": h, "locked": locked, "info": self.index.note(path)}

    def _lock_state(self, path: str, text: str) -> dict:
        """For answers about a note: nothing for an ordinary note."""
        if not is_locked_text(text):
            return {}
        return {"locked": True, "unlocked_for": self.grants.remaining(path)}

    # ------------------------------------------------------------------ reads
    def overview(self) -> dict:
        folders = self.vault.folders()
        shown = set(folders)
        colors = {f: c for f, c in self.folder_colors.all().items() if f in shown}
        return {
            "notes": self.index.list(),
            "pdfs": self.pdfs.listing() if self.pdfs else [],
            "folders": folders,
            "folder_colors": colors,
            "stats": self.index.stats(),
        }

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
        self.require_open(path, text)
        lock = self._lock_state(path, text)
        if unless and unless == h:
            return {"path": path, "hash": h, "unchanged": True, **lock}
        info = self.index.note(path)
        if info is None or info["hash"] != h:  # edited outside the app since the last sync
            self.index.update(path)
            info = self.index.note(path)
        return {"path": path, "text": text, "hash": h, "info": info, **lock}

    def links(self, path: str) -> dict:
        if is_pdf_path(path or ""):  # a PDF: the notes that link to it
            return {
                "outgoing": [],
                "cited": [],
                "unresolved": [],
                "backlinks": self.index.pdf_backlinks(self.vault.clean_rel(path, suffix=PDF_SUFFIX)),
            }
        path = self.vault.clean_rel(path)
        self.require_open(path)
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
                if (l["kind"] in ("note", "embed", "pdf") and not l["resolved"])
                or (l["kind"] == "cite" and not l["item"])
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
        found = self.index.search(q)
        if self.pdf_search:
            try:
                found += self.pdf_search(q)
            except Exception:  # the notes alone are an answer
                log.exception("Searching the text of the PDFs failed")
        return found

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
        if base_hash is not None or self.vault.exists(path):
            self.require_open(path, self._current_text(path), extend=True)
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
        if is_locked_text(text) and by != "ai" and locks.has_viewer() and not self.grants.remaining(path):
            self.grants.grant(path)  # locked by typing ``locked: true``: the tab that did it stays open
        self._changed()
        self._saved(path, h, by)
        return {"path": path, "hash": h, "info": self.index.note(path), **self._lock_state(path, text)}

    def apply_offline(self, path: str, base_hash: str | None, base_text: str | None, versions: list[dict]) -> dict:
        """Bring in a note's edits made on a device while Synapse could not be reached (docs/offline.md).

        ``base_hash``/``base_text``: the note as the device last had it (None: created offline);
        ``versions``: ``{text, at}`` in order, ``at`` in ms since the epoch. When the note did not
        change here meanwhile, each version is committed as it was made; otherwise each is merged
        (three-way) with the note as it is now first. Nothing is written when a merge conflicts:
        the answer is ``status: "conflict"`` with the texts, for the user to resolve; a note deleted
        here meanwhile answers ``status: "deleted"``."""
        path = self.vault.clean_rel(path)
        if not path.endswith(".md"):
            raise ValueError("Only notes can be edited offline.")
        now = time.time()
        clean: list[tuple[str, float]] = []
        for v in versions or []:
            if not isinstance(v, dict) or not isinstance(v.get("text"), str):
                raise ValueError("Each offline version needs its text.")
            at = v.get("at")
            when = float(at) / 1000 if isinstance(at, int | float) else now
            clean.append((v["text"], min(max(when, 0.0), now)))  # a device clock in the future is not believed
        if not clean:
            raise ValueError("There are no offline edits to bring in.")
        current = self._current_text(path)
        if current is None and base_hash is not None:
            return {"status": "deleted", "path": path, "mine": clean[-1][0]}
        if current is not None:
            self.require_open(path, current, extend=True)
        cur_hash = self.vault.current_hash(path) if current is not None else None
        status = "applied"
        if current is not None and cur_hash != base_hash:
            base = base_text if base_text is not None and base_hash is not None else ""
            merged = []
            for text, when in clean:
                text2, ok = self.history.merge(current, base, text)
                if not ok:
                    return {"status": "conflict", "path": path, "hash": cur_hash, "current": current,
                            "base": base, "mine": clean[-1][0], "merged": self.history.merge(current, base, clean[-1][0])[0]}  # fmt: skip
                merged.append((text2, when))
            clean, status = merged, "merged"
        expected: list[str | None] = [cur_hash]

        def write(text: str) -> str:
            h = self.vault.write(path, text, expected_hash=expected[0])
            expected[0] = h
            return h

        h = self.history.offline(path, clean, write, device=current_device())
        self.index.update(path)
        self._changed()
        self._saved(path, h, "app")
        final = clean[-1][0]
        return {
            "status": status,
            "path": path,
            "hash": h,
            "text": final,
            "info": self.index.note(path),
            **self._lock_state(path, final),
        }

    def _current_text(self, path: str) -> str | None:
        try:
            return self.vault.read(path)[0]
        except VaultError:
            return None

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
            self.require_open(path, before, extend=True)
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
        self.require_open(path, self._current_text(path))
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
            if e.get("path") and self._version_closed(e["path"], e.get("before"), e.get("after")):
                raise NoteLocked(e["path"])
            return e
        if not re.fullmatch(r"[0-9a-f]{7,64}", entry_id or "") or not path:
            raise KeyError(entry_id)
        path = self.vault.clean_rel(path)
        from_path = self.vault.clean_rel(from_path) if from_path else path
        v = self.history.version(entry_id, path, from_path)
        if v["after"] is None and v["before"] is None:
            raise KeyError(entry_id)
        if self._version_closed(path, v["before"], v["after"]):
            raise NoteLocked(path)
        return v

    def _version_closed(self, path: str, *texts: str | None) -> bool:
        """Whether an old version of ``path`` must not be shown: the note is locked now, or it is
        gone and the version itself was locked. A note whose lock was removed shows its past."""
        current = self._current_text(path)
        if current is not None:
            return self._closed(path, current)
        return any(is_locked_text(t) for t in texts) and not self.grants.allows(path)

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
        d = self.history.diff(a, b)
        for f in d["files"]:  # a locked note shows that it changed, never what it says
            paths = {f["path"], f.get("from") or f["path"]}
            if any(self._version_closed(p, f.get("before"), f.get("after")) for p in paths):
                f["locked"] = True
                f.pop("before", None)
                f.pop("after", None)
        return d

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
                        if is_locked_text(self._current_text(f["path"])) and not is_locked_text(text):
                            text = frontmatter.set_fields(text, {locks.FIELD: True})  # a lock is never undone so
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
            updated, unfixed = [], [{"path": plan.moves.get(p, p), "target": "", "line": 0} for p in plan.sources]
        if updated:
            rec.paths = [*(rec.paths or []), *(u["path"] for u in updated)]
            n = sum(u["count"] for u in updated)
            rec.body = (
                f"Updated {n} link{'s' if n != 1 else ''} in {len(updated)} note{'s' if len(updated) != 1 else ''}."
            )
        return {"updated_links": updated, "broken_links": unfixed}

    def move_pdf(self, path: str, to: str) -> dict:
        """Rename or move a PDF; it keeps its number (and so its highlights). Never in the history:
        only notes are."""
        src = self.vault.clean_rel(path, suffix=PDF_SUFFIX)
        dst = self.vault.clean_rel(to, suffix=PDF_SUFFIX)
        plan = self._link_plan({src: dst}) if src != dst else Relink({})
        # the history records the notes whose links were rewritten, never the PDF
        change = (
            self.history.change(
                kind="rename",
                subject=f"Renamed the PDF {Path(src).name} to {Path(dst).name}",
                device=current_device(),
                paths=list(plan.sources),
            )
            if plan
            else nullcontext()
        )
        with change as rec:
            self.vault.move(src, dst, suffix=PDF_SUFFIX)
            if self.pdfs and src != dst:
                self.pdfs.moved(src, dst)
            self.index.sync_files()
            fixed: dict = {"updated_links": [], "broken_links": []}
            if rec is not None:
                rec.paths = []
                fixed = self._fix_links(plan, rec)
        if fixed["updated_links"]:
            self._changed()
        return {"path": dst, **fixed}

    def move(self, path: str, to: str, base_hash: str | None = None) -> dict:
        if is_pdf_path(path or ""):
            return self.move_pdf(path, to)
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
            self.grants.moved({src: dst})
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
        if is_pdf_path(path or ""):
            return self.trash_many([path], [])
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
        notes = [self.vault.clean_rel(p) for p in paths or [] if not is_pdf_path(p or "")] + inside
        subject = f"Deleted {Path(notes[0]).stem}" if len(notes) == 1 else f"Deleted {len(notes)} notes"
        # a deletion of PDFs only changes no note: nothing for the history
        change = (
            self.history.change(kind="delete", subject=subject, device=current_device(), paths=notes)
            if notes
            else nullcontext()
        )
        with change:
            tid, moved = self.vault.trash_batch(paths or [], folders or [])
        pdfs = [p for p in moved if is_pdf_path(p)]
        moved = [p for p in moved if not is_pdf_path(p)]
        for p in moved:
            self.index.remove(p)
        for p in pdfs:
            if self.pdfs:
                self.pdfs.moved(p, f"{TRASH}/{tid}/{p}")
        if pdfs:
            self.index.sync_files()
        self._changed()
        return {"trash_id": tid, "paths": moved, "pdfs": pdfs}

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
            pdfs = [p for p in restored if is_pdf_path(p)]
            restored = [p for p in restored if not is_pdf_path(p)]
            rec.paths = restored
        for p in pdfs:
            if self.pdfs:
                self.pdfs.moved(f"{TRASH}/{trash_id}/{p}", p)
        if pdfs:
            self.index.sync_files()
            self._pdfs_came()
        self._reindex(restored)
        self._changed()
        return {"paths": restored, "pdfs": pdfs, "skipped": skipped}

    def move_folder(self, path: str, to: str) -> dict:
        src = self.vault.clean_rel(path, suffix=None)
        dst = self.vault.clean_rel(to, suffix=None)
        inside = self.vault.notes_in(src)
        files = self.vault.pdfs_in(src)
        plan = self._link_plan({n: dst + n[len(src) :] for n in [*inside, *files]}) if dst != src else Relink({})
        with self.history.change(
            kind="rename",
            subject=f"Moved the folder {src} to {to}",
            device=current_device(),
            paths=[*inside, *plan.sources],
        ) as rec:
            moved = self.vault.move_folder(src, to)
            self.grants.moved(dict(moved))
            if self.pdfs and dst != src:
                self.pdfs.moved(src, dst)
            self.history.legacy.moved(moved)
            self.folder_colors.moved(src, dst)
            for old, _ in moved:
                self.index.remove(old)
            self.index.sync_files()
            self._reindex(new for _, new in moved)
            rec.paths = [p for pair in moved for p in pair]
            fixed = self._fix_links(plan, rec)
        if fixed["updated_links"]:
            self._changed()
        return {"path": dst, "moved": [{"from": o, "to": n} for o, n in moved], **fixed}

    def key_summary(self) -> dict:
        return self.index.key_summary()

    def restore(self, trash_id: str, path: str) -> dict:
        if is_pdf_path(path or ""):
            path = self.vault.restore(trash_id, path)
            if self.pdfs:
                self.pdfs.moved(f"{TRASH}/{trash_id}/{path}", path)
            self.index.sync_files()
            self._pdfs_came()
            return {"path": path}
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

    def add_pdf(self, filename: str, stream, folder: str = "") -> dict:
        """Add an uploaded PDF to a notes folder (the caller has checked that it is one)."""
        if not self.pdfs:
            raise InvalidPath("PDFs are not kept here")
        folder = self.vault.clean_rel(folder, suffix=None) if (folder or "").strip("/ ") else ""
        raw = os.path.splitext(os.path.basename((filename or "").replace("\\", "/")))[0]
        # no invisible direction or format characters in a file name (U+202E would flip how ``x.pdf`` reads)
        raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Cf")
        stem = slugify(raw, "Document")
        rel, sha, _ = self.vault.write_stream(
            f"{folder}/{stem}{PDF_SUFFIX}" if folder else f"{stem}{PDF_SUFFIX}", stream, max_bytes=MAX_PDF_BYTES
        )
        info = self.pdfs.register(rel, sha)
        self.index.sync_files()
        self._pdfs_came()
        return info

    def _pdfs_came(self) -> None:
        if self.on_pdfs_changed:
            self.on_pdfs_changed()

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
        if self.pdfs:
            self.pdfs.sync()
        return self.index.sync()
