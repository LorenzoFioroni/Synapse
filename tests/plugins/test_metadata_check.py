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
