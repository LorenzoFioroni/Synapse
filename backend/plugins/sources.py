"""The lists of outside papers running plugins offer (``@plugin.item_source``), as the app shows
them: the sidebar asks for the list of sources, the item list for a source's papers. A paper
already in the library comes back as the library item itself, so it shows and behaves as one;
the others are outside records (outside.py)."""

from __future__ import annotations

from werkzeug.exceptions import NotFound

from .badges import highlights
from .errors import PluginError
from .fields import keys_of
from .outside import record

MAX_PAPERS = 2000  # a source returning more is cut (the list is meant for reading, not a database)


class SourceList:
    def __init__(self, host):
        self.host = host

    def _find(self, full_id: str):
        base_id, _, variant = full_id.partition("~")
        pid, _, sid = base_id.partition(".")
        act = self.host._active.get(pid)
        src = act.plugin.sources.get(sid) if act else None
        if not src:
            raise NotFound(f"No list {full_id!r} (is its plugin turned on?)")
        if variant and variant not in {v["id"] for v in self._variants(pid, src)}:
            raise NotFound(f"No list variant {variant!r} in {base_id!r}")
        return act, src, variant or None

    def _variants(self, pid: str, src) -> list[dict]:
        if not src.variants:
            return []
        got = self.host.run(pid, f"variants of {src.id}", src.variants) or []
        if not isinstance(got, list | tuple):
            raise PluginError(f"The variants of item source {src.id} must be a list")
        out = []
        for v in got[:400]:
            if not isinstance(v, dict) or not str(v.get("id") or "").strip():
                raise PluginError(f"Each variant of item source {src.id} needs an id and title")
            entry: dict = {
                "id": str(v["id"])[:80],
                "title": str(v.get("title") or v["id"])[:80],
                "badge": str(v["badge"])[:12] if v.get("badge") not in (None, "", 0) else None,
            }
            badges = highlights(v.get("highlights"))
            if badges:
                entry["highlights"] = badges
            out.append(entry)
        return out

    def describe(self) -> list[dict]:
        """The sources of the running plugins, for the sidebar (with their badges)."""
        out = []
        for pid, act in list(self.host._active.items()):
            for s in act.plugin.sources.values():
                if s.hidden:
                    continue
                badge = None
                if s.badge:
                    try:
                        b = self.host.run(pid, f"badge of {s.id}", s.badge)
                        badge = None if b in (None, "", 0) else str(b)[:12]
                    except Exception:
                        badge = None
                described = {
                    "id": f"{pid}.{s.id}",
                    "title": s.title,
                    "icon": s.icon,
                    "description": s.description,
                    "plugin": pid,
                    "plugin_name": act.entry.manifest.name if act.entry.manifest else pid,
                    "badge": badge,
                }
                variants = self._variants(pid, s)
                if variants:
                    described["variants"] = variants
                out.append(described)
        return out

    def items(self, full_id: str) -> list[dict]:
        """A source's papers: library items for those in the library, outside records for the
        rest, in the order the plugin gave them (each with ``_rank``). Raises NotFound for an
        unknown source, PluginError when the plugin fails."""
        act, src, variant = self._find(full_id)
        got = (
            self.host.run(act.entry.id, f"item source {src.id}", src.fn, variant)
            if src.variants
            else self.host.run(act.entry.id, f"item source {src.id}", src.fn)
        )
        got = got or []
        if not isinstance(got, list | tuple):
            raise PluginError(f"The item source {src.id} must return a list of papers")
        by_key: dict[str, dict] = {}
        library = self.host.library.items() if self.host.library else []
        for p in library:
            for k in keys_of(p)[:-1]:  # doi:/arxiv: keys (not item:)
                by_key.setdefault(k, p)
        out, seen = [], set()
        for rank, d in enumerate(got[:MAX_PAPERS]):
            rec = record(d, source=full_id)
            if rec["key"] in seen:
                continue
            seen.add(rec["key"])
            mine = by_key.get(rec["key"]) or (by_key.get(f"arxiv:{rec['arxiv']}") if rec.get("arxiv") else None)
            if mine:
                if mine["id"] in seen:
                    continue
                seen.add(mine["id"])
                item = {**mine, "_rank": rank, "_source": full_id}
                if rec.get("highlights"):
                    item["highlights"] = rec["highlights"]
                if rec.get("highlight"):
                    item["highlight"] = rec["highlight"]
                if rec.get("reasons"):
                    item["reasons"] = rec["reasons"]  # why the plugin lists it, also for a library paper
                out.append(item)
            else:
                out.append({**rec, "_rank": rank})
        return out

    def record(self, full_id: str, key: str) -> dict:
        """One outside paper of a source, by its key (for adding it)."""
        for p in self.items(full_id):
            if p.get("key") == key and p.get("_outside"):
                return p
        raise NotFound(f"{key} is not in {full_id}")
