"""Note operations used by the HTTP API (and later by the MCP server).

Keeps the vault (files) and the index (derived data) in step: every change goes to the file
first and the index is updated from the file afterwards.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime

from . import frontmatter
from .index import NotesIndex
from .vault import ATTACHMENTS, NoteExists, Vault, slugify

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


class NotesService:
    def __init__(self, vault: Vault, index: NotesIndex, papers):
        self.vault = vault
        self.index = index
        self.papers = papers

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

    def complete_citations(self, q: str, limit: int = 20) -> list[dict]:
        """Library items for the [@ autocomplete, matched on key, title, authors."""
        words = [w.lower() for w in re.findall(r"\w+", q or "")]
        res = []
        for key, it in self._library_keys().items():
            if it["trashed"]:
                continue
            hay = f"{key} {it['title']} {it['authors']} {it['year'] or ''}".lower()
            if all(w in hay for w in words):
                res.append({"key": key, **it})
        res.sort(key=lambda r: (not r["key"].lower().startswith(words[0]) if words else False, r["key"]))
        return res[:limit]

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

    def save(self, path: str, text: str, base_hash: str | None) -> dict:
        """Save over the version with ``base_hash``; ``None`` creates (fails if the file exists)."""
        path = self.vault.clean_rel(path)
        h = self.vault.write(path, text, expected_hash=base_hash)
        self.index.update(path)
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
        return {"trash_id": tid, "path": path}

    def restore(self, trash_id: str, path: str) -> dict:
        path = self.vault.restore(trash_id, path)
        self.index.update(path)
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

    def rescan(self) -> dict:
        return self.index.sync()
