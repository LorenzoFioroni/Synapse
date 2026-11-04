"""The arXiv Today plugin (plugins/arxiv-today) with a fake RSS feed and fake reference lists: the
listing, scoring, its two lists, dismissing, the morning's news, Claude's tool, forgetting old
days; and the RSS parser."""

import json
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import urlsplit

import pytest

from backend.mcp import build_server
from backend.plugins import http as H
from backend.references import sources as ref_sources

PID = "arxiv-today"
TODAY = f"/api/items?view=source:{PID}.today&lean=1"
PAST = f"/api/items?view=source:{PID}.past&lean=1"
FEED = f"/api/items?view=source:{PID}.feed&lean=1"


def rss(day: date, items: list[tuple]) -> str:
    stamp = format_datetime(datetime(day.year, day.month, day.day, 4, 0, tzinfo=timezone.utc))
    body = "".join(
        f"""<item><title>{title}</title><link>https://arxiv.org/abs/{aid}</link>
        <description>arXiv:{aid}v1 Announce Type: {kind}
Abstract: {abstract}</description>
        <guid isPermaLink="false">oai:arXiv.org:{aid}v1</guid>
        {"".join(f"<category>{c}</category>" for c in cats)}
        <arxiv:announce_type>{kind}</arxiv:announce_type>
        <dc:creator>{authors}</dc:creator></item>"""
        for aid, kind, title, authors, abstract, cats in items
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
<channel><title>quant-ph updates on arXiv.org</title><pubDate>{stamp}</pubDate>{body}</channel></rss>"""


PAPERS = [
    (
        "2610.00001",
        "new",
        "Contracting tensor networks faster",
        "Jane Doe, Ann Other",
        "We contract tensor networks for quantum circuits.",
        ["quant-ph"],
    ),
    (
        "2610.00002",
        "cross",
        "Volcanic ash in the stratosphere",
        "Ray Volcano and Sam Ash",
        "Ash clouds and their optics.",
        ["physics.ao-ph", "quant-ph"],
    ),
    ("2609.09999", "replace", "An old paper, new version", "Old Author", "Revised.", ["quant-ph"]),
]


class Feed:
    def __init__(self):
        self.day = date.today()
        self.items = PAPERS
        self.uncached: list[tuple] | None = None  # what arXiv itself lists when its cache is behind
        self.urls: list[str] = []

    def __call__(self, method, url, headers=None, **kw):
        self.urls.append(url)
        assert urlsplit(url).hostname == "rss.arxiv.org", url
        past_cache = "?t=" in url
        items = self.uncached if past_cache and self.uncached is not None else self.items
        body = rss(self.day, items).encode()

        class R:
            status_code, reason, headers, encoding = 200, "OK", {}, "utf-8"

            def iter_content(self, n):
                yield body

            def close(self):
                pass

        return R()


@pytest.fixture
def feed(monkeypatch, bundled):
    f = Feed()
    monkeypatch.setattr(H.requests, "request", f)
    refs = {"arXiv:2610.00001": [{"doi": "10.1/mine", "title": "Mine"}]}
    monkeypatch.setattr(
        ref_sources, "fetch", lambda item, fresh=False, **kw: ("test", refs.get(item["archive_id"], []))
    )
    return f


def wait(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def plugin(client, svc, feed):
    client.post(
        "/api/items",
        json={"title": "Tensor network methods for quantum circuits", "doi": "10.1/mine", "authors": "Doe, Jane"},
    )
    svc.plugins.private_network = True
    client.put(f"/api/plugin-host/{PID}/settings", json={"categories": ["quant-ph", "not a category"]})
    assert client.post(f"/api/plugin-host/{PID}/enable").get_json()["plugin"]["running"]
    p = svc.plugins._active[PID].plugin
    assert wait(lambda: not p.jobs.pending() and _scored(client))
    return p


def _scored(client):
    items = client.get(PAST).get_json().get("items") or []  # (showing today's list marks it seen)
    return len(items) == 2 and all(
        i.get("reasons") != [{"text": "Not scored yet", "kind": "", "items": []}] for i in items
    )


def test_a_day_arrives(client, svc, feed, plugin):
    assert feed.urls[0].startswith("https://rss.arxiv.org/rss/quant-ph")  # the bad category left out
    src = {s["id"]: s for s in client.get("/api/plugin-sources").get_json()["sources"]}
    assert list(src) == [f"{PID}.feed"]
    assert src[f"{PID}.feed"]["badge"] == "2"  # unseen
    assert src[f"{PID}.feed"]["variants"][0]["id"] == date.today().isoformat()
    items = client.get(FEED).get_json()["items"]
    first, second = items
    assert first["id"] == "x:arxiv:2610.00001" and first["_outside"]  # cites a library paper, shares an author
    assert {r["kind"] for r in first["reasons"]} >= {"cites", "authors"}
    assert second["id"] == "x:arxiv:2610.00002"  # a cross-list; the replacement is left out
    assert first["authors"] == "Doe, Jane; Other, Ann" and first["categories"] == "quant-ph"
    assert first["_f"][f"{PID}.announced"] == date.today().isoformat()
    assert first["_f"][f"{PID}.score"] > second["_f"].get(f"{PID}.score", 0)
    assert second["_f"][f"{PID}.categories"] == "physics.ao-ph, quant-ph"
    assert client.get("/api/plugin-sources").get_json()["sources"][0]["badge"] is None  # seen now
    assert len(client.get(PAST).get_json()["items"]) == 2
    # filters on its fields work on the list
    found = client.get(f"/api/items?view=source:{PID}.feed~{date.today().isoformat()}&q=categories:ao-ph").get_json()[
        "items"
    ]
    assert [i["id"] for i in found] == ["x:arxiv:2610.00002"]


def test_dismiss_and_news(client, svc, plugin):
    r = client.post(f"/api/plugins/{PID}/dismiss", json={"ids": ["2610.00002"]}).get_json()
    assert r == {"changed": 1}
    assert [i["id"] for i in client.get(TODAY).get_json()["items"]] == ["x:arxiv:2610.00001"]
    client.post(f"/api/plugins/{PID}/dismiss", json={"ids": ["2610.00002"], "undo": True})
    assert len(client.get(TODAY).get_json()["items"]) == 2
    news = client.post(f"/api/plugins/{PID}/news").get_json()["news"]
    assert news == {"day": date.today().isoformat(), "count": 2, "connected": 1}
    assert client.post(f"/api/plugins/{PID}/news").get_json()["news"] is None  # once


def test_concurrent_windows_only_claim_daily_news_once(app, plugin):
    from concurrent.futures import ThreadPoolExecutor

    def claim(_):
        with app.test_client() as window:
            return window.post(f"/api/plugins/{PID}/news").get_json()["news"]

    with ThreadPoolExecutor(max_workers=6) as windows:
        notices = list(windows.map(claim, range(6)))
    assert sum(n is not None for n in notices) == 1


def test_adding_keeps_the_score(client, svc, plugin):
    paper = client.get(TODAY).get_json()["items"][0]
    item = client.post("/api/outside/add", json={"paper": paper}).get_json()["item"]
    assert client.get(TODAY).get_json()["items"][0]["id"] == item["id"]
    fields = {f["name"]: f["value"] for f in client.get(f"/api/items/{item['id']}/fields").get_json()["fields"]}
    assert fields["score"] > 0 and fields["announced"] == date.today().isoformat()


def test_claude(client, svc, plugin):
    server = build_server(svc)
    r = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": f"{PID}__today", "arguments": {"min_score": 1}},
        }
    )["result"]
    data = r.get("structuredContent") or json.loads(r["content"][0]["text"])
    assert data["day"] == date.today().isoformat() and data["total"] == 1
    p = data["papers"][0]
    assert p["arxiv"] == "2610.00001" and any("Cites" in w for w in p["why"]) and p["in_library"] is False


def test_old_days_are_forgotten(client, svc, feed, plugin):
    feed.day = date.today() - timedelta(days=40)
    feed.items = [("2609.00042", "new", "Long ago", "Some One", "Old.", ["quant-ph"])]
    assert client.post(f"/api/plugins/{PID}/fetch").get_json()["queued"]
    assert wait(lambda: len(feed.urls) >= 2 and not plugin.jobs.pending())
    ids = [i["id"] for i in client.get(PAST).get_json()["items"]]
    assert "x:arxiv:2609.00042" not in ids and len(ids) == 2
    assert "arxiv:2609.00042" not in svc.plugins.fields.store.rows(PID, "announced")


def test_a_feed_dated_ahead_of_its_papers_is_read_again(client, svc, feed, plugin):
    """arXiv may date the feed for the new day while it still lists the previous day's papers."""
    import sqlite3

    tomorrow = date.today() + timedelta(days=1)
    db = plugin.jobs._folder / "feed.sqlite"
    with sqlite3.connect(db) as c:  # what the old version recorded for such a feed
        c.execute(
            "INSERT INTO days (day, fetched_at, categories, notified) VALUES (?, 'x', 'quant-ph', 1)", (str(tomorrow),)
        )
    feed.day = tomorrow  # the same papers under tomorrow's date
    assert client.post(f"/api/plugins/{PID}/fetch").get_json()["queued"]
    assert wait(lambda: any(j["key"] == "listing-again" for j in plugin.jobs.pending()))
    again = next(j for j in plugin.jobs.pending() if j["key"] == "listing-again")
    assert json.loads(again["args"]) == [1] and again["run_after"] > time.time() + 25 * 60
    days = [i["id"] for i in client.get("/api/plugin-sources").get_json()["sources"][0]["variants"]]
    assert days == [date.today().isoformat()]  # the papers kept their day
    assert client.post(f"/api/plugins/{PID}/news").get_json()["news"] is None

    feed.items = [("2610.00077", "new", "Tomorrow's paper", "New Author", "Fresh.", ["quant-ph"])]
    with plugin.jobs.queue._connect() as c:  # (bring the second look forward)
        c.execute("UPDATE jobs SET run_after = 0 WHERE key = 'listing-again'")
    plugin.wake()
    assert wait(lambda: not plugin.jobs.pending() and len(client.get(PAST).get_json()["items"]) == 3)
    days = [i["id"] for i in client.get("/api/plugin-sources").get_json()["sources"][0]["variants"]]
    assert days == [tomorrow.isoformat(), date.today().isoformat()]
    news = client.post(f"/api/plugins/{PID}/news").get_json()["news"]  # told although marked told before
    assert news and news["day"] == tomorrow.isoformat() and news["count"] == 1


def test_a_stale_cached_feed_is_asked_again_past_the_cache(client, svc, feed, plugin):
    """28 Oct 2026: arXiv's cache served the new day's date over the previous day's papers all day."""
    tomorrow = date.today() + timedelta(days=1)
    feed.day = tomorrow
    feed.uncached = [("2610.36047", "new", "Wednesday's paper", "New Author", "Fresh.", ["quant-ph"])]
    seen = len(feed.urls)
    assert client.post(f"/api/plugins/{PID}/fetch").get_json()["queued"]
    assert wait(lambda: not plugin.jobs.pending() and len(client.get(PAST).get_json()["items"]) == 3)
    asked = feed.urls[seen:]
    assert "?t=" not in asked[0] and "?t=" in asked[1]  # the cache first, arXiv itself only when stale
    days = [i["id"] for i in client.get("/api/plugin-sources").get_json()["sources"][0]["variants"]]
    assert days == [tomorrow.isoformat(), date.today().isoformat()]
    assert not any(j["key"] == "listing-again" for j in plugin.jobs.pending())


def test_no_categories(client, svc, feed):
    svc.plugins.private_network = True
    client.post(f"/api/plugin-host/{PID}/enable")
    r = client.post(f"/api/plugins/{PID}/fetch")
    assert r.status_code == 400 and "categories" in r.get_json()["error"]
    assert client.get(TODAY).get_json()["items"] == [] and feed.urls == []


def _listing():
    import importlib.util

    from backend.config import APP_ROOT

    spec = importlib.util.spec_from_file_location(
        "arxiv_today_listing", APP_ROOT / "plugins" / PID / "backend" / "listing.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_rss_parser():
    listing = _listing()
    xml = rss(date(2026, 10, 22), [*PAPERS, ("quant-ph/0101001", "replace-cross", "Old style", "A. B", "x", [])])
    day, entries = listing.parse(xml)
    assert day == "2026-10-22"
    assert [e["arxiv"] for e in entries] == ["2610.00001", "2610.00002", "2609.09999", "quant-ph/0101001"]
    e = entries[1]
    assert e["kind"] == "cross" and e["authors"] == ["Ray Volcano", "Sam Ash"]
    assert e["abstract"] == "Ash clouds and their optics." and e["categories"] == ["physics.ao-ph", "quant-ph"]
    assert listing.parse(
        '<rss version="2.0"><channel><pubDate>Sat, 24 Oct 2026 00:00:00 -0400</pubDate></channel></rss>'
    ) == (
        "2026-10-24",
        [],
    )
    assert listing.check_categories(["quant-ph", " cond-mat.str-el", "hep th", "", "cs.LG"]) == (
        ["quant-ph", "cond-mat.str-el", "cs.LG"],
        ["hep th"],
    )
    assert listing.url(["quant-ph", "cs.LG"]) == "https://rss.arxiv.org/rss/quant-ph+cs.LG"


def test_authored_library_papers_have_a_stronger_citation_signal(client, svc, plugin):
    before = client.get(FEED).get_json()["items"][0]
    assert next(r for r in before["reasons"] if r["kind"] == "cites")["text"] == "Cites 1 paper in your library"
    assert client.put(f"/api/plugin-host/{PID}/settings", json={"author_names": ["Jane Doe"]}).status_code == 200
    assert wait(lambda: any(r["kind"] == "cites_own" for r in client.get(FEED).get_json()["items"][0]["reasons"]))
    after = client.get(FEED).get_json()["items"][0]
    reason = next(r for r in after["reasons"] if r["kind"] == "cites_own")
    assert reason["text"] == "Cites 1 paper you authored"
    assert after["_f"][f"{PID}.score"] == before["_f"][f"{PID}.score"] + 3
    assert not any(r["kind"] == "cites" for r in after["reasons"])


@pytest.mark.parametrize("response", ["empty", "stale", "offline"])
def test_delayed_feed_is_checked_again_until_current_day_arrives(client, svc, feed, plugin, monkeypatch, response):
    from zoneinfo import ZoneInfo

    # Move to the next announcement after the fixture's already fetched day.
    day = feed.day + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    namespace = next(s.run for s in plugin.schedules if s.name == "announcement-retry").__globals__

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(day.year, day.month, day.day, 4, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(tz)

    monkeypatch.setitem(namespace, "datetime", Clock)
    retry = next(s for s in plugin.schedules if s.name == "announcement-retry")
    assert retry.every == 1800
    if response == "empty":
        feed.day, feed.items = day, []
    elif response == "offline":

        def offline(*args, **kwargs):
            raise H.requests.ConnectionError("offline")

        monkeypatch.setattr(H.requests, "request", offline)
    retry.run()
    if response == "offline":
        assert wait(lambda: any(j["attempts"] > 0 for j in plugin.jobs.pending()))
        # Simulate the runner exhausting its short HTTP retries.
        for job in plugin.jobs.pending():
            plugin.jobs.queue.done(job["id"])
        monkeypatch.setattr(H.requests, "request", feed)
    else:
        assert wait(lambda: not plugin.jobs.pending())
    feed.day = day
    feed.items = [("2610.00003", "new", "Delayed paper", "Some One", "New.", ["quant-ph"])]
    retry.run()
    assert wait(lambda: not plugin.jobs.pending())
    assert client.get("/api/plugin-sources").get_json()["sources"][0]["variants"][0]["id"] == day.isoformat()
    count = len(feed.urls)
    retry.run()
    assert not plugin.jobs.pending() and len(feed.urls) == count


def test_following_updates_retained_feed_without_scoring_or_fetching(client, svc, feed, plugin):
    before = client.get(FEED).get_json()["items"]
    assert all("highlight" not in item for item in before)
    urls = list(feed.urls)
    since = svc.changes.seq
    assert (
        client.put(
            f"/api/plugin-host/{PID}/settings", json={"followed_authors": ["  JANE   DÓE  ", "Ann Other"]}
        ).status_code
        == 200
    )
    # the settings handler runs on the plugin's worker, after the request returns
    assert wait(lambda: "sources" in svc.changes.since(since, svc.changes.epoch, None)["changes"])
    after = client.get(FEED).get_json()["items"]
    assert [i["id"] for i in after] == [i["id"] for i in before]
    assert [i["_f"] for i in after] == [i["_f"] for i in before]
    assert after[0]["highlight"] == {"label": "Following", "text": "Following: Jane Doe; Ann Other"}
    assert after[0]["reasons"][0] == {"kind": "following", "text": "Following: Jane Doe; Ann Other", "items": []}
    assert "highlight" not in after[1]
    day = date.today().isoformat()
    assert (
        client.get(FEED.replace(".feed", f".feed~{day}")).get_json()["items"][0]["highlight"] == after[0]["highlight"]
    )
    item = client.post("/api/outside/add", json={"paper": after[0]}).get_json()["item"]
    listed = client.get(FEED).get_json()["items"][0]
    assert listed["id"] == item["id"] and listed["highlight"] == after[0]["highlight"]
    assert "highlight" not in client.get(f"/api/items/{item['id']}").get_json()
    client.put(f"/api/plugin-host/{PID}/settings", json={"followed_authors": []})
    cleared = client.get(PAST).get_json()["items"]
    assert all("highlight" not in i and all(r["kind"] != "following" for r in i.get("reasons", [])) for i in cleared)
    assert feed.urls == urls


@pytest.mark.parametrize(
    ("authors", "followed", "expected"),
    [
        (["Jane Doe"], ["Jane Doe"], ["Jane Doe"]),
        (["Jane Doe"], ["DOE, jane"], ["Jane Doe"]),
        (["José de la Cruz"], [" jose  de la cruz "], ["José de la Cruz"]),
        (["Jane Doe", "John Doe", "J. Doe"], ["Jane Doe"], ["Jane Doe"]),
        (["Jane Doe"], ["Doe"], []),
        (["Jane Doe"], ["Ann Other", "", "  "], []),
        (["Jane Doe", "Jane Doe"], ["Jane Doe", "Jane Doe"], ["Jane Doe"]),
        ([], ["Jane Doe"], []),
    ],
)
def test_followed_name_matching(authors, followed, expected):
    import importlib

    following = importlib.import_module("plugins.arxiv-today.backend.following")
    assert following.followed_names(authors, followed) == expected


def test_following_and_authored_citations_have_independent_paper_and_day_badges(client, svc, plugin):
    client.put(
        f"/api/plugin-host/{PID}/settings", json={"followed_authors": ["Jane Doe"], "author_names": ["Jane Doe"]}
    )
    assert wait(lambda: any(r["kind"] == "cites_own" for r in client.get(FEED).get_json()["items"][0]["reasons"]))
    paper = client.get(FEED).get_json()["items"][0]
    assert [(b["label"], b["tone"], b["icon"]) for b in paper["highlights"]] == [
        ("Following", "gold", "star"),
        ("Cites you", "red", "megaphone"),
    ]
    source = client.get("/api/plugin-sources").get_json()["sources"][0]
    assert "highlights" not in source  # main navigation derives only today's variant in the browser
    assert [b["text"] for b in source["variants"][0]["highlights"]] == [
        "1 paper by authors you follow",
        "1 paper citing you",
    ]
    item = client.post("/api/outside/add", json={"paper": paper}).get_json()["item"]
    added = client.get(FEED).get_json()["items"][0]
    assert added["id"] == item["id"] and added["highlights"] == paper["highlights"]
    client.put(f"/api/plugin-host/{PID}/settings", json={"followed_authors": []})
    assert [b["label"] for b in client.get(FEED).get_json()["items"][0]["highlights"]] == ["Cites you"]
    client.post(f"/api/plugins/{PID}/dismiss", json={"ids": ["2610.00001"]})
    source = client.get("/api/plugin-sources").get_json()["sources"][0]
    assert "highlights" not in source and "highlights" not in source["variants"][0]
    client.post(f"/api/plugins/{PID}/dismiss", json={"ids": ["2610.00001"], "undo": True})
    assert (
        client.get("/api/plugin-sources").get_json()["sources"][0]["variants"][0]["highlights"][0]["label"]
        == "Cites you"
    )
    client.put(f"/api/plugin-host/{PID}/settings", json={"author_names": []})
    assert wait(lambda: "highlights" not in client.get(FEED).get_json()["items"][0])
