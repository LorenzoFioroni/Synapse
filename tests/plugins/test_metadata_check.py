"""Metadata Check reports changed records and published arXiv versions without editing items."""

import time

import pytest

PID = "metadata-check"


@pytest.fixture(autouse=True)
def _bundled(bundled):
    """This plugin comes with Synapse (plugins/)."""


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_metadata_audit(client, svc, monkeypatch):
    article = client.post(
        "/api/items",
        json={"title": "Stored title", "authors": "Doe, Jane", "year": 2020, "doi": "10.1000/article"},
    ).get_json()
    preprint = client.post(
        "/api/items",
        json={"title": "A preprint", "item_type": "preprint", "archive_id": "arXiv:2610.00001"},
    ).get_json()

    def lookup(query):
        if query["kind"] == "arxiv":
            return {"title": "A preprint", "doi": "10.1000/published", "year": "2026"}
        return {"title": "Correct current title", "authors": "Doe, Jane", "year": "2020", "doi": query["value"]}

    monkeypatch.setattr(svc.plugins, "lookup_metadata", lookup)
    assert client.post(f"/api/plugin-host/{PID}/enable").get_json()["plugin"]["running"]

    def values():
        return {
            item["id"]: {
                f["name"]: f["value"] for f in client.get(f"/api/items/{item['id']}/fields").get_json()["fields"]
            }
            for item in (article, preprint)
        }

    assert wait(lambda: all(fields.get("metadata_check") is not None for fields in values().values()))
    got = values()
    assert got[article["id"]]["metadata_check"] == "Review: title"
    assert got[preprint["id"]]["metadata_check"] == "Published version: 10.1000/published"
    assert client.get(f"/api/items/{article['id']}").get_json()["title"] == "Stored title"


def _start(client, svc, monkeypatch, found_title="Correct current title"):
    """One article whose title differs from what the lookup finds; ``found`` can be changed later."""
    article = client.post(
        "/api/items",
        json={"title": "Stored title", "authors": "Doe, Jane", "year": 2020, "doi": "10.1000/article"},
    ).get_json()
    found = {"title": found_title, "calls": 0}

    def lookup(query):
        found["calls"] += 1
        return {"title": found["title"], "authors": "Doe, Jane", "year": "2020", "doi": query["value"]}

    monkeypatch.setattr(svc.plugins, "lookup_metadata", lookup)
    assert client.post(f"/api/plugin-host/{PID}/enable").get_json()["plugin"]["running"]
    return article, found


def value(client, item):
    fields = client.get(f"/api/items/{item['id']}/fields").get_json()["fields"]
    return next(f["value"] for f in fields if f["name"] == "metadata_check")


def test_the_interval_is_a_setting(client, svc, monkeypatch):
    _start(client, svc, monkeypatch)
    field = svc.plugins._active[PID].plugin.fields.defs["metadata_check"]
    assert field.refresh == 3 * 86400
    client.put(f"/api/plugin-host/{PID}/settings", json={"interval_days": 10})
    assert wait(lambda: field.refresh == 10 * 86400)
    assert client.put(f"/api/plugin-host/{PID}/settings", json={"interval_days": 0}).status_code >= 400
    client.post(f"/api/plugin-host/{PID}/restart")
    assert svc.plugins._active[PID].plugin.fields.defs["metadata_check"].refresh == 10 * 86400


def test_run_now_checks_again(client, svc, monkeypatch):
    article, found = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")
    assert client.get(f"/api/plugins/{PID}/status").get_json()["last_checked"]
    calls = found["calls"]
    found["title"] = "Stored title"
    assert client.post(f"/api/plugins/{PID}/run").get_json()["items"] == 1
    assert wait(lambda: value(client, article) == "Up to date")
    assert found["calls"] > calls


def test_an_ignored_suggestion_stays_hidden_until_it_changes(client, svc, monkeypatch):
    article, found = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")
    r = client.post(f"/api/plugins/{PID}/ignore", json={"ids": [article["id"]]}).get_json()
    assert r == {"ignored": [article["id"]], "pending": []}
    assert value(client, article) == "Up to date · ignored title"
    listed = client.get(f"/api/plugins/{PID}/ignored").get_json()["ignored"]
    assert [(e["id"], e["what"]) for e in listed] == [(article["id"], ["title: Correct current title"])]

    calls = found["calls"]
    client.post(f"/api/plugins/{PID}/run")
    assert wait(lambda: found["calls"] > calls)
    svc.plugins.drain(PID)
    time.sleep(0.2)
    assert value(client, article) == "Up to date · ignored title"  # the same suggestion: still ignored

    found["title"] = "Yet another title"
    client.post(f"/api/plugins/{PID}/run")
    assert wait(lambda: value(client, article) == "Review: title")  # a new one shows

    client.post(f"/api/plugins/{PID}/ignore", json={"ids": [article["id"]]})
    assert value(client, article) == "Up to date · ignored title"
    client.post(f"/api/plugins/{PID}/unignore", json={"ids": [article["id"]]})
    assert value(client, article) == "Review: title"  # at once, without asking again
    assert client.get(f"/api/plugins/{PID}/ignored").get_json()["ignored"] == []


def test_ignoring_before_anything_was_kept_waits_for_the_next_check(client, svc, monkeypatch):
    article, found = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")
    plugin = svc.plugins._active[PID].plugin
    plugin.storage.json("findings").delete(article["id"])  # a value checked by an earlier version
    r = client.post(f"/api/plugins/{PID}/ignore", json={"ids": [article["id"]]}).get_json()
    assert r["pending"] == [article["id"]]
    assert wait(lambda: value(client, article) == "Up to date · ignored title")
    assert not client.get(f"/api/plugins/{PID}/ignored").get_json()["ignored"][0]["pending"]


def test_an_ignored_published_version(client, svc, monkeypatch):
    preprint = client.post(
        "/api/items", json={"title": "A preprint", "item_type": "preprint", "archive_id": "arXiv:2610.00001"}
    ).get_json()
    doi = {"value": "10.1000/published"}
    monkeypatch.setattr(svc.plugins, "lookup_metadata", lambda q: {"title": "A preprint", "doi": doi["value"]})
    client.post(f"/api/plugin-host/{PID}/enable")
    assert wait(lambda: value(client, preprint) == "Published version: 10.1000/published")
    client.post(f"/api/plugins/{PID}/ignore", json={"ids": [preprint["id"]]})
    assert value(client, preprint) == "Up to date · ignored published version"
    doi["value"] = "10.1000/other"
    client.post(f"/api/plugins/{PID}/run")
    assert wait(lambda: value(client, preprint) == "Published version: 10.1000/other")


def test_items_deleted_for_good_are_forgotten(client, svc, monkeypatch):
    article, _ = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")
    client.post(f"/api/plugins/{PID}/ignore", json={"ids": [article["id"]]})
    plugin = svc.plugins._active[PID].plugin
    svc.plugins.emit("item.deleted", ids=[article["id"]])
    svc.plugins.drain(PID)
    assert article["id"] not in plugin.storage.json("ignored").all()
    assert article["id"] not in plugin.storage.json("findings").all()


def test_ignore_paper_altogether(client, svc, monkeypatch):
    article, _ = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")

    client.post(f"/api/plugins/{PID}/ignore-paper", json={"ids": [article["id"]]})
    assert value(client, article) == "Up to date · ignored paper"
    st = client.get(f"/api/plugins/{PID}/status").get_json()
    assert st["ignored_papers"] == 1
    assert st["unreviewed"] == 0

    rev = client.get(f"/api/plugins/{PID}/review").get_json()
    assert rev["articles"] == []

    client.post(f"/api/plugins/{PID}/unignore-paper", json={"ids": [article["id"]]})
    client.post(f"/api/plugins/{PID}/run")
    assert wait(lambda: value(client, article) == "Review: title")


def test_review_endpoint_and_applied(client, svc, monkeypatch):
    article, _ = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")

    st = client.get(f"/api/plugins/{PID}/status").get_json()
    assert st["unreviewed"] == 1

    rev = client.get(f"/api/plugins/{PID}/review").get_json()
    assert len(rev["articles"]) == 1
    assert rev["articles"][0]["id"] == article["id"]
    assert any(c["key"] == "title" for c in rev["articles"][0]["changes"])

    client.patch(f"/api/items/{article['id']}", json={"title": "Correct current title"})
    client.post(f"/api/plugins/{PID}/applied", json={"ids": [article["id"]]})
    assert value(client, article) == "Up to date"
    st2 = client.get(f"/api/plugins/{PID}/status").get_json()
    assert st2["unreviewed"] == 0


def test_reject_single_suggestion(client, svc, monkeypatch):
    article = client.post(
        "/api/items",
        json={"title": "Stored title", "authors": "Doe, Jane", "year": 2010, "doi": "10.1000/multi"},
    ).get_json()

    def lookup(query):
        return {
            "title": "New Title",
            "authors": "Doe, Jane",
            "year": 2020,
            "doi": query["value"],
        }

    monkeypatch.setattr(svc.plugins, "lookup_metadata", lookup)
    client.post(f"/api/plugin-host/{PID}/enable")
    assert wait(lambda: "title" in (value(client, article) or "") and "year" in (value(client, article) or ""))

    # Reject only title
    client.post(f"/api/plugins/{PID}/ignore", json={"ids": [article["id"]], "field": "title", "value": "New Title"})
    assert value(client, article) == "Review: year"

    # Reject year too
    client.post(f"/api/plugins/{PID}/ignore", json={"ids": [article["id"]], "field": "year", "value": "2020"})
    assert value(client, article) == "Up to date · ignored title, year"


def test_partial_apply_keeps_unselected_suggestions(client, svc, monkeypatch):
    article, _ = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")
    plugin = svc.plugins._active[PID].plugin
    finding = plugin.storage.json("findings").get(article["id"])
    finding["fetched"]["year"] = "2025"
    plugin.storage.json("findings").set(article["id"], finding)
    client.patch(f"/api/items/{article['id']}", json={"title": "Correct current title"})
    client.post(f"/api/plugins/{PID}/applied", json={"ids": [article["id"]]})
    review = client.get(f"/api/plugins/{PID}/review").get_json()["articles"]
    assert [c["key"] for c in review[0]["changes"]] == ["year"]
    assert value(client, article) == "Review: year"


def test_long_rejection_remembers_the_entire_value(client, svc, monkeypatch):
    title = "A" * 301 + " first ending"
    article, found = _start(client, svc, monkeypatch, found_title=title)
    assert wait(lambda: value(client, article) == "Review: title")
    client.post(f"/api/plugins/{PID}/ignore", json={"ids": [article["id"]]})
    assert client.get(f"/api/plugins/{PID}/review").get_json()["articles"] == []
    found["title"] = "A" * 301 + " different ending"
    client.post(f"/api/plugins/{PID}/run")
    assert wait(lambda: value(client, article) == "Review: title")


def test_ignored_published_doi_is_not_reviewed_as_a_field(client, svc, monkeypatch):
    item = client.post("/api/items", json={"title": "Preprint", "archive_id": "arXiv:2610.00001"}).get_json()
    monkeypatch.setattr(svc.plugins, "lookup_metadata", lambda q: {"title": "Preprint", "doi": "10.1000/published"})
    client.post(f"/api/plugin-host/{PID}/enable")
    assert wait(lambda: value(client, item) == "Published version: 10.1000/published")
    client.post(f"/api/plugins/{PID}/ignore", json={"ids": [item["id"]]})
    assert client.get(f"/api/plugins/{PID}/review").get_json()["articles"] == []
    assert client.get(f"/api/plugins/{PID}/status").get_json()["unreviewed"] == 0


def test_partial_automatic_run_finishes(client, svc, monkeypatch):
    article, _ = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")
    new = client.post("/api/items", json={"title": "New paper", "doi": "10.1000/new"}).get_json()
    assert wait(lambda: value(client, new) is not None)
    status = client.get(f"/api/plugins/{PID}/status").get_json()
    assert not status["running"]
    assert status["done"] == status["total"] == 1


def test_offline_run_finishes_and_records_failures(client, svc, monkeypatch):
    import requests

    article, _ = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")

    def offline(query):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(svc.plugins, "lookup_metadata", offline)
    before = client.get(f"/api/plugins/{PID}/status").get_json()["last_checked"]
    client.post(f"/api/plugins/{PID}/run")
    assert wait(lambda: client.get(f"/api/plugins/{PID}/status").get_json()["last_checked"] > before)
    status = client.get(f"/api/plugins/{PID}/status").get_json()
    assert not status["running"]
    assert status["failed"] == status["done"] == 1
    assert value(client, article) == "Review: title"
    monkeypatch.setattr(svc.plugins, "lookup_metadata", lambda q: {"title": "Stored title"})
    client.post(f"/api/plugins/{PID}/run")
    assert wait(lambda: value(client, article) == "Up to date")


def test_unignore_paper_restores_cached_findings_without_lookup(client, svc, monkeypatch):
    article, found = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")
    client.post(f"/api/plugins/{PID}/ignore-paper", json={"ids": [article["id"]]})
    calls = found["calls"]
    client.post(f"/api/plugins/{PID}/unignore-paper", json={"ids": [article["id"]]})
    assert value(client, article) == "Review: title"
    assert found["calls"] == calls


def test_old_rejections_and_findings_are_upgraded(client, svc, monkeypatch):
    article, _ = _start(client, svc, monkeypatch)
    assert wait(lambda: value(client, article) == "Review: title")
    plugin = svc.plugins._active[PID].plugin
    plugin.storage.json("state").delete("exact_rejections")
    plugin.storage.json("ignored").set(
        article["id"],
        {
            "fields": {"title": "correctcurrenttitle"},
            "shown": {"title": "Correct current title"},
        },
    )
    plugin.storage.json("findings").set(article["id"], {"fields": {"title": "Correct current title"}})
    client.post(f"/api/plugin-host/{PID}/restart")
    assert wait(lambda: value(client, article) == "Up to date · ignored title")
    assert client.get(f"/api/plugins/{PID}/review").get_json()["articles"] == []


def test_contributed_defaults_and_safe_mode_preserve_saved_state(client, svc, tmp_path):
    import json

    from backend import create_app
    from backend.config import Config

    info = svc.plugins.info(PID)
    assert not info["core"] and not info["enabled"]
    data = tmp_path / "safe"
    data.mkdir()
    (data / "plugins.json").write_text(json.dumps({"plugins": {PID: {"enabled": True}}}))
    storage = data / "plugin-data" / PID
    storage.mkdir(parents=True)
    saved = '{"paper": {"title": "Kept", "at": 123}}'
    (storage / "ignored_papers.json").write_text(saved)
    config = Config.from_env(data)
    config.safe_mode = True
    app = create_app(config, start_indexer=False)
    try:
        host = app.extensions["synapse"].plugins
        info = host.info(PID)
        assert info["enabled"] and not info["running"] and not info["core"]
        assert info["status"] == "safe-mode"
        assert (storage / "ignored_papers.json").read_text() == saved
        assert app.test_client().get(f"/api/plugins/{PID}/status").status_code == 404
    finally:
        app.extensions["synapse"].plugins.stop()


def test_metadata_check_imports_only_the_public_plugin_api():
    import ast

    from backend.config import APP_ROOT

    folder = APP_ROOT / "plugins" / PID
    for path in (folder / "backend").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("backend."):
                assert node.module in {"backend.plugins", "backend.plugins.scholarly"}, path
    frontend = (folder / "frontend" / "index.ts").read_text()
    assert "import type {" in frontend
    assert 'from "../../../frontend/src/plugins/api.js"' in frontend
    assert "plugin.synapse" not in (folder / "backend" / "__init__.py").read_text()


def test_retired_lookup_does_not_save_findings_or_complete_run(client, svc, monkeypatch):
    from backend.plugins.fields import LATER
    from backend.plugins.jobs import Runner

    # Run the refresher explicitly so retirement occurs at the exact lookup boundary.
    monkeypatch.setattr(Runner, "start", lambda self: None)
    item = svc.papers.create({"title": "Before", "doi": "10.1000/article"})
    svc.plugins.enable(PID)
    plugin = svc.plugins._active[PID].plugin

    def lookup(current):
        for stop in reversed(plugin.unload):
            stop()
        return current, {"title": "Late answer"}

    monkeypatch.setattr(plugin.services, "lookup_item", lookup)
    field = plugin.fields.defs["metadata_check"]
    key = plugin.fields.key(item)
    result = field.refresher([{"key": key, "item_id": item["id"], "doi": item["doi"]}])
    assert result == {key: LATER}
    assert plugin.storage.json("findings").all() == {}
    assert plugin.storage.json("state").get("last_checked") is None


# ---------------------------------------------------------------- arXiv versions and published versions
FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">{}</feed>"""
ENTRY = "<entry><id>http://arxiv.org/abs/{id}v{v}</id><updated>{updated}</updated><title>T</title></entry>"


def _arxiv_module():
    import importlib.util

    from backend.config import APP_ROOT

    spec = importlib.util.spec_from_file_location("mc_arxiv", APP_ROOT / "plugins" / PID / "backend" / "arxiv.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_arxiv_helpers():
    arxiv = _arxiv_module()
    feed = FEED.format(
        ENTRY.format(id="2610.00001", v=3, updated="2026-10-20T10:00:00Z")
        + ENTRY.format(id="hep-th/9901001", v=2, updated="1999-02-01T00:00:00Z")
        + "<entry><id>http://arxiv.org/api/errors#bad</id><title>Error</title></entry>"
    )
    assert arxiv.parse_feed(feed) == {
        "2610.00001": {"latest": 3, "updated": "2026-10-20T10:00:00Z"},
        "hep-th/9901001": {"latest": 2, "updated": "1999-02-01T00:00:00Z"},
    }
    page = "cites arXiv:2501.00002v9 ... arXiv:2610.00001v2 [quant-ph] 3 Oct 2026"
    assert arxiv.stamped_version(page, "2610.00001") == 2  # not the cited paper's version
    assert arxiv.recorded_version({"archive_id": "arXiv:2610.00001v1"}, "2610.00001") == 1
    pdf = {"date_added": "2026-10-01T00:00:00Z"}
    entry = {"latest": 3, "updated": "2026-10-20T10:00:00Z"}
    assert arxiv.newer({**entry, "stamp": 2}, pdf) == {"version": 3, "have": 2, "how": "stamp"}
    assert arxiv.newer({**entry, "stamp": 3, "recorded": 1}, pdf) is None  # the stamp wins
    assert arxiv.newer(entry, pdf) == {"version": 3, "have": None, "how": "date"}
    assert arxiv.newer(entry, {"date_added": "2026-11-01T00:00:00Z"}) is None  # added after v3
    assert arxiv.newer(entry, None) is None


class FakeResp:
    def __init__(self, status, body, headers=None):
        import json

        self.status_code = status
        self.reason = "OK" if status < 400 else "Error"
        self.headers = headers or {}
        self._body = (
            body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else json.dumps(body).encode())
        )
        self.encoding = "utf-8"

    def iter_content(self, n):
        yield self._body

    def close(self):
        pass


@pytest.fixture
def arxiv_web(monkeypatch, svc, tmp_path):
    """arXiv's API and PDFs, and Crossref's search, as far as the plugin asks them."""
    from urllib.parse import parse_qs, urlsplit

    from backend.plugins import http as H
    from tests.conftest import make_pdf

    web = {"latest": 3, "updated": "2999-01-01T00:00:00Z", "crossref": [], "calls": []}

    def request(method, url, **kw):
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        web["calls"].append((parts.hostname, parts.path, q))
        if parts.hostname == "export.arxiv.org":
            ids = q["id_list"].split(",")
            return FakeResp(
                200, FEED.format("".join(ENTRY.format(id=i, v=web["latest"], updated=web["updated"]) for i in ids))
            )
        if parts.hostname == "arxiv.org" and parts.path.startswith("/pdf/"):
            version = parts.path.rsplit("v", 1)[1]
            pdf = make_pdf(tmp_path / f"v{version}.pdf", f"arXiv:2610.00001v{version} [quant-ph]")
            return FakeResp(200, pdf.read_bytes(), {"Content-Type": "application/pdf"})
        if parts.hostname == "api.crossref.org":
            return FakeResp(200, {"message": {"items": web["crossref"]}})
        raise AssertionError(url)

    monkeypatch.setattr(H.requests, "request", request)
    monkeypatch.setattr(H, "DEFAULT_GAP", 0)
    svc.plugins.private_network = True  # no name lookups for the fake hosts
    return web


def _preprint_with_pdf(client, tmp_path, stamp="arXiv:2610.00001v1 [quant-ph]", **fields):
    from tests.conftest import make_pdf

    item = client.post(
        "/api/items",
        json={
            "title": "A preprint",
            "authors": "Doe, Jane",
            "year": 2026,
            "item_type": "preprint",
            "archive_id": "arXiv:2610.00001",
            **fields,
        },
    ).get_json()
    pdf = make_pdf(tmp_path / "mine.pdf", stamp)
    att = client.post(
        f"/api/items/{item['id']}/attachments",
        data={"file": (open(pdf, "rb"), "mine.pdf")},
        content_type="multipart/form-data",
    ).get_json()["attachments"][0]
    return item, att


def _same_preprint(monkeypatch, svc):
    monkeypatch.setattr(
        svc.plugins,
        "lookup_metadata",
        lambda q: {
            "title": "A preprint",
            "authors": "Doe, Jane",
            "year": "2026",
            "item_type": "preprint",
            "archive_id": f"arXiv:{q['value']}",
        },
    )


def test_a_newer_arxiv_version_is_announced_once_and_replaces_the_pdf(client, svc, monkeypatch, tmp_path, arxiv_web):
    item, att = _preprint_with_pdf(client, tmp_path)
    assert (
        client.post(
            f"/api/attachments/{att['id']}/annotations",
            json={"type": "highlight", "page": 1, "text": "mine", "rects": [[0.1, 0.1, 0.2, 0.05]]},
        ).status_code
        == 201
    )
    _same_preprint(monkeypatch, svc)
    client.post(f"/api/plugin-host/{PID}/enable")
    assert wait(lambda: value(client, item) == "New arXiv version: v3")

    [a] = client.get(f"/api/plugins/{PID}/review").get_json()["articles"]
    assert a["version"]["version"] == 3 and a["version"]["have"] == 1 and a["version"]["how"] == "stamp"
    assert a["version"]["highlights"] == 1 and a["version"]["url"] == "https://arxiv.org/abs/2610.00001v3"
    news = client.post(f"/api/plugins/{PID}/news").get_json()["news"]
    assert [(n["id"], n["version"]) for n in news] == [(item["id"], 3)]
    assert client.post(f"/api/plugins/{PID}/news").get_json()["news"] == []  # told once

    r = client.post(f"/api/plugins/{PID}/replace", json={"ids": [item["id"]]}).get_json()
    assert r == {"version": 3, "removed_annotations": 1}
    [now] = client.get(f"/api/items/{item['id']}").get_json()["attachments"]
    assert now["id"] == att["id"] and now["url"] != att["url"]  # the same attachment, a new file
    assert b"v3" in client.get(f"/pdfs/{now['url']}").data
    assert client.get(f"/pdfs/{att['url']}").status_code == 404  # the old file is gone
    assert client.get(f"/api/attachments/{att['id']}/annotations").get_json()["annotations"] == []
    assert value(client, item) == "Up to date"
    assert client.get(f"/api/plugins/{PID}/review").get_json()["articles"] == []

    arxiv_web["latest"] = 4  # a later version is a new suggestion, and a new notice
    client.post(f"/api/plugins/{PID}/run")
    assert wait(lambda: value(client, item) == "New arXiv version: v4")
    assert [n["version"] for n in client.post(f"/api/plugins/{PID}/news").get_json()["news"]] == [4]


def test_keeping_the_current_pdf_hides_that_version_only(client, svc, monkeypatch, tmp_path, arxiv_web):
    item, att = _preprint_with_pdf(client, tmp_path)
    _same_preprint(monkeypatch, svc)
    client.post(f"/api/plugin-host/{PID}/enable")
    assert wait(lambda: value(client, item) == "New arXiv version: v3")
    client.post(f"/api/plugins/{PID}/keep-current", json={"ids": [item["id"]]})
    assert value(client, item) == "Up to date · ignored arXiv v3"
    assert client.get(f"/api/plugins/{PID}/review").get_json()["articles"] == []
    assert client.get(f"/api/plugins/{PID}/ignored").get_json()["ignored"][0]["what"] == ["arXiv v3"]
    assert client.post(f"/api/plugins/{PID}/replace", json={"ids": [item["id"]]}).status_code >= 400
    assert client.get(f"/api/items/{item['id']}").get_json()["attachments"][0]["url"] == att["url"]

    arxiv_web["latest"] = 4
    client.post(f"/api/plugins/{PID}/run")
    assert wait(lambda: value(client, item) == "New arXiv version: v4")

    client.put(f"/api/plugin-host/{PID}/settings", json={"arxiv_versions": False})
    assert wait(lambda: value(client, item) == "Up to date")


def test_up_to_date_pdfs_and_published_papers_are_not_offered_versions(client, svc, monkeypatch, tmp_path, arxiv_web):
    current, _ = _preprint_with_pdf(client, tmp_path, stamp="arXiv:2610.00001v3 [quant-ph]")
    _same_preprint(monkeypatch, svc)
    client.post(f"/api/plugin-host/{PID}/enable")
    assert wait(lambda: value(client, current) == "Up to date")
    asked = [c for c in arxiv_web["calls"] if c[0] == "export.arxiv.org"]
    assert len(asked) == 1  # the batch in one request

    no_stamp, _ = _preprint_with_pdf(client, tmp_path, stamp="no stamp here", archive_id="arXiv:2610.00009")
    arxiv_web["updated"] = "2000-01-01T00:00:00Z"  # v3 was posted before the PDF was added
    client.post(f"/api/plugins/{PID}/run")
    svc.plugins.drain(PID)
    assert wait(lambda: value(client, no_stamp) == "Up to date")


def test_a_published_version_found_by_title_suggests_the_journal_record(client, svc, monkeypatch, tmp_path, arxiv_web):
    item = client.post(
        "/api/items",
        json={
            "title": "A preprint about qubits",
            "authors": "Doe, Jane",
            "year": 2025,
            "item_type": "preprint",
            "archive_id": "arXiv:2510.00001",
            "journal": "arXiv",
        },
    ).get_json()
    arxiv_web["crossref"] = [
        {
            "DOI": "10.1000/other",
            "type": "journal-article",
            "title": ["A preprint about qubits"],
            "author": [{"family": "Someone"}],
            "issued": {"date-parts": [[2026]]},
        },
        {
            "DOI": "10.1000/PRL.1",
            "type": "journal-article",
            "title": ["A Preprint About Qubits"],
            "author": [{"family": "Doe"}],
            "issued": {"date-parts": [[2026]]},
        },
    ]

    def lookup(q):
        if q["kind"] == "doi":
            assert q["value"].lower() == "10.1000/prl.1"
            return {
                "title": "A preprint about qubits",
                "authors": "Doe, Jane",
                "year": "2026",
                "item_type": "journalArticle",
                "journal": "Phys. Rev. Lett.",
                "volume": "1",
                "doi": "10.1000/PRL.1",
            }
        return {
            "title": "A preprint about qubits",
            "authors": "Doe, Jane",
            "year": "2025",
            "item_type": "preprint",
            "archive_id": "arXiv:2510.00001",
            "journal": "arXiv",
        }

    monkeypatch.setattr(svc.plugins, "lookup_metadata", lookup)
    client.post(f"/api/plugin-host/{PID}/enable")
    assert wait(lambda: value(client, item) == "Published version: 10.1000/prl.1")
    [a] = client.get(f"/api/plugins/{PID}/review").get_json()["articles"]
    proposed = {c["key"]: c["fetched"] for c in a["changes"]}
    assert proposed["journal"] == "Phys. Rev. Lett." and proposed["item_type"] == "journalArticle"
    assert "archive_id" not in proposed  # the arXiv ID stays with the published record
    [n] = client.post(f"/api/plugins/{PID}/news").get_json()["news"]
    assert n["published"] == "10.1000/prl.1"
    search = next(c for c in arxiv_web["calls"] if c[0] == "api.crossref.org")
    assert "journal-article" in search[2]["filter"]


def test_replacing_a_pdf_needs_edit_permission_and_a_pdf(svc, tmp_path):
    from backend.plugins import PluginError
    from tests.conftest import make_pdf

    item = svc.papers.create({"title": "Paper"})
    name = "old.pdf"
    make_pdf(svc.config.pdf_dir / name, "old")
    att = svc.papers.add_attachment(item["id"], "Full Text PDF", name)
    with pytest.raises(ValueError):
        svc.replace_pdf(att["id"], b"<html>login</html>")
    assert (svc.config.pdf_dir / name).exists()
    link = svc.papers.add_attachment(item["id"], "Link", "https://example.org/paper.pdf")
    with pytest.raises(ValueError):
        svc.replace_pdf(link["id"], make_pdf(tmp_path / "new.pdf", "new").read_bytes())
    from backend.plugins.services import PluginActions

    class Manifest:
        name, library = "Reader", ["read"]

    with pytest.raises(PluginError, match="edit"):
        PluginActions(svc, Manifest()).replace_pdf(att["id"], b"%PDF-1.4")
