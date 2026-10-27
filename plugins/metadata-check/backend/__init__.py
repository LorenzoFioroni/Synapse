"""Periodic metadata audit, including journal versions announced by arXiv.

What a check finds for an item is kept (``findings``) apart from what the field shows, so a
suggestion the user ignores can be hidden at once and shown again when they stop ignoring it,
without asking the metadata services again. An ignore remembers the values it was about: a later
check that finds a *different* title, or another published DOI, is a new suggestion and shows.
"""

from __future__ import annotations

import time
from threading import Event

from backend.plugins import Offline, Plugin, PluginError
from backend.plugins.scholarly import compute_changes

from .comparison import find, ignore_entry, is_preprint, verdict
from .progress import Progress

FIELD = "metadata_check"
DEFAULT_DAYS = 3


def _days(value) -> float:
    try:
        return max(1.0, float(value))
    except (TypeError, ValueError):
        return float(DEFAULT_DAYS)


def _ids() -> list[str]:
    from flask import request

    return [str(i) for i in (request.get_json(silent=True) or {}).get("ids") or []]


def activate(plugin: Plugin) -> None:
    state = plugin.storage.json("state")  # last_checked, running, done, total, with_changes, last_run
    findings = plugin.storage.json("findings")  # {item id: finding}
    ignored = plugin.storage.json("ignored")  # {item id: ignore entry, or {"pending": True}}
    ignored_papers = plugin.storage.json("ignored_papers")  # {item id: {"title": ..., "at": ...}}

    if not state.get("exact_rejections"):
        for item_id, entry in ignored.all().items():
            if entry.get("shown"):
                entry["fields"] = dict(entry["shown"])
                ignored.set(item_id, entry)
        state.set("exact_rejections", True)
    stopped = Event()
    plugin.on_unload(stopped.set)
    progress = Progress(state, lambda: None if stopped.is_set() else plugin.fields.changed())

    def remember_ignore(item_id: str, finding: dict) -> dict:
        item = plugin.library.get(item_id) or {}
        before = ignored.get(item_id) or {}
        entry = ignore_entry(finding, item.get("title") or before.get("title") or "", before)
        ignored.set(item_id, entry)
        return entry

    def remember_ignore_field(item_id: str, field_name: str, value: str) -> dict:
        item = plugin.library.get(item_id) or {}
        before = ignored.get(item_id) or {}
        fields = {**(before.get("fields") or {}), field_name: value}
        shown = {**(before.get("shown") or {}), field_name: value}
        entry = {
            "title": item.get("title") or before.get("title") or "",
            "fields": fields,
            "shown": shown,
            "at": time.time(),
        }
        if before.get("published"):
            entry["published"] = before["published"]
        finding = findings.get(item_id) or {}
        if field_name == "doi" and str((finding.get("fetched") or {}).get("doi")) == value:
            if finding.get("published"):
                entry["published"] = finding["published"]
        ignored.set(item_id, entry)
        return entry

    def audit(papers: list[dict]) -> dict:
        out: dict = {}
        if not progress.running:
            due = plugin.fields.due(FIELD)
            progress.start([p["id"] for p in due])

        for paper in papers:
            if stopped.is_set():
                return {p["key"]: plugin.fields.LATER for p in papers}
            item_id = str(paper["item_id"])
            if item_id in ignored_papers.all():
                out[paper["key"]] = "Up to date · ignored paper"
                progress.done(item_id)
                continue

            current = plugin.library.get(item_id) or {}
            if not current.get("doi") and not paper.get("doi") and not paper.get("arxiv") and not current.get("title"):
                out[paper["key"]] = "Needs a DOI or arXiv ID"
                progress.done(item_id)
                continue

            try:
                _, found = plugin.services.lookup_item(current)
            except Offline as error:
                plugin.log.info("Could not check %s: %s", item_id, error)
                out[paper["key"]] = plugin.fields.LATER
                progress.done(item_id, failed=True)
                continue
            except PluginError as error:
                out[paper["key"]] = f"Could not verify: {error}"
                progress.done(item_id, failed=True)
                continue

            if stopped.is_set():
                return {p["key"]: plugin.fields.LATER for p in papers}
            finding = find(current, found, is_preprint(current) or bool(paper.get("arxiv")))
            has_diffs = bool(finding.get("fields") or finding.get("published"))
            if has_diffs:
                findings.set(item_id, finding)
            else:
                findings.delete(item_id)

            entry = ignored.get(item_id)
            if entry and entry.get("pending"):
                entry = remember_ignore(item_id, finding)

            v = verdict(finding, entry, is_paper_ignored=(item_id in ignored_papers.all()))
            out[paper["key"]] = v
            progress.done(item_id)

        progress.finish()
        return out

    field = plugin.fields.declare(
        FIELD,
        type="text",
        label="Metadata Check",
        description="Current scholarly metadata compared with this library record",
        source="Metadata Sources",
        refresh=f"{_days(plugin.settings.get('interval_days', DEFAULT_DAYS)):g}d",
        refresher=audit,
    )

    # Older findings lack the full payload needed for review; refresh them once.
    old = [item_id for item_id, finding in findings.all().items() if "fetched" not in finding]
    if old:
        plugin.fields.refresh(FIELD, old, wake=False)

    @plugin.settings.on_change
    def _interval(changed: dict) -> None:
        if "interval_days" in changed:
            field.refresh = _days(changed["interval_days"]) * 86400
            plugin.fields.refresh_soon()

    def show(item_id: str) -> None:
        if item_id in ignored_papers.all():
            plugin.fields.set(item_id, FIELD, "Up to date · ignored paper")
            return
        finding = findings.get(item_id)
        current = plugin.library.get(item_id)
        if finding is not None and current and "fetched" in finding:
            finding = find(current, finding["fetched"], is_preprint(current))
        if finding is not None:
            text = verdict(finding, ignored.get(item_id))
            value, url = text if isinstance(text, tuple) else (text, None)
            plugin.fields.set(item_id, FIELD, value, source_url=url)
        else:
            plugin.fields.set(item_id, FIELD, "Up to date")

    @plugin.on("item.deleted")
    def _forget(event: dict) -> None:
        for item_id in event.get("ids") or []:
            findings.delete(str(item_id))
            ignored.delete(str(item_id))
            ignored_papers.delete(str(item_id))

    def review_articles() -> list[dict]:
        out = []
        for item_id, stored in sorted(findings.all().items()):
            if ignored_papers.get(item_id):
                continue
            item = plugin.library.get(item_id)
            if not item:
                continue
            fetched = stored.get("fetched") or {}
            finding = find(item, fetched, is_preprint(item))
            entry = ignored.get(item_id) or {}
            ignored_fields = entry.get("fields") or {}
            pub = finding.get("published")
            pub_active = pub and pub != entry.get("published")
            changes = [
                c
                for c in compute_changes(item, fetched)
                if c["checked"]
                and ignored_fields.get(c["key"]) != str(c["fetched"])
                and not (c["key"] == "doi" and pub and not pub_active)
            ]
            if changes or pub_active:
                out.append(
                    {
                        "id": item_id,
                        "current": item,
                        "fetched": fetched,
                        "changes": changes,
                        "published": pub if pub_active else None,
                    }
                )
        return out

    @plugin.route("/status")
    def status():
        unreviewed = len(review_articles())
        return {
            "last_checked": state.get("last_checked"),
            "interval_days": _days(plugin.settings.get("interval_days", DEFAULT_DAYS)),
            "ignored": len(ignored.all()),
            "ignored_papers": len(ignored_papers.all()),
            "running": progress.running,
            "failed": state.get("failed", 0),
            "done": state.get("done", 0),
            "total": state.get("total", 0),
            "with_changes": unreviewed,
            "unreviewed": unreviewed,
            "last_run": state.get("last_run"),
        }

    @plugin.route("/run", methods=["POST"])
    def run_now():
        """Check every library item again soon, whatever the interval says."""
        if progress.running:
            return {"items": state.get("total", 0)}
        # Wake only after progress is initialized from the complete due snapshot.
        plugin.fields.refresh(FIELD, wake=False)
        due = plugin.fields.due(FIELD)
        progress.start([p["id"] for p in due])
        progress.finish()
        plugin.fields.refresh_soon()
        return {"items": len(due)}

    @plugin.route("/review")
    def review():
        """Return flagged items with differences for the Review dialog."""
        return {"articles": review_articles()}

    @plugin.route("/applied", methods=["POST"])
    def applied():
        """Changes were applied from the review dialog."""
        ids = _ids()
        for item_id in ids:
            finding = findings.get(item_id)
            current = plugin.library.get(item_id)
            if finding and current:
                findings.set(item_id, find(current, finding.get("fetched") or {}, is_preprint(current)))
            show(item_id)
        return {"applied": len(ids)}

    @plugin.route("/ignore", methods=["POST"])
    def ignore():
        from flask import request

        data = request.get_json(silent=True) or {}
        ids = [str(i) for i in data.get("ids") or []]
        field_name = data.get("field")
        val = data.get("value")

        done, later = [], []
        for item_id in ids:
            if not plugin.library.get(item_id):
                continue
            finding = findings.get(item_id)
            if field_name and val is not None:
                remember_ignore_field(item_id, field_name, str(val))
                show(item_id)
                done.append(item_id)
            elif finding is None:
                ignored.set(item_id, {"pending": True, "at": time.time()})
                later.append(item_id)
            else:
                remember_ignore(item_id, finding)
                show(item_id)
                done.append(item_id)
        if later:
            plugin.fields.refresh(FIELD, later)
        return {"ignored": done, "pending": later}

    @plugin.route("/ignore-paper", methods=["POST"])
    def ignore_paper():
        ids = _ids()
        for item_id in ids:
            item = plugin.library.get(item_id)
            if item:
                ignored_papers.set(item_id, {"title": item.get("title") or item_id, "at": time.time()})
                show(item_id)
        return {"ignored_papers": len(ignored_papers.all())}

    @plugin.route("/unignore-paper", methods=["POST"])
    def unignore_paper():
        ids = _ids()
        for item_id in ids:
            ignored_papers.delete(item_id)
            show(item_id)
        return {"ignored_papers": len(ignored_papers.all())}

    @plugin.route("/unignore", methods=["POST"])
    def unignore():
        ids = _ids()
        for item_id in ids:
            ignored.delete(item_id)
            show(item_id)
        return {"ignored": len(ignored.all())}

    @plugin.route("/ignored")
    def ignored_list():
        out = []
        for item_id, entry in ignored.all().items():
            item = plugin.library.get(item_id)
            what = (["published version " + entry["published"]] if entry.get("published") else []) + [
                f"{n}: {v}" for n, v in (entry.get("shown") or {}).items()
            ]
            out.append(
                {
                    "id": item_id,
                    "title": (item or {}).get("title") or entry.get("title") or item_id,
                    "in_library": item is not None,
                    "what": what,
                    "pending": bool(entry.get("pending")),
                    "at": entry.get("at"),
                }
            )

        out_papers = []
        for item_id, entry in ignored_papers.all().items():
            item = plugin.library.get(item_id)
            out_papers.append(
                {
                    "id": item_id,
                    "title": (item or {}).get("title") or entry.get("title") or item_id,
                    "in_library": item is not None,
                    "at": entry.get("at"),
                }
            )

        return {
            "ignored": sorted(out, key=lambda e: -(e["at"] or 0)),
            "ignored_papers": sorted(out_papers, key=lambda e: -(e["at"] or 0)),
        }
