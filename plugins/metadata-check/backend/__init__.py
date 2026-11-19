"""Periodic metadata audit: changed fields, published versions of arXiv preprints (announced by
arXiv or found by title), and newer arXiv versions of a preprint's PDF (``arxiv.py``).

What a check finds for an item is kept (``findings``) apart from what the field shows, so a
suggestion the user ignores can be hidden at once and shown again when they stop ignoring it,
without asking the metadata services again. An ignore remembers the values it was about: a later
check that finds a *different* title, or another published DOI, is a new suggestion and shows.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from threading import Event, Lock

from backend.plugins import Offline, Plugin, PluginError, scholarly

from . import arxiv
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
    # {item id: latest, updated, and the PDF's version: file, stamp, replaced, recorded}
    versions = plugin.storage.json("versions")
    notified = plugin.storage.json("notified")  # {item id: {"published": doi, "version": n}} already told
    news_lock = Lock()

    if not state.get("exact_rejections"):
        for item_id, entry in ignored.all().items():
            if entry.get("shown"):
                entry["fields"] = dict(entry["shown"])
                ignored.set(item_id, entry)
        state.set("exact_rejections", True)
    stopped = Event()
    plugin.on_unload(stopped.set)
    progress = Progress(state, lambda: None if stopped.is_set() else plugin.fields.changed())

    def offered_version(item_id: str, item: dict | None = None) -> dict | None:
        """The newer arXiv version of the item's PDF, with its abs page, or None (ignores aside)."""
        if not plugin.settings.get("arxiv_versions", True):
            return None
        item = item if item is not None else plugin.library.get(item_id)
        if not item or not arxiv.checks_versions(item):
            return None
        found = arxiv.newer(versions.get(item_id), arxiv.pdf_of(item))
        if found:
            found["url"] = f"https://arxiv.org/abs/{arxiv.bare_id(item)}v{found['version']}"
        return found

    def remember_ignore(item_id: str, finding: dict) -> dict:
        item = plugin.library.get(item_id) or {}
        before = ignored.get(item_id) or {}
        offered = offered_version(item_id, item)
        entry = ignore_entry(
            finding, item.get("title") or before.get("title") or "", before, offered["version"] if offered else None
        )
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
        if before.get("version"):
            entry["version"] = before["version"]
        finding = findings.get(item_id) or {}
        if field_name == "doi" and str((finding.get("fetched") or {}).get("doi")) == value:
            if finding.get("published"):
                entry["published"] = finding["published"]
        ignored.set(item_id, entry)
        return entry

    def check_versions(items: dict[str, dict]) -> None:
        """Ask arXiv for the latest version of the batch's preprints (one request for all of them)
        and note the version of each PDF, read from its stamp once per file."""
        wanted: dict[str, tuple[str, dict]] = {}  # item id: (arXiv id, its PDF)
        for item_id, item in items.items():
            arxiv_id, pdf = arxiv.bare_id(item), arxiv.pdf_of(item)
            if arxiv_id and pdf and item_id not in ignored_papers.all() and arxiv.checks_versions(item):
                wanted[item_id] = (arxiv_id, pdf)
        for item_id in items.keys() - wanted.keys():
            versions.delete(item_id)  # published, or no PDF any more
        if not wanted or not plugin.settings.get("arxiv_versions", True):
            return
        try:
            got = arxiv.latest(plugin.http, sorted({arxiv_id for arxiv_id, _ in wanted.values()}))
        except (PluginError, ET.ParseError) as error:  # offline, or arXiv answered oddly: next time
            plugin.log.info("Could not ask arXiv for versions: %s", error)
            return
        for item_id, (arxiv_id, pdf) in wanted.items():
            info = got.get(arxiv_id.lower())
            if not info:
                continue
            entry = versions.get(item_id) or {}
            if entry.get("file") != pdf["url"]:  # another file than the one last read
                entry = {
                    "file": pdf["url"],
                    "stamp": arxiv.stamped_version(plugin.library.pdf_text(pdf["id"]), arxiv_id),
                }
            entry.update(info, recorded=arxiv.recorded_version(items[item_id], arxiv_id))
            versions.set(item_id, entry)

    def published_record(item_id: str, current: dict, finding: dict) -> dict:
        """The finding with the published version's own record as the suggestion (its journal,
        volume, pages …), so that applying it turns the preprint into the published paper. When arXiv
        names no DOI, Crossref is searched by title. The arXiv ID stays: those records have none."""
        doi = finding.get("published")
        if arxiv.real_doi(current.get("doi")):
            return finding
        try:
            if not doi:
                doi = arxiv.published_by_title(plugin.http, current, plugin.services.contact_email())
                if not doi:
                    return finding
            _, record = plugin.services.lookup_item({"id": item_id, "doi": doi})
        except (PluginError, ValueError) as error:  # offline too: keep what arXiv said, ask again next time
            plugin.log.info("Could not look up the published version of %s: %s", item_id, error)
            return finding
        if arxiv.real_doi(record.get("doi")) != doi:
            return finding
        return find(current, record, True)

    def audit(papers: list[dict]) -> dict:
        out: dict = {}
        if not progress.running:
            due = plugin.fields.due(FIELD)
            progress.start([p["id"] for p in due])

        items = {str(p["item_id"]): plugin.library.get(str(p["item_id"])) or {} for p in papers}
        check_versions({k: v for k, v in items.items() if v})
        for paper in papers:
            if stopped.is_set():
                return {p["key"]: plugin.fields.LATER for p in papers}
            item_id = str(paper["item_id"])
            if item_id in ignored_papers.all():
                out[paper["key"]] = "Up to date · ignored paper"
                progress.done(item_id)
                continue

            current = items[item_id]
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
            preprint = is_preprint(current) or bool(paper.get("arxiv"))
            finding = find(current, found, preprint)
            if preprint:
                finding = published_record(item_id, current, finding)
            has_diffs = bool(finding.get("fields") or finding.get("published"))
            if has_diffs:
                findings.set(item_id, finding)
            else:
                findings.delete(item_id)

            entry = ignored.get(item_id)
            if entry and entry.get("pending"):
                entry = remember_ignore(item_id, finding)

            v = verdict(
                finding,
                entry,
                is_paper_ignored=(item_id in ignored_papers.all()),
                version=offered_version(item_id, current),
            )
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
        if "arxiv_versions" in changed:
            for item_id in versions.all():
                show(item_id)

    def show(item_id: str) -> None:
        if item_id in ignored_papers.all():
            plugin.fields.set(item_id, FIELD, "Up to date · ignored paper")
            return
        finding = findings.get(item_id)
        current = plugin.library.get(item_id)
        if finding is not None and current and "fetched" in finding:
            finding = find(current, finding["fetched"], is_preprint(current))
        text = verdict(finding or {}, ignored.get(item_id), version=offered_version(item_id, current))
        value, url = text if isinstance(text, tuple) else (text, None)
        plugin.fields.set(item_id, FIELD, value, source_url=url or None)

    @plugin.on("item.deleted")
    def _forget(event: dict) -> None:
        for item_id in event.get("ids") or []:
            for store in (findings, ignored, ignored_papers, versions, notified):
                store.delete(str(item_id))

    def review_articles() -> list[dict]:
        out = []
        for item_id in sorted(findings.all().keys() | versions.all().keys()):
            if ignored_papers.get(item_id):
                continue
            item = plugin.library.get(item_id)
            if not item:
                continue
            stored = findings.get(item_id) or {}
            fetched = stored.get("fetched") or {}
            finding = find(item, fetched, is_preprint(item))
            entry = ignored.get(item_id) or {}
            ignored_fields = entry.get("fields") or {}
            pub = finding.get("published")
            pub_active = pub and pub != entry.get("published")
            changes = [
                c
                for c in scholarly.compute_changes(item, fetched)
                if c["checked"]
                and ignored_fields.get(c["key"]) != str(c["fetched"])
                and not (c["key"] == "doi" and pub and not pub_active)
            ]
            offered = offered_version(item_id, item)
            if offered and offered["version"] == entry.get("version"):
                offered = None
            if offered:
                pdf = arxiv.pdf_of(item)
                offered = {
                    **offered,
                    "detail": arxiv.describe(offered),
                    "highlights": plugin.library.annotation_count(pdf["id"]) if pdf else 0,
                }
            if changes or pub_active or offered:
                out.append(
                    {
                        "id": item_id,
                        "current": item,
                        "fetched": fetched,
                        "changes": changes,
                        "published": pub if pub_active else None,
                        "version": offered,
                    }
                )
        return out

    def news() -> list[dict]:
        """Published and newer arXiv versions not announced yet, marked as announced: one window
        tells the user about each (the claim is atomic), and again only when another turns up."""
        with news_lock:
            out = []
            for a in review_articles():
                told = notified.get(a["id"]) or {}
                now = {"published": a["published"], "version": (a["version"] or {}).get("version")}
                fresh = (now["published"] and now["published"] != told.get("published")) or (
                    now["version"] and now["version"] != told.get("version")
                )
                if now["published"] or now["version"]:
                    notified.set(a["id"], {**told, **{k: v for k, v in now.items() if v}})
                if fresh:
                    out.append({"id": a["id"], "title": a["current"].get("title") or "", **now})
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

    @plugin.route("/news", methods=["POST"])
    def news_route():
        """Claim what to notify about: published versions and newer arXiv versions found lately."""
        return {"news": news()}

    @plugin.route("/replace", methods=["POST"])
    def replace():
        """Put the newer arXiv version in place of the item's PDF (the user confirmed, knowing that
        its highlights go): downloaded from arXiv, then replaced through the app."""
        ids = _ids()
        item_id = ids[0] if ids else ""
        item = plugin.library.get(item_id)
        offered = offered_version(item_id, item) if item else None
        if offered and offered["version"] == (ignored.get(item_id) or {}).get("version"):
            offered = None  # the user chose to keep the current PDF
        if not item or not offered:
            raise PluginError("There is no newer arXiv version to replace this PDF with")
        arxiv_id, pdf = arxiv.bare_id(item), arxiv.pdf_of(item)
        if not arxiv_id or not pdf:
            raise PluginError("This item has no arXiv ID or no PDF to replace")
        r = plugin.http.get(f"https://arxiv.org/pdf/{arxiv_id}v{offered['version']}", cache=False)
        done = plugin.actions.replace_pdf(pdf["id"], r.content)
        entry = versions.get(item_id) or {}
        entry.update(
            file=done["attachment"]["url"],
            stamp=None,
            replaced=offered["version"],
            latest=max(int(entry.get("latest") or 0), offered["version"]),
        )
        versions.set(item_id, entry)
        show(item_id)
        return {"version": offered["version"], "removed_annotations": done["removed_annotations"]}

    @plugin.route("/keep-current", methods=["POST"])
    def keep_current():
        """Keep the PDF as it is: this version is not offered again, a later one is."""
        for item_id in _ids():
            offered = offered_version(item_id)
            if offered:
                before = ignored.get(item_id) or {}
                if before.get("pending"):
                    before = {}
                item = plugin.library.get(item_id) or {}
                ignored.set(
                    item_id,
                    {
                        **before,
                        "title": item.get("title") or before.get("title") or "",
                        "version": offered["version"],
                        "at": time.time(),
                    },
                )
                show(item_id)
        return {"ok": True}

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
            elif finding is None and offered_version(item_id):
                remember_ignore(item_id, {})
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
            what = (
                (["published version " + entry["published"]] if entry.get("published") else [])
                + ([f"arXiv v{entry['version']}"] if entry.get("version") else [])
                + [f"{n}: {v}" for n, v in (entry.get("shown") or {}).items()]
            )
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
