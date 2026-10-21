"""Recently Added: an example of a Synapse plugin (docs/plugins.md).

It shows each part of the plugin API once: settings from the manifest, a SQLite store with a
migration, an event listener, reading the library, a route and a tool for Claude.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.plugins import Plugin, PluginError

from .store import SCHEMA


def activate(plugin: Plugin) -> None:
    db = plugin.storage.sqlite("added", migrations=SCHEMA)

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
        forget_old()

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

    @plugin.mcp_tool("Items added to the library in the last days, newest first, with when each arrived.")
    def recently_added(days: int = 7) -> dict:
        return {"days": days, "items": recent(days)}

    plugin.log.info("Remembering additions for %s days", plugin.settings["keep_days"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
