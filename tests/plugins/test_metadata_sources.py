"""Metadata sources: contributed-provider toggling and user-provider routing, with no real network."""

import json
import time
from pathlib import Path

import pytest

from backend.online import metadata
from tests.fake_metadata import Response, install_transport


@pytest.fixture(autouse=True)
def bundled_sources(bundled):
    pass


def test_metadata_sources_off_and_on_preserves_the_library(client, svc, monkeypatch):
    item = svc.papers.create({"title": "Kept", "doi": "10.1234/kept"})
    info = svc.plugins.info("metadata-sources")
    assert info["running"] and info["metadata_sources"][0]["id"] == "scholarly"
    svc.plugins.disable("metadata-sources")
    install_transport(monkeypatch, lambda *a, **kw: pytest.fail("disabled sources used the network"))
    result = client.post(f"/api/items/{item['id']}/lookup")
    assert result.status_code == 404 and "No metadata sources" in result.json["error"]
    assert svc.papers.get(item["id"])["title"] == "Kept"
    svc.plugins.enable("metadata-sources")
    assert svc.plugins.info("metadata-sources")["running"]
    assert svc.papers.get(item["id"])["title"] == "Kept"

    install_transport(
        monkeypatch, lambda *a, **kw: Response({"message": {"title": ["Fetched again"], "DOI": "10.1234/kept"}})
    )
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
    install_transport(monkeypatch, lambda *a, **kw: pytest.fail("later source should not run"))
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

    install_transport(
        monkeypatch, lambda url, **kwargs: Response(fixture["openalex"] if "openalex" in url else fixture["crossref"])
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


def test_contributed_permissions_and_live_service_credentials(svc, monkeypatch):
    info = svc.plugins.info("metadata-sources")
    assert not info["core"] and info["enabled"] and info["running"]
    assert set(info["permissions"]["network"]) == {
        "api.crossref.org",
        "api.openalex.org",
        "export.arxiv.org",
        "openlibrary.org",
    }
    svc.settings.update({"contact_email": "researcher@example.org", "openalex_api_key": "test-key"})
    calls = []

    def get(url, params=None, headers=None, **kwargs):
        calls.append((url, params, headers))
        return Response(
            {"message": {"title": ["Fetched"], "DOI": "10.1234/credentials"}}
            if "crossref" in url
            else {"authorships": []}
        )

    install_transport(monkeypatch, get)
    svc.plugins.lookup_metadata({"kind": "doi", "value": "10.1234/credentials"})
    crossref = next(c for c in calls if "crossref" in c[0])
    openalex = next(c for c in calls if "openalex" in c[0])
    assert crossref[1] is None
    assert openalex[1] == {"api_key": "test-key", "mailto": "researcher@example.org"}
    assert all(
        "plugin metadata-sources" in c[2]["User-Agent"] and "researcher@example.org" in c[2]["User-Agent"]
        for c in calls
    )
    calls.clear()
    svc.settings.update({"openalex_api_key": "changed-key"})
    svc.plugins.lookup_metadata({"kind": "doi", "value": "10.1234/credentials"})
    assert next(c for c in calls if "openalex" in c[0])[1]["api_key"] == "changed-key"


def test_disabling_retires_the_provider_and_preserves_saved_choice(svc):
    from backend import create_app
    from backend.plugins import PluginError

    provider = next(iter(svc.plugins._active["metadata-sources"].plugin.metadata_sources.values())).fn
    svc.plugins.disable("metadata-sources")
    with pytest.raises(PluginError, match="stopped"):
        provider({"kind": "doi", "value": "10.1234/stopped"})
    app = create_app(svc.config, start_indexer=False)
    try:
        info = app.extensions["synapse"].plugins.info("metadata-sources")
        assert not info["enabled"] and not info["running"]
    finally:
        app.extensions["synapse"].plugins.stop()


def test_safe_mode_preserves_enabled_metadata_sources(tmp_path):
    from backend import create_app
    from backend.config import Config

    data = tmp_path / "safe"
    data.mkdir()
    (data / "plugins.json").write_text(json.dumps({"plugins": {"metadata-sources": {"enabled": True}}}))
    config = Config.from_env(data)
    config.safe_mode = True
    app = create_app(config, start_indexer=False)
    try:
        info = app.extensions["synapse"].plugins.info("metadata-sources")
        assert not info["core"] and info["enabled"] and not info["running"]
        assert info["status"] == "safe-mode"
    finally:
        app.extensions["synapse"].plugins.stop()


def test_concurrent_queries_keep_provider_caches_separate(svc, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    revalidated = []

    def get(url, headers=None, **kwargs):
        if (headers or {}).get("If-None-Match") == '"same-tag"':
            revalidated.append(url)
            return Response(status=304, content=b"")
        data = {"message": {"title": ["Shared lookup"], "DOI": "10.1234/cache"}}
        if "openalex" in url:
            data = {"authorships": [{"raw_author_name": "Mina de Vries"}]}
        response = Response(data)
        response.headers["ETag"] = '"same-tag"'
        return response

    install_transport(monkeypatch, get)
    query = {"kind": "doi", "value": "10.1234/cache"}
    with ThreadPoolExecutor(max_workers=2) as callers:
        results = list(callers.map(lambda _: svc.plugins.lookup_metadata(query), range(2)))
    results.append(svc.plugins.lookup_metadata(query))
    assert all(item["title"] == "Shared lookup" and item["authors"] == "de Vries, Mina" for item in results)
    assert any("crossref" in url for url in revalidated) and any("openalex" in url for url in revalidated)
