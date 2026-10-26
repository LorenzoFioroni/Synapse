"""Metadata sources: core-provider toggling and user-provider routing, with no real network."""

import json
import time
from pathlib import Path

import pytest

from backend.online import metadata


@pytest.fixture(autouse=True)
def core_sources(bundled):
    pass


def test_core_metadata_sources_off_and_on_preserves_the_library(client, svc, monkeypatch):
    item = svc.papers.create({"title": "Kept", "doi": "10.1234/kept"})
    info = svc.plugins.info("metadata-sources")
    assert info["running"] and info["metadata_sources"][0]["id"] == "scholarly"
    svc.plugins.disable("metadata-sources")
    monkeypatch.setattr("requests.get", lambda *a, **kw: pytest.fail("disabled sources used the network"))
    result = client.post(f"/api/items/{item['id']}/lookup")
    assert result.status_code == 404 and "No metadata sources" in result.json["error"]
    assert svc.papers.get(item["id"])["title"] == "Kept"
    svc.plugins.enable("metadata-sources")
    assert svc.plugins.info("metadata-sources")["running"]
    assert svc.papers.get(item["id"])["title"] == "Kept"

    class Response:
        ok, status_code = True, 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"title": ["Fetched again"], "DOI": "10.1234/kept"}}

    monkeypatch.setattr("requests.get", lambda *a, **kw: Response())
    result = client.post(f"/api/items/{item['id']}/lookup")
    assert result.status_code == 200 and result.json["fetched"]["title"] == "Fetched again"
    assert svc.papers.get(item["id"])["title"] == "Kept"


def test_user_source_priority_and_identifier_normalization(client, svc, make_plugin, monkeypatch):
    make_plugin(
        "custom-metadata",
        code="""
        def activate(p):
            @p.metadata_source("custom", title="Custom catalogue", kinds=["doi"], order=1)
            def find(q):
                assert q["kind"] == "doi" and q["value"] == "10.1234/custom"
                return {"title": "Custom result", "doi": q["value"], "authors": "Doe, Jane", "year": "2026"}
    """,
    )
    svc.plugins.enable("custom-metadata")
    monkeypatch.setattr("requests.get", lambda *a, **kw: pytest.fail("later source should not run"))
    result = client.post("/api/items/from_identifier", json={"identifier": "https://doi.org/10.1234/custom"})
    assert result.status_code == 201 and result.json["title"] == "Custom result"
    svc.plugins.disable("metadata-sources")
    assert metadata.lookup(doi="10.1234/custom", sources=svc.plugins.lookup_metadata)["title"] == "Custom result"
    with pytest.raises(metadata.LookupFailed, match="No metadata sources"):
        metadata.lookup(identifier="arXiv:2610.00001", sources=svc.plugins.lookup_metadata)


def test_declining_and_failing_sources_fall_through(svc, make_plugin):
    svc.plugins.disable("metadata-sources")
    make_plugin(
        "fallback-metadata",
        code="""
        from backend.plugins import PluginError
        def activate(p):
            @p.metadata_source("empty", title="No match", order=1)
            def empty(q): return None
            @p.metadata_source("failed", title="Unavailable", order=2)
            def failed(q): raise PluginError("temporarily unavailable")
            @p.metadata_source("last", title="Fallback", order=3)
            def last(q): return {"title": "Fallback", "doi": q["value"]}
    """,
    )
    svc.plugins.enable("fallback-metadata")
    assert metadata.lookup(doi="10.1234/x", sources=svc.plugins.lookup_metadata)["title"] == "Fallback"
    assert svc.plugins.info("fallback-metadata")["running"]
    svc.plugins.disable("fallback-metadata")
    with pytest.raises(metadata.LookupFailed, match="No metadata sources"):
        metadata.lookup(doi="10.1234/x", sources=svc.plugins.lookup_metadata)


@pytest.mark.parametrize("already_correct", [False, True])
def test_retrieve_and_metadata_check_use_paper_author_names(client, svc, monkeypatch, already_correct):
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/metadata_author_names.json").read_text())
    message = fixture["crossref"]["message"]

    class Response:
        ok, status_code = True, 200

        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    monkeypatch.setattr(
        "requests.get",
        lambda url, **kwargs: Response(fixture["openalex"] if "openalex" in url else fixture["crossref"]),
    )
    stored = fixture["expected"] if already_correct else fixture["profile_authors"]
    item = svc.papers.create(
        {"title": message["title"][0], "doi": message["DOI"], "authors": stored, "library_catalog": "Crossref"}
    )
    result = client.post(f"/api/items/{item['id']}/lookup")
    assert result.status_code == 200
    assert result.json["fetched"]["authors"] == fixture["expected"]

    svc.plugins.enable("metadata-check")
    value = None
    end = time.monotonic() + 5
    while value is None and time.monotonic() < end:
        fields = client.get(f"/api/items/{item['id']}/fields").json["fields"]
        value = next((f["value"] for f in fields if f["name"] == "metadata_check"), None)
        if value is None:
            time.sleep(0.05)
    assert value == ("Up to date" if already_correct else "Review: authors")
    articles = client.get("/api/plugins/metadata-check/review").json["articles"]
    if already_correct:
        assert articles == []
    else:
        change = next(c for c in articles[0]["changes"] if c["key"] == "authors")
        assert change["fetched"] == fixture["expected"]
    assert svc.papers.get(item["id"])["authors"] == stored
