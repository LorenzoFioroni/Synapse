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
