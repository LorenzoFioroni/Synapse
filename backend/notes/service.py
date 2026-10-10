"""Note operations used by the HTTP API (and later by the MCP server).

Keeps the vault (files) and the index (derived data) in step: every change goes to the file
first and the index is updated from the file afterwards.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from . import frontmatter
from .index import NotesIndex
from .rekey import Rekeyer
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
        from ..search import normalize
        self._normalize = normalize
        self._entries = []   # (item_id, title, original text, normalised text)
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
            if m:   # the term as typed: show it highlighted in context
                a = max(0, m.start() - 60)
                piece = (html.escape(flat[a:m.start()]) + "<mark>" + html.escape(m.group(0)) + "</mark>"
                         + html.escape(flat[m.end():m.end() + 80]))
            else:   # matched only after removing accents/case: show the start of the text
                a = 0
                piece = html.escape(flat[:140])
            out[pid] = f'<span class="hit-src">Note · {html.escape(title)}</span> {"…" if a else ""}{piece}…'
        return out


class NotesService:
    def __init__(self, vault: Vault, index: NotesIndex, papers, backups_dir=None):
        self.vault = vault
        self.index = index
        self.papers = papers
        self.rekeyer = Rekeyer(vault, index, backups_dir or (vault.root.parent / "backups"))
        self.on_change = None   # called after notes are saved, deleted or restored

    # ------------------------------------------------------------------ citation keys
    def text_matcher(self) -> NoteTextMatcher:
        """For the library search (cached until the notes or the keys change)."""
        keys = {p["citation_key"]: p["id"] for p in self.papers.list() if p.get("citation_key")}
        sig = (self.index.revision, tuple(sorted(keys.items())))
        if getattr(self, "_matcher_sig", None) != sig:
            self._matcher = NoteTextMatcher(self.index.texts_by_key(), keys)
            self._matcher_sig = sig
        return self._matcher

    def rename_plan(self, old: str, new: str) -> dict:
        return self.rekeyer.plan(old, new)

    def rename_key(self, old: str, new: str) -> dict:
        return self.rekeyer.apply(old, new)

    # ------------------------------------------------------------------ helpers
    def _library_keys(self) -> dict[str, dict]:
        """citation_key -> compact item info, for resolving [@key] (includes trashed items)."""
        out = {}
        notes = self.index.paper_note_map()
        for view in ("library", "trash"):
            for p in self.papers.list(view):
                k = p.get("citation_key")
                if k and k not in out:
                    pdf = next((a for a in p.get("attachments") or [] if (a.get("type") or "") == "pdf"), None)
                    out[k] = {"id": p["id"], "title": p.get("title") or "", "authors": p.get("authors") or "",
                              "year": p.get("year"), "journal": p.get("journal") or "", "trashed": view == "trash",
                              "has_pdf": pdf is not None,
                              "pdf": {"id": pdf["id"], "url": pdf["url"], "name": pdf.get("name") or ""} if pdf else None,
                              "note": notes.get(k)}
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
        return {"notes": self.index.list(), "folders": self.vault.folders(), "stats": self.index.stats()}

    def get(self, path: str) -> dict:
        path = self.vault.clean_rel(path)
        text, h = self.vault.read(path)
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
            "unresolved": [l for l in out if (l["kind"] in ("note", "embed") and not l["resolved"])
                           or (l["kind"] == "cite" and not l["item"])],
            "backlinks": self.index.backlinks(path),
        }

    def item_notes(self, item_id: str) -> dict:
        item = self.papers.get(item_id)
        key = item.get("citation_key") or ""
        if not key:
            return {"key": "", "paper_notes": [], "citing": []}
        return {"key": key, "paper_notes": self.index.paper_notes(key), "citing": self.index.citing(key),
                "suggested_path": f"papers/{slugify(key)}.md"}

    def search(self, q: str) -> list[dict]:
        return self.index.search(q)

    # ------------------------------------------------------------------ writes
    def create(self, *, path: str | None = None, folder: str = "", title: str = "", item_id: str | None = None,
               text: str | None = None) -> dict:
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
        h = self.vault.write(path, content, expected_hash=None)
        self.index.update(path)
        return {"path": path, "text": content, "hash": h}

    def _changed(self) -> None:
        """Notes were saved, deleted or restored: attachments may have become (un)used."""
        if self.on_change:
            self.on_change()

    def save(self, path: str, text: str, base_hash: str | None) -> dict:
        """Save over the version with ``base_hash``; ``None`` creates (fails if the file exists)."""
        path = self.vault.clean_rel(path)
        h = self.vault.write(path, text, expected_hash=base_hash)
        self.index.update(path)
        self._changed()
        return {"path": path, "hash": h, "info": self.index.note(path)}

    def move(self, path: str, to: str, base_hash: str | None = None) -> dict:
        src = self.vault.clean_rel(path)
        dst = self.vault.clean_rel(to)
        linked_before = self.index.backlinks(src)
        self.vault.move(src, dst, expected_hash=base_hash)
        self.index.remove(src)
        self.index.update(dst)
        # Links by name keep working if only the folder changed; report the ones that broke.
        broken = [b for b in linked_before if b["path"] != dst and
                  (self.index.resolve(b["target"], from_path=b["path"]) or [None])[0] != dst]
        return {"path": dst, "broken_links": broken}

    def make_folder(self, path: str) -> dict:
        return {"path": self.vault.make_folder(path)}

    def trash(self, path: str) -> dict:
        path = self.vault.clean_rel(path)
        tid = self.vault.trash(path)
        self.index.remove(path)
        self._changed()
        return {"trash_id": tid, "path": path}

    def trash_many(self, paths: list[str], folders: list[str]) -> dict:
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

    def restore_batch(self, trash_id: str) -> dict:
        restored, skipped = self.vault.restore_batch(trash_id)
        self._reindex(restored)
        self._changed()
        return {"paths": restored, "skipped": skipped}

    def move_folder(self, path: str, to: str) -> dict:
        src = self.vault.clean_rel(path, suffix=None)
        before = {n: self.index.backlinks(n) for n in self.vault.notes_in(src)}
        moved = self.vault.move_folder(src, to)
        for old, _ in moved:
            self.index.remove(old)
        self._reindex(new for _, new in moved)
        new_of = dict(moved)
        broken = []
        for old, links in before.items():
            for b in links:
                src_now = new_of.get(b["path"], b["path"])
                if (self.index.resolve(b["target"], from_path=src_now) or [None])[0] != new_of[old]:
                    broken.append({**b, "path": src_now})
        return {"path": self.vault.clean_rel(to, suffix=None), "moved": [{"from": o, "to": n} for o, n in moved],
                "broken_links": broken}

    def key_summary(self) -> dict:
        return self.index.key_summary()

    def restore(self, trash_id: str, path: str) -> dict:
        path = self.vault.restore(trash_id, path)
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
        for _ in range(3):   # someone may be saving the same note right now: re-read and retry
            text, h = self.vault.read(path)
            nl = "\r\n" if "\r\n" in text else "\n"
            new = text.rstrip("\r\n") + nl + nl + markdown.replace("\n", nl) + nl
            try:
                h2 = self.vault.write(path, new, expected_hash=h)
                break
            except Conflict:
                continue
        else:
            raise Conflict(path, self.vault.current_hash(path))
        self.index.update(path)
        return {"path": path, "hash": h2, "created": not existing}

    def rescan(self) -> dict:
        return self.index.sync()
