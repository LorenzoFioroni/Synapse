"""Suggested Reading: an example of a plugin that offers papers from outside the library
(docs/plugins.md, "Papers outside the library").

The papers are the arXiv ids and DOIs in its setting. For each, a background job looks up its
metadata and its reference list (``plugin.services``); another job scores how each connects to the
library (``plugin.services.connections``: the library papers it cites and that cite it, shared
authors, similar titles and abstracts). The list shows in the sidebar (``@plugin.item_source``),
best connected first, with the reasons in the item pane and a "+" that adds a paper. The score is
also a per-item field, so it is a column, a filter (``connection:>5``) and stays with a paper once
it is added.
"""

from __future__ import annotations

from backend.plugins import Plugin, PluginError


def activate(plugin: Plugin) -> None:
    cache = plugin.storage.json("papers")  # identifier -> {"record", "refs"}
    scores = plugin.storage.json("scores")  # paper key -> {"score", "reasons"}

    plugin.fields.declare(
        "connection",
        type="float",
        label="Connection",
        description="How strongly a suggested paper connects to your library",
    )

    def wanted() -> list[str]:
        return [s.strip() for s in plugin.settings["papers"] if s.strip()]

    @plugin.job("fetch", retries=3)
    def fetch(identifier: str) -> None:
        # Offline (no network) makes the job wait and try again; a PluginError (no such paper) is
        # logged and the paper left out
        rec = plugin.services.lookup(identifier)
        try:
            refs = plugin.services.references(rec)
        except PluginError as e:
            plugin.log.info("No references for %s: %s", identifier, e)
            refs = []
        cache.set(identifier, {"record": rec, "refs": refs})
        plugin.jobs.enqueue("rescore", key="rescore", delay=1)

    @plugin.job("rescore")
    def rescore() -> None:
        entries = [e for e in (cache.get(i) for i in wanted()) if e]
        if not entries:
            return
        papers = [e["record"] for e in entries]
        result = plugin.services.connections(papers, refs={e["record"]["key"]: e["refs"] for e in entries})
        scores.update({p["key"]: r for p, r in zip(papers, result, strict=True)})
        plugin.fields.set_many("connection", {p["key"]: r["score"] for p, r in zip(papers, result, strict=True)})
        plugin.refresh_sources()  # pages showing the list ask for it again

    def sync() -> None:
        for identifier in wanted():
            if not cache.get(identifier):
                plugin.jobs.enqueue("fetch", identifier, key=f"fetch:{identifier}")
        plugin.jobs.enqueue("rescore", key="rescore", delay=1)

    @plugin.settings.on_change
    def papers_changed(changed: dict) -> None:
        if "papers" in changed:
            sync()

    # a paper added to or removed from the library changes how the others connect to it
    plugin.on("item.added", lambda event: plugin.jobs.enqueue("rescore", key="rescore", delay=5))
    plugin.on("item.deleted", lambda event: plugin.jobs.enqueue("rescore", key="rescore", delay=5))

    def unread() -> int:
        """For the sidebar's badge: how many of the papers are not in the library yet."""
        recs = [e["record"] for e in (cache.get(i) for i in wanted()) if e]
        return sum(1 for r in recs if not plugin.library.find(doi=r.get("doi"), arxiv=r.get("arxiv")))

    @plugin.item_source(
        "suggested",
        title="Suggested Reading",
        icon="sparkle",
        description="Papers from the Suggested Reading settings, best connected to your library first.",
        badge=unread,
    )
    def suggested() -> list[dict]:
        out = []
        for identifier in wanted():
            e = cache.get(identifier)
            if not e:
                continue  # not fetched yet
            s = scores.get(e["record"]["key"]) or {}
            out.append({**e["record"], "reasons": s.get("reasons") or ["Not scored yet"], "_score": s.get("score", 0)})
        return sorted(out, key=lambda p: -p["_score"])

    sync()
