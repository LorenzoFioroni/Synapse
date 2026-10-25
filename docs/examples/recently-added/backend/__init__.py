"""Recently Added: an example of a Synapse plugin (docs/plugins.md).

It shows each part of the plugin API once: settings from the manifest, a SQLite store with a
migration, an event listener, a schedule, reading the library, routes, a tool for Claude and two
per-item fields (one it sets itself, one Synapse asks it to fill). Its frontend
(frontend/main.js) adds a page, a command, an item-pane section and an item action.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.plugins import Plugin, PluginError

from .store import SCHEMA


def activate(plugin: Plugin) -> None:
    db = plugin.storage.sqlite("added", migrations=SCHEMA)

    # Per-item fields: each is a list column, a search filter (added_on:>2026-10-01,
    # author_count:>100), a row in the item pane and data Claude sees.
    plugin.fields.declare("added_on", type="date", label="Added On", description="When this plugin saw it added")

    def count_authors(papers: list[dict]) -> dict:
        # Synapse hands over the library items with no value yet (or older than `refresh`), in
        # batches; what comes back is stored. Citation counts work the same way, from the web.
        out = {}
        for p in papers:
            item = plugin.library.get(p["item_id"])
            names = [a for a in ((item or {}).get("authors") or "").split(";") if a.strip()]
            out[p["key"]] = len(names) if names else None
        return out

    plugin.fields.declare("author_count", type="int", label="Authors", refresh="1d", refresher=count_authors)

    def forget_old() -> None:
        cutoff = _ago(plugin.settings["keep_days"])
        with db.session() as c:
            c.execute("DELETE FROM added WHERE added_at < ?", (cutoff,))

    @plugin.on("item.added")
    def remember(event: dict) -> None:
        skip = {t.lower() for t in plugin.settings["skip_tags"]}
        with db.session() as c:
            for item_id in event["ids"]:
                item = plugin.library.get(item_id)
                if not item or skip & {t.lower() for t in item["tags"]}:
                    continue
                c.execute(
                    "INSERT OR REPLACE INTO added (item_id, title, added_at) VALUES (?, ?, ?)",
                    (item_id, item.get("title") or "", _now()),
                )
                plugin.fields.set(item, "added_on", _now()[:10])
        forget_old()

    # once a day (and at the first start), in the web app
    plugin.schedule("prune", forget_old, every="1d")

    @plugin.on("item.deleted")
    def forget(event: dict) -> None:
        with db.session() as c:
            c.executemany("DELETE FROM added WHERE item_id = ?", [(i,) for i in event["ids"]])

    @plugin.settings.on_change
    def settings_changed(changed: dict) -> None:
        plugin.log.info("Settings changed: %s", ", ".join(sorted(changed)))
        if "keep_days" in changed:
            forget_old()

    def recent(days: int) -> list[dict]:
        if not 1 <= days <= 3650:
            raise PluginError("days: between 1 and 3650")
        return db.query(
            "SELECT item_id, title, added_at FROM added WHERE added_at >= ? ORDER BY added_at DESC", (_ago(days),)
        )

    @plugin.route("/recent")
    def recent_route():
        from flask import request

        try:
            days = int(request.args.get("days", 7))
        except ValueError as e:
            raise PluginError("days must be a number") from e
        return {"items": recent(days)}

    @plugin.route("/item/<item_id>")
    def item_route(item_id: str):
        rows = db.query("SELECT added_at FROM added WHERE item_id = ?", (item_id,))
        return {"added_at": rows[0]["added_at"] if rows else None}

    @plugin.route("/forget", methods=["POST"])
    def forget_route():
        from flask import request

        ids = (request.get_json(silent=True) or {}).get("ids") or []
        if not isinstance(ids, list):
            raise PluginError("ids: a list of item ids")
        with db.session() as c:
            c.executemany("DELETE FROM added WHERE item_id = ?", [(str(i),) for i in ids])
        for i in ids:
            plugin.fields.set(str(i), "added_on", None)
        return {"forgotten": len(ids)}

    @plugin.mcp_tool("Items added to the library in the last days, newest first, with when each arrived.")
    def recently_added(days: int = 7) -> dict:
        return {"days": days, "items": recent(days)}

    plugin.log.info("Remembering additions for %s days", plugin.settings["keep_days"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
