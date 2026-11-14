"""arXiv Today: the papers announced on arXiv in the user's categories, ranked by how they connect
to the library, as one feed with a separate retained-day picker and a "+" on each paper.

How a day arrives:
1. ``listing`` (a job; the schedule queues it after each announcement, and changing the
   categories or "Fetch Now" does too) reads the RSS feed of the categories and stores the new
   papers, then scores them at once from authors and topics.
2. ``refs`` (a job per paper) reads its reference list, from its HTML version on arXiv first
   (the other services rarely know a new paper's references yet), and queues a rescoring.
3. ``score`` gives each recent paper its match (0–100, ``scoring.py``) from how specifically and
   how personally it connects to the library, and stores it (also as the field ``score``) with
   its tier and the reasons the item pane shows.
The frontend asks ``/news`` when the lists change, and says "12 new papers: 3 to read, 4 to
skim" once a day is scored.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.plugins import Plugin, PluginError, scholarly

from . import background, learning, listing, report, scoring
from .badges import day_variants, paper_badges
from .store import SCHEMA

RESCORE_DAYS = 3  # how many recent days a rescoring covers (older ones keep their scores)
# when even arXiv itself still lists the previous day's papers under the new date, look again
# this often, this many times (until mid-morning US Eastern after the 00:30 run)
CATCH_UP_WAIT_S = 30 * 60
CATCH_UP_TRIES = 12


def activate(plugin: Plugin) -> None:
    db = plugin.storage.sqlite("feed", migrations=SCHEMA)
    if background.backfill(db):
        plugin.log.info("Filled the scoring background from the listings kept so far")

    plugin.fields.declare(
        "score", type="float", label="arXiv Match", description="0–100: how likely you are to want to read it"
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
        authors = json.loads(r["authors"])
        why = json.loads(r["reasons"]) or (["Not scored yet"] if r["score"] is None else [])
        if r["score"] is not None and r["tier"]:
            line = f"Match {r['score']:.0f} · {scoring.TIER_TEXT.get(r['tier'], r['tier'])}"
            if r["score_v1"] is not None:
                line += f" (earlier score: {r['score_v1']:g})"
            why = [{"kind": "match", "text": line, "items": []}, *why]
        badges = paper_badges(authors, why, plugin.settings["followed_authors"])
        following = next((b["text"] for b in badges if b["label"] == "Following"), None)
        return {
            **(
                {"highlight": {"label": badges[0]["label"], "text": badges[0]["text"]}, "highlights": badges}
                if badges
                else {}
            ),
            "arxiv": r["arxiv"],
            "title": r["title"],
            "authors": authors,
            "abstract": r["abstract"],
            "categories": json.loads(r["categories"]),
            "date": r["day"],
            "year": int(r["day"][:4]),
            "item_type": "preprint",
            "reasons": ([{"kind": "following", "text": following}] if following else []) + why,
            **({"group": scoring.TIER_TEXT.get(r["tier"], r["tier"])} if r["tier"] else {}),
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
                    background.record_paper(c, e["arxiv"], day, e["authors"])
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
            background.record_refs(c, arxiv, keep)
        # rescore now and then while reference lists come in, and at once after the last one
        more = any(j["name"] == "refs" and j["key"] != f"refs:{arxiv}" for j in plugin.jobs.pending())
        plugin.jobs.enqueue("score", key="score", delay=30 if more else 0)

    def library_side(names: list[str]) -> tuple[scoring.Library, scoring.Background]:
        items = plugin.library.items()
        graph = plugin.library.citation_graph()
        try:
            notes, links = plugin.notes.list(), plugin.notes.links()
        except PluginError:  # an older manifest without "notes": ["read"]
            notes, links = [], []
        keys = set().union(*(scoring.item_keys(i) for i in items)) if items else set()
        for n in graph.get("nodes", []):
            if not n.get("in_library"):
                keys |= scoring.ref_keys({"doi": n.get("doi"), "arxiv_id": n.get("arxiv_id"), "title": n.get("title")})
        bg = background.load(db, keys, names)
        from_feed = {r["arxiv"] for r in db.query("SELECT arxiv FROM papers")}
        lib = scoring.build_library(
            items,
            notes=notes,
            links=links,
            graph=graph,
            author_names=plugin.settings["author_names"],
            from_feed=from_feed,
            background=bg,
        )
        return lib, bg

    @plugin.job("score")
    def score(days: int = RESCORE_DAYS) -> None:
        latest = latest_day()
        if not latest:
            return
        since = (date.fromisoformat(latest) - timedelta(days=days - 1)).isoformat()
        rows = db.query("SELECT * FROM papers WHERE day >= ? AND dismissed = 0", (since,))
        if not rows:
            return
        papers = [
            {
                "key": f"arxiv:{r['arxiv']}",
                "arxiv": r["arxiv"],
                "title": r["title"],
                "abstract": r["abstract"],
                "authors": json.loads(r["authors"]),
                "kind": r["kind"],
                "day": r["day"],
            }
            for r in rows
        ]
        refs = {p["key"]: json.loads(r["refs"]) for p, r in zip(papers, rows, strict=True) if r["refs"]}
        matched = {
            k: plugin.services.match(
                [
                    {
                        **r,
                        "doi": scholarly.norm_doi(r.get("doi")),
                        "arxiv_id": scholarly.norm_arxiv(r.get("arxiv_id")),
                    }
                    for r in v
                ]
            )
            for k, v in refs.items()
        }
        lib, bg = library_side([a for p in papers for a in p["authors"]])
        result = scoring.score(
            papers,
            lib,
            bg,
            refs=refs,
            matched=matched,
            weights=weights(),
            read_from=plugin.settings["read_from"],
            skim_from=plugin.settings["skim_from"],
        )
        # the earlier model, shown beside the match while the new one is checked
        earlier = plugin.services.connections(
            [{**record(r), "key": p["key"]} for p, r in zip(papers, rows, strict=True)],
            refs=refs,
            author_names=plugin.settings["author_names"],
        )
        with db.session() as c:
            c.executemany(
                "UPDATE papers SET score = ?, tier = ?, reasons = ?, score_v1 = ?, features = ? WHERE arxiv = ?",
                [
                    (
                        res["score"],
                        res["tier"],
                        json.dumps(res["reasons"]),
                        old["score"],
                        json.dumps(res["features"]),
                        r["arxiv"],
                    )
                    for r, res, old in zip(rows, result, earlier, strict=True)
                ],
            )
        plugin.fields.set_many("score", {p["key"]: res["score"] for p, res in zip(papers, result, strict=True)})
        plugin.refresh_sources()

    model = plugin.storage.json("model")

    def weights() -> dict[str, float] | None:
        """The learned weights while learning is on, else the hand-set ones (None)."""
        return (model.get("weights") or None) if plugin.settings["learn"] else None

    def library_added() -> dict[str, str]:
        """arXiv id -> when the library item with it was added."""
        out = {}
        for item in plugin.library.items():
            arx = scholarly.norm_arxiv(scholarly.identifiers(item)[1])
            if arx:
                out[arx] = str(item.get("date_added") or "")
        return out

    def collect_labels() -> int:
        """Labels from the papers kept now into ``labels`` (which outlives the listings)."""
        added = library_added()
        rows = db.query("SELECT * FROM papers WHERE features IS NOT NULL")
        found = []
        for r in rows:
            got = learning.label(dict(r), added.get(r["arxiv"]))
            if got:
                found.append((r["arxiv"], r["day"], got[0], got[1], r["features"]))
        with db.session() as c:
            # a paper with no label any more (an Undo) drops it
            c.executemany("DELETE FROM labels WHERE arxiv = ?", [(r["arxiv"],) for r in rows])
            c.executemany("INSERT INTO labels (arxiv, day, y, strength, features) VALUES (?, ?, ?, ?, ?)", found)
            cutoff = (date.today() - timedelta(days=learning.KEEP_DAYS)).isoformat()
            c.execute("DELETE FROM labels WHERE day < ?", (cutoff,))
        return len(found)

    @plugin.job("learn")
    def learn() -> None:
        collect_labels()
        samples = [(json.loads(r["features"]), r["y"], r["strength"]) for r in db.query("SELECT * FROM labels")]
        learned = learning.fit(samples)
        wanted = sum(1 for _, y, _ in samples if y >= 0.5)
        model.update({"checked_at": _now(), "labels": len(samples), "wanted": wanted})
        if learned:
            model.update({"fitted_at": _now(), "weights": learned})
        if learned:
            plugin.log.info("Learned weights from %d labels: %s", len(samples), learned)
            if plugin.settings["learn"]:
                plugin.jobs.enqueue("score", plugin.settings["keep_days"], key="score-all")
        else:
            plugin.log.info("Not enough labels to learn from yet (%d, %d wanted)", len(samples), wanted)

    # at night, so a day's reading has been seen (the server's local time)
    plugin.schedule("learning", lambda: plugin.jobs.enqueue("learn", key="learn"), cron="20 3 * * *")

    def forget_old() -> None:
        background.forget(db, (date.today() - timedelta(days=background.BACKGROUND_DAYS)).isoformat())
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

    def retry_announcement() -> None:
        # Empty/stale RSS responses are successful HTTP requests, not failed jobs. Keep
        # checking until this announcement is stored, including after exhausted HTTP retries.
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5 or (now.hour == 0 and now.minute < 30) or not categories():
            return
        rows = db.query("SELECT day FROM days WHERE day = ?", (now.date().isoformat(),))
        if not rows:
            plugin.jobs.enqueue("listing", key="listing")

    plugin.schedule("announcement-retry", retry_announcement, every="30m")

    @plugin.settings.on_change
    def settings_changed(changed: dict) -> None:
        if {"categories", "cross_lists", "replacements"} & set(changed):
            plugin.jobs.enqueue("listing", key="listing")
        if "followed_authors" in changed:
            plugin.refresh_sources()
        if {"author_names", "read_from", "skim_from", "learn"} & set(changed):
            plugin.jobs.enqueue("score", plugin.settings["keep_days"], key="score-all")
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
        rows = db.query("SELECT day, authors, reasons, seen, dismissed FROM papers ORDER BY day DESC")
        return day_variants(rows, plugin.settings["followed_authors"])

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

    @plugin.route("/opened", methods=["POST"])
    def opened():
        """A paper was looked at (shown for a while, or its PDF opened): the unopened papers listed
        above it in its day were passed over."""
        from flask import request

        arxiv = str((request.get_json(silent=True) or {}).get("id") or "")
        rows = db.query("SELECT day, opened_at FROM papers WHERE arxiv = ?", (arxiv,))
        if not rows:
            return {"opened": False}
        day = rows[0]["day"]
        order = [
            r["arxiv"]
            for r in db.query(
                "SELECT arxiv FROM papers WHERE day = ? AND dismissed = 0 ORDER BY score IS NULL, score DESC, arxiv",
                (day,),
            )
        ]
        above = order[: order.index(arxiv)] if arxiv in order else []
        with db.session() as c:
            c.execute("UPDATE papers SET opened_at = COALESCE(opened_at, ?) WHERE arxiv = ?", (_now(), arxiv))
            c.executemany("UPDATE papers SET passed = 1 WHERE arxiv = ? AND opened_at IS NULL", [(a,) for a in above])
        return {"opened": True, "passed": len(above)}

    @plugin.route("/explain")
    def explain():
        """What a paper's match rests on: each kind of evidence and how much it added."""
        from flask import request

        arxiv = str(request.args.get("id") or "")
        rows = db.query("SELECT score, tier, score_v1, features FROM papers WHERE arxiv = ?", (arxiv,))
        if not rows or rows[0]["features"] is None:
            return {"score": None}
        r = rows[0]
        w = {**scoring.WEIGHTS, **(weights() or {})}
        features = json.loads(r["features"])
        parts = [
            {"id": k, "label": scoring.FEATURE_LABELS[k], "value": round(w[k] * features.get(k, 0.0), 2)}
            for k in scoring.WEIGHTS
        ]
        return {"score": r["score"], "tier": r["tier"], "earlier": r["score_v1"], "parts": parts}

    @plugin.route("/model")
    def model_info():
        """The weights in use, learned and hand-set, for the settings page."""
        learned = model.get("weights") or {}
        return {
            "learning": bool(plugin.settings["learn"]),
            "fitted_at": model.get("fitted_at") if model.get("weights") else None,
            "checked_at": model.get("checked_at"),
            "labels": model.get("labels", 0),
            "wanted": model.get("wanted", 0),
            "needed": learning.MIN_EACH,
            "weights": [
                {"id": k, "label": scoring.FEATURE_LABELS[k], "default": v, "learned": learned.get(k)}
                for k, v in scoring.WEIGHTS.items()
            ],
        }

    @plugin.route("/model/reset", methods=["POST"])
    def model_reset():
        """Forget the learned weights (the labels stay, so the next night learns again)."""
        model.update({"weights": None, "fitted_at": None})
        plugin.jobs.enqueue("score", plugin.settings["keep_days"], key="score-all")
        return model_info()

    @plugin.route("/model/learn", methods=["POST"])
    def model_learn():
        return {"queued": plugin.jobs.enqueue("learn", key="learn")}

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
            "SELECT COUNT(*) AS n, SUM(tier = 'read') AS read, SUM(tier = 'skim') AS skim "
            "FROM papers WHERE day = ? AND dismissed = 0",
            (day,),
        )[0]
        if not stats["n"]:  # a day recorded without papers (an unfinished feed): nothing to tell yet
            return {"news": None}
        with db.session() as c:
            # Multiple windows may ask together; only the one claiming the day publishes it.
            if not c.execute("UPDATE days SET notified = 1 WHERE day = ? AND notified = 0", (day,)).rowcount:
                return {"news": None}
        return {"news": {"day": day, "count": stats["n"] or 0, "read": stats["read"] or 0, "skim": stats["skim"] or 0}}

    # ------------------------------------------------------------------ Claude
    @plugin.mcp_tool(
        "Papers announced on arXiv in the user's categories, best match first. `score` is the match "
        "(0–100: how likely the user wants to read it), `tier` read | skim | rest, `why` what it rests on "
        "(specific library papers it cites, references it shares with the library, shared authors, "
        "topic). `day` is YYYY-MM-DD (default: the latest announcement); `category` keeps papers listed "
        "in it; `min_score` is a match.",
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
                "tier": r["tier"],
                "why": [x.get("text") for x in json.loads(r["reasons"])],
                "in_library": in_library(r["arxiv"]),
                "url": f"https://arxiv.org/abs/{r['arxiv']}",
            }
            for r in rows[: max(1, min(limit, 200))]
        ]
        return {"day": day, "total": len(rows), "papers": papers}

    @plugin.mcp_tool(
        "How the match compares with the earlier score over the days kept: where the papers the user "
        "added to their library and the ones they dismissed ranked under each (within their day), how "
        "many papers fall in each tier, and the papers whose rank changed most, with the reasons. For "
        "checking and tuning the match.",
        title="arXiv match report",
    )
    def match_report(days: int = 30, top: int = 10) -> dict:
        since = (date.today() - timedelta(days=max(1, days))).isoformat()
        rows = db.query("SELECT * FROM papers WHERE day >= ?", (since,))
        mine = {scholarly.norm_arxiv(scholarly.identifiers(i)[1]) for i in plugin.library.items()}
        out = report.compare(rows, {a for a in mine if a}, top=max(1, min(top, 50)))
        if isinstance(out.get("days"), int) and 0 < out["days"] < days:  # only the days scored by both models
            out["note"] = f"{days} days were asked for; {out['days']} have scores from both models."
        return out

    # Stored reasons and scores must also reflect scoring changes after an upgrade.
    plugin.jobs.enqueue("score", plugin.settings["keep_days"], key="score-all")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
