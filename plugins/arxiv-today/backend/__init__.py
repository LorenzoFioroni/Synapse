"""arXiv Today: the papers announced on arXiv in the user's categories, ranked by how they connect
to the library, as one feed with a separate retained-day picker and a "+" on each paper.

How a day arrives:
1. ``listing`` (a job; the schedule queues it after each announcement, and changing the
   categories or "Fetch Now" does too) reads the RSS feed of the categories and stores the new
   papers, then scores them at once from authors and topics.
2. ``refs`` (a job per paper) reads its reference list, from its HTML version on arXiv first
   (the other services rarely know a new paper's references yet), and queues a rescoring.
3. ``score`` asks ``plugin.services.connections`` how each recent paper connects to the library
   and stores the score (also as the field ``score``) and the reasons the item pane shows.
The frontend asks ``/news`` when the lists change, and says "12 new papers, 3 connected to your
library" once a day is scored.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone

from backend.plugins import Plugin, PluginError

from . import listing
from .store import SCHEMA

RESCORE_DAYS = 3  # how many recent days a rescoring covers (older ones keep their scores)
# when even arXiv itself still lists the previous day's papers under the new date, look again
# this often, this many times (until mid-morning US Eastern after the 00:30 run)
CATCH_UP_WAIT_S = 30 * 60
CATCH_UP_TRIES = 12


def activate(plugin: Plugin) -> None:
    db = plugin.storage.sqlite("feed", migrations=SCHEMA)

    plugin.fields.declare(
        "score", type="float", label="arXiv Score", description="How the paper connects to your library"
    )
    plugin.fields.declare("announced", type="date", label="Announced", description="When arXiv announced it")
    plugin.fields.declare("categories", type="text", label="arXiv Categories")

    # ------------------------------------------------------------------ helpers
    def categories() -> list[str]:
        good, bad = listing.check_categories(plugin.settings["categories"])
        if bad:
            plugin.log.warning("Not arXiv categories, left out: %s", ", ".join(bad))
        return good

    def latest_day() -> str | None:
        rows = db.query("SELECT MAX(day) AS day FROM papers WHERE dismissed = 0")
        return rows[0]["day"] if rows else None

    def newest_stored() -> str | None:
        rows = db.query("SELECT MAX(day) AS day FROM papers")
        return rows[0]["day"] if rows else None

    def record(r: dict) -> dict:
        """A stored paper as a list entry (an outside paper, see docs/plugins.md)."""
        return {
            "arxiv": r["arxiv"],
            "title": r["title"],
            "authors": json.loads(r["authors"]),
            "abstract": r["abstract"],
            "categories": json.loads(r["categories"]),
            "date": r["day"],
            "year": int(r["day"][:4]),
            "item_type": "preprint",
            "reasons": json.loads(r["reasons"]) or (["Not scored yet"] if r["score"] is None else []),
        }

    def stored(arxiv: str) -> bool:
        return bool(db.query("SELECT 1 FROM papers WHERE arxiv = ?", (arxiv,)))

    def in_library(arxiv: str) -> bool:
        return plugin.library.find(arxiv=arxiv) is not None

    # ------------------------------------------------------------------ fetching
    def read_feed(cats: list[str], fresh: bool = False) -> tuple[str | None, list[dict]]:
        """The feed's day and the entries of the kinds chosen in the settings. ``fresh`` adds a
        query string arXiv's cache has not seen, so the answer comes from arXiv itself."""
        params = {"t": str(int(time.time()))} if fresh else None
        r = plugin.http.get(listing.url(cats), params=params, cache=not fresh)
        day, entries = listing.parse(r.text)
        kinds = {"new"}
        if plugin.settings["cross_lists"]:
            kinds.add("cross")
        if plugin.settings["replacements"]:
            kinds |= {"replace", "replace-cross"}
        return day, [e for e in entries if e["kind"] in kinds]

    def looks_stale(day: str, entries: list[dict]) -> bool:
        """Dated after the newest stored day, yet listing only papers already stored."""
        newest = newest_stored()
        return bool(newest and day > newest and all(stored(e["arxiv"]) for e in entries))

    @plugin.job("listing", retries=3)
    def fetch_listing(tries: int = 0) -> None:
        cats = categories()
        if not cats:
            plugin.log.info("No categories chosen (Settings → Plugins → arXiv Today)")
            return
        day, entries = read_feed(cats)
        if not day or not entries:
            plugin.log.info("No new papers in %s (no announcement today?)", ", ".join(cats))
            return
        if looks_stale(day, entries):
            # arXiv's cache can keep a feed dated for the new day that lists the previous day's
            # papers for up to 24 hours: ask arXiv itself, past the cache
            plugin.log.info("The cached feed for %s lists earlier papers; asking arXiv directly", day)
            day, entries = read_feed(cats, fresh=True)
            if not day or not entries:
                return
        if looks_stale(day, entries):
            newest = newest_stored()
            if tries < CATCH_UP_TRIES:
                plugin.log.info(
                    "The feed for %s still lists the papers of %s; looking again in %d min",
                    day,
                    newest,
                    CATCH_UP_WAIT_S // 60,
                )
                # its own key: the job running now still holds "listing" until it returns
                plugin.jobs.enqueue("listing", tries + 1, key="listing-again", delay=CATCH_UP_WAIT_S)
            else:
                plugin.log.warning("The feed for %s still lists the papers of %s; giving up for today", day, newest)
            return
        had = db.query("SELECT COUNT(*) AS n FROM papers WHERE day = ?", (day,))[0]["n"]
        added = []
        with db.session() as c:
            for e in entries:
                cur = c.execute(
                    "INSERT OR IGNORE INTO papers (arxiv, day, kind, title, authors, abstract, categories) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        e["arxiv"],
                        day,
                        e["kind"],
                        e["title"],
                        json.dumps(e["authors"]),
                        e["abstract"],
                        json.dumps(e["categories"]),
                    ),
                )
                if cur.rowcount:
                    added.append(e["arxiv"])
            c.execute(
                "INSERT INTO days (day, fetched_at, categories) VALUES (?, ?, ?) "
                "ON CONFLICT (day) DO UPDATE SET fetched_at = excluded.fetched_at, categories = excluded.categories",
                (day, _now(), ",".join(cats)),
            )
            if added and not had:  # the day's papers arrive now: tell about them even if it was claimed empty
                c.execute("UPDATE days SET notified = 0 WHERE day = ?", (day,))
        plugin.log.info("%s: %d papers, %d new", day, len(entries), len(added))
        plugin.fields.set_many("announced", {f"arxiv:{a}": day for a in added})
        plugin.fields.set_many(
            "categories", {f"arxiv:{e['arxiv']}": ", ".join(e["categories"]) for e in entries if e["arxiv"] in added}
        )
        if plugin.settings["references"]:
            for a in added:
                plugin.jobs.enqueue("refs", a, key=f"refs:{a}")
        score()  # at once, from authors and topics; the references refine it as they arrive
        forget_old()

    @plugin.job("refs", retries=3)
    def fetch_refs(arxiv: str) -> None:
        try:
            refs = plugin.services.references({"arxiv": arxiv}, new=True)
        except PluginError as e:  # (Offline is retried by the job queue)
            plugin.log.info("No references for %s: %s", arxiv, e)
            refs = []
        keep = [{k: r.get(k) for k in ("doi", "arxiv_id", "title", "year")} for r in refs]
        with db.session() as c:
            c.execute("UPDATE papers SET refs = ? WHERE arxiv = ?", (json.dumps(keep), arxiv))
        # rescore now and then while reference lists come in, and at once after the last one
        more = any(j["name"] == "refs" and j["key"] != f"refs:{arxiv}" for j in plugin.jobs.pending())
        plugin.jobs.enqueue("score", key="score", delay=30 if more else 0)

    @plugin.job("score")
    def score(days: int = RESCORE_DAYS) -> None:
        latest = latest_day()
        if not latest:
            return
        since = (date.fromisoformat(latest) - timedelta(days=days - 1)).isoformat()
        rows = db.query("SELECT * FROM papers WHERE day >= ? AND dismissed = 0", (since,))
        if not rows:
            return
        papers = [{**record(r), "key": f"arxiv:{r['arxiv']}"} for r in rows]
        refs = {f"arxiv:{r['arxiv']}": json.loads(r["refs"]) for r in rows if r["refs"]}
        result = plugin.services.connections(papers, refs=refs)
        with db.session() as c:
            c.executemany(
                "UPDATE papers SET score = ?, reasons = ? WHERE arxiv = ?",
                [(res["score"], json.dumps(res["reasons"]), r["arxiv"]) for r, res in zip(rows, result, strict=True)],
            )
        plugin.fields.set_many("score", {p["key"]: res["score"] for p, res in zip(papers, result, strict=True)})
        plugin.refresh_sources()

    def forget_old() -> None:
        cutoff = (date.today() - timedelta(days=plugin.settings["keep_days"])).isoformat()
        old = [r["arxiv"] for r in db.query("SELECT arxiv FROM papers WHERE day < ?", (cutoff,))]
        if not old:
            return
        gone = [f"arxiv:{a}" for a in old if not in_library(a)]  # an added paper keeps its values
        for name in ("score", "announced", "categories"):
            plugin.fields.clear(name, papers=gone)
        with db.session() as c:
            c.executemany("DELETE FROM papers WHERE arxiv = ?", [(a,) for a in old])
            c.execute("DELETE FROM days WHERE day < ?", (cutoff,))
        plugin.log.info("Forgot %d papers announced before %s", len(old), cutoff)

    # after each announcement (Monday to Friday, around midnight US Eastern; the feed is rebuilt then)
    plugin.schedule(
        "announcement",
        lambda: plugin.jobs.enqueue("listing", key="listing"),
        cron="30 0 * * 1-5",
        tz="America/New_York",
    )

    @plugin.settings.on_change
    def settings_changed(changed: dict) -> None:
        if {"categories", "cross_lists", "replacements"} & set(changed):
            plugin.jobs.enqueue("listing", key="listing")
        if "keep_days" in changed:
            forget_old()

    # the library changed: papers connect to it differently
    for event in ("item.added", "item.deleted", "item.trashed", "item.restored"):
        plugin.on(event, lambda e: plugin.jobs.enqueue("score", key="score", delay=10))

    # ------------------------------------------------------------------ the lists
    def unseen() -> int:
        day = latest_day()
        rows = db.query("SELECT COUNT(*) AS n FROM papers WHERE day = ? AND seen = 0 AND dismissed = 0", (day,))
        return rows[0]["n"] if day else 0

    def feed_days() -> list[dict]:
        rows = db.query(
            "SELECT day, SUM(seen = 0 AND dismissed = 0) AS unseen FROM papers GROUP BY day ORDER BY day DESC"
        )
        return [
            {"id": r["day"], "title": date.fromisoformat(r["day"]).strftime("%d %b %Y"), "badge": r["unseen"]}
            for r in rows
        ]

    @plugin.item_source(
        "feed",
        title="arXiv Feed",
        icon="today",
        description="Papers in your arXiv categories, grouped by retained announcement day and best connected to your library first.",
        badge=unseen,
        variants=feed_days,
    )
    def feed_list(day: str | None = None) -> list[dict]:
        day = day or latest_day()
        if not day:
            return []
        rows = db.query(
            "SELECT * FROM papers WHERE day = ? AND dismissed = 0 ORDER BY score IS NULL, score DESC, arxiv", (day,)
        )
        with db.session() as c:
            c.execute("UPDATE papers SET seen = 1 WHERE day = ? AND seen = 0", (day,))
        return [record(r) for r in rows]

    # Old source ids remain readable for saved navigation and external clients.
    @plugin.item_source("today", title="arXiv Today", hidden=True)
    def today_list() -> list[dict]:
        return feed_list()

    @plugin.item_source(
        "past",
        title="arXiv: Past Days",
        icon="calendar",
        description="Every retained paper, newest day first.",
        hidden=True,
    )
    def past_list() -> list[dict]:
        rows = db.query("SELECT * FROM papers WHERE dismissed = 0 ORDER BY day DESC, score IS NULL, score DESC, arxiv")
        return [record(r) for r in rows]

    # ------------------------------------------------------------------ routes for the frontend
    @plugin.route("/dismiss", methods=["POST"])
    def dismiss():
        from flask import request

        body = request.get_json(silent=True) or {}
        ids = [str(i) for i in body.get("ids") or []]
        with db.session() as c:
            n = sum(
                c.execute("UPDATE papers SET dismissed = ? WHERE arxiv = ?", (0 if body.get("undo") else 1, i)).rowcount
                for i in ids
            )
        plugin.refresh_sources()
        return {"changed": n}

    @plugin.route("/fetch", methods=["POST"])
    def fetch_now():
        if not categories():
            raise PluginError("Choose arXiv categories first (Settings → Plugins → arXiv Today)")
        return {"queued": plugin.jobs.enqueue("listing", key="listing")}

    @plugin.route("/news", methods=["POST"])
    def news():
        """The latest day, once it is scored (its reference lists read), if nobody was told yet."""
        if not plugin.settings["notify"]:
            return {"news": None}
        rows = db.query("SELECT * FROM days ORDER BY day DESC LIMIT 1")
        if not rows or rows[0]["notified"]:
            return {"news": None}
        day = rows[0]["day"]
        waiting = [j for j in plugin.jobs.pending() if j["name"] in ("listing", "refs", "score")]
        if waiting:
            return {"news": None}
        stats = db.query(
            "SELECT COUNT(*) AS n, SUM(score >= ?) AS connected FROM papers WHERE day = ? AND dismissed = 0",
            (plugin.settings["min_score"], day),
        )[0]
        if not stats["n"]:  # a day recorded without papers (an unfinished feed): nothing to tell yet
            return {"news": None}
        with db.session() as c:
            # Multiple windows may ask together; only the one claiming the day publishes it.
            if not c.execute("UPDATE days SET notified = 1 WHERE day = ? AND notified = 0", (day,)).rowcount:
                return {"news": None}
        return {"news": {"day": day, "count": stats["n"] or 0, "connected": stats["connected"] or 0}}

    # ------------------------------------------------------------------ Claude
    @plugin.mcp_tool(
        "Papers announced on arXiv in the user's categories, best connected to their library first, with "
        "why each connects (papers of theirs it cites, shared authors, similar topics). `day` is "
        "YYYY-MM-DD (default: the latest announcement); `category` keeps papers listed in it.",
        title="arXiv papers of the day",
    )
    def today(category: str | None = None, min_score: float = 0, day: str | None = None, limit: int = 30) -> dict:
        day = day or latest_day()
        if not day:
            return {"day": None, "papers": [], "note": "Nothing fetched yet: are categories chosen?"}
        rows = db.query(
            "SELECT * FROM papers WHERE day = ? AND dismissed = 0 AND COALESCE(score, 0) >= ? "
            "ORDER BY score IS NULL, score DESC",
            (day, min_score),
        )
        if category:
            rows = [r for r in rows if category in json.loads(r["categories"])]
        papers = [
            {
                "arxiv": r["arxiv"],
                "title": r["title"],
                "authors": json.loads(r["authors"]),
                "categories": json.loads(r["categories"]),
                "score": r["score"],
                "why": [x.get("text") for x in json.loads(r["reasons"])],
                "in_library": in_library(r["arxiv"]),
                "url": f"https://arxiv.org/abs/{r['arxiv']}",
            }
            for r in rows[: max(1, min(limit, 200))]
        ]
        return {"day": day, "total": len(rows), "papers": papers}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
