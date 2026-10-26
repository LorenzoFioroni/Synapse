"""Periodic metadata audit, including journal versions announced by arXiv.

What a check finds for an item is kept (``findings``) apart from what the field shows, so a
suggestion the user ignores can be hidden at once and shown again when they stop ignoring it,
without asking the metadata services again. An ignore remembers the values it was about: a later
check that finds a *different* title, or another published DOI, is a new suggestion and shows."""

from __future__ import annotations

import re
import time
import unicodedata

from backend.plugins import Offline, Plugin, PluginError

FIELD = "metadata_check"
CHECK_FIELDS = ("title", "authors", "year", "journal", "volume", "issue", "pages", "publisher")
DEFAULT_DAYS = 3


def _plain(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)).lower())


def _real_doi(value) -> str:
    doi = str(value or "").lower().removeprefix("https://doi.org/")
    return "" if doi.startswith("10.48550/arxiv.") else doi


def _days(value) -> float:
    try:
        return max(1.0, float(value))
    except (TypeError, ValueError):
        return float(DEFAULT_DAYS)


def find(current: dict, found: dict, arxiv: bool) -> dict:
    """What differs between the library record and the metadata found for it: ``published`` (a
    journal DOI of an arXiv preprint) and ``fields`` ({name: the value found})."""
    out: dict = {"fields": {}}
    published = _real_doi(found.get("doi"))
    if arxiv and published and published != _real_doi(current.get("doi")):
        out["published"] = published
    for name in CHECK_FIELDS:
        if found.get(name) and _plain(found[name]) != _plain(current.get(name)):
            out["fields"][name] = str(found[name])[:300]
    return out


def verdict(finding: dict, ignored: dict | None) -> str | tuple[str, str]:
    """The field's text for a finding, leaving out what the user ignored (when it is still the
    same suggestion)."""
    ignored = ignored or {}
    gone = ignored.get("fields") or {}
    published = finding.get("published")
    if published and published != ignored.get("published"):
        return (f"Published version: {published}", f"https://doi.org/{published}")
    changed = [n for n, v in finding["fields"].items() if gone.get(n) != _plain(v)]
    if changed:
        return f"Review: {', '.join(changed)}"
    hidden = (["published version"] if published else []) + list(finding["fields"])
    return f"Up to date · ignored {', '.join(hidden)}" if hidden else "Up to date"


def ignore_entry(finding: dict, title: str, before: dict | None = None) -> dict:
    """What is remembered when the user ignores a finding (added to an earlier ignore)."""
    before = before or {}
    fields = {**(before.get("fields") or {}), **{n: _plain(v) for n, v in finding["fields"].items()}}
    shown = {**(before.get("shown") or {}), **finding["fields"]}
    out = {"title": title, "fields": fields, "shown": shown, "at": time.time()}
    published = finding.get("published") or before.get("published")
    if published:
        out["published"] = published
    return out


def _ids() -> list[str]:
    from flask import request

    return [str(i) for i in (request.get_json(silent=True) or {}).get("ids") or []]


def activate(plugin: Plugin) -> None:
    state = plugin.storage.json("state")  # last_checked
    findings = plugin.storage.json("findings")  # {item id: finding}
    ignored = plugin.storage.json("ignored")  # {item id: ignore entry, or {"pending": True}}

    def remember_ignore(item_id: str, finding: dict) -> dict:
        item = plugin.library.get(item_id) or {}
        before = ignored.get(item_id) or {}
        entry = ignore_entry(finding, item.get("title") or before.get("title") or "", before)
        ignored.set(item_id, entry)
        return entry

    def audit(papers: list[dict]) -> dict:
        out: dict = {}
        if papers:
            state.set("last_checked", time.time())
        for paper in papers:
            current = plugin.library.get(paper["item_id"]) or {}
            identifier = f"arXiv:{paper['arxiv']}" if paper.get("arxiv") else paper.get("doi")
            if not identifier:
                out[paper["key"]] = "Needs a DOI or arXiv ID"
                continue
            try:
                found = plugin.services.lookup(identifier)
            except Offline as error:
                plugin.log.info("Could not check %s: %s", identifier, error)
                out[paper["key"]] = plugin.fields.LATER
                continue
            except PluginError as error:
                out[paper["key"]] = f"Could not verify: {error}"
                continue
            finding = find(current, found, bool(paper.get("arxiv")))
            findings.set(paper["item_id"], finding)
            entry = ignored.get(paper["item_id"])
            if entry and entry.get("pending"):  # ignored before this check had found anything
                entry = remember_ignore(paper["item_id"], finding)
            out[paper["key"]] = verdict(finding, entry)
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

    @plugin.settings.on_change
    def _interval(changed: dict) -> None:
        if "interval_days" in changed:
            field.refresh = _days(changed["interval_days"]) * 86400
            plugin.fields.refresh_soon()  # a shorter wait may make some values due now

    def show(item_id: str) -> None:
        finding = findings.get(item_id)
        if finding is not None:
            text = verdict(finding, ignored.get(item_id))
            value, url = text if isinstance(text, tuple) else (text, None)
            plugin.fields.set(item_id, FIELD, value, source_url=url)

    @plugin.on("item.deleted")
    def _forget(event: dict) -> None:
        for item_id in event.get("ids") or []:
            findings.delete(str(item_id))
            ignored.delete(str(item_id))

    @plugin.route("/status")
    def status():
        return {
            "last_checked": state.get("last_checked"),
            "interval_days": _days(plugin.settings.get("interval_days", DEFAULT_DAYS)),
            "ignored": len(ignored.all()),
        }

    @plugin.route("/run", methods=["POST"])
    def run_now():
        """Check every library item again soon, whatever the interval says."""
        plugin.fields.refresh(FIELD)
        return {"items": len(plugin.library.items())}

    @plugin.route("/ignore", methods=["POST"])
    def ignore():
        ids = _ids()
        done, later = [], []
        for item_id in ids:
            if not plugin.library.get(item_id):
                continue
            finding = findings.get(item_id)
            if finding is None:  # a value from before findings were kept: ignore what the next check finds
                ignored.set(item_id, {"pending": True, "at": time.time()})
                later.append(item_id)
            else:
                remember_ignore(item_id, finding)
                show(item_id)
                done.append(item_id)
        if later:
            plugin.fields.refresh(FIELD, later)
        return {"ignored": done, "pending": later}

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
        return {"ignored": sorted(out, key=lambda e: -(e["at"] or 0))}
