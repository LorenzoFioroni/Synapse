"""Contributed reference providers preserve identities, credentials and offline citation data."""

import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import create_app
from backend.config import Config
from backend.online import http as online_http
from backend.plugins import HttpError, PluginError
from backend.plugins import Response as PublicResponse
from backend.plugins.http import PluginHttp
from tests.fake_metadata import Response, install_transport

P = importlib.import_module("plugins.reference-sources.server.providers")
CITING = importlib.import_module("plugins.reference-sources.server.citing")
HTML = '<section class="ltx_bibliography"><li class="ltx_bibitem">A. Author. “A cited reference fixture” (2021).</li></section>'
S2 = {"data": [{"citedPaper": {"title": "Cited fixture", "externalIds": {"DOI": "10.1234/cited"}}}]}


@pytest.fixture(autouse=True)
def bundled_sources(bundled):
    pass


@pytest.mark.parametrize("selected", ["semanticscholar", "openalex", "crossref", "arxiv-html"])
def test_each_provider_preserves_the_legacy_key_and_saved_data(selected, client, svc, monkeypatch):
    def get(url, **kwargs):
        if "semanticscholar" in url:
            return Response(S2 if selected == "semanticscholar" else {"data": []})
        if "openalex" in url:
            if "/works/doi:" in url:
                return Response({"referenced_works": ["https://openalex.org/W1"] if selected == "openalex" else []})
            return Response({"results": [{"id": "https://openalex.org/W1", "title": "Cited fixture"}]})
        if "crossref" in url:
            return Response({"message": {"reference": [{"DOI": "10.1234/cited"}] if selected == "crossref" else []}})
        return Response(content=HTML.encode())

    install_transport(monkeypatch, get)
    info = svc.plugins.info("reference-sources")
    assert not info["core"] and info["enabled"] and info["running"]
    assert set(info["permissions"]["network"]) == {
        "api.semanticscholar.org",
        "api.openalex.org",
        "api.crossref.org",
        "arxiv.org",
    }
    paper = svc.papers.create({"title": "Parent", "doi": "10.1234/parent", "archive_id": "arXiv:2101.00001"})
    result = client.post(f"/api/items/{paper['id']}/references")
    assert result.status_code == 200, result.json
    assert result.json["fetch"]["source"] == selected and len(result.json["references"]) == 1
    assert result.json["fetch"]["source_title"] == svc.plugins.reference_source_title(selected)
    before = svc.citations.store.fetch_info(paper["id"]), svc.citations.store.references(paper["id"])
    provider = next(iter(svc.plugins._active["reference-sources"].plugin.reference_sources.values())).fn
    svc.plugins.disable("reference-sources")
    with pytest.raises(PluginError, match="stopped"):
        provider({"item": paper, "fresh": False})
    assert (svc.citations.store.fetch_info(paper["id"]), svc.citations.store.references(paper["id"])) == before
    saved = client.get(f"/api/items/{paper['id']}/references").json
    assert (
        saved["fetch"]["source"] == selected and saved["fetch"]["source_title"] == result.json["fetch"]["source_title"]
    )
    svc.plugins.enable("reference-sources")
    assert client.post(f"/api/items/{paper['id']}/references").status_code == 200


def test_current_credentials_stay_with_their_service(svc, monkeypatch):
    svc.settings.update(
        {
            "semantic_scholar_api_key": "s2-secret",
            "openalex_api_key": "oa-secret",
            "contact_email": "reader@example.org",
        }
    )
    calls = []

    def get(url, params=None, headers=None, **kwargs):
        calls.append((url, params or {}, headers or {}))
        if "crossref" in url:
            return Response({"message": {"reference": [{"DOI": "10.1234/cited"}]}})
        return Response({"data": [], "referenced_works": []})

    install_transport(monkeypatch, get)
    query = {"item": {"doi": "10.1234/parent"}, "email": "", "fresh": False}
    assert svc.plugins.fetch_references(query)[0] == "crossref"
    for url, params, headers in calls:
        assert (headers.get("x-api-key") == "s2-secret") == ("semanticscholar" in url)
        assert (params.get("api_key") == "oa-secret") == ("openalex" in url)
        assert "reader@example.org" in headers["User-Agent"]
    svc.settings.update({"semantic_scholar_api_key": "new-s2", "openalex_api_key": "new-oa"})
    calls.clear()
    svc.plugins.fetch_references(query)
    assert next(c for c in calls if "semanticscholar" in c[0])[2]["x-api-key"] == "new-s2"
    assert next(c for c in calls if "openalex" in c[0])[1]["api_key"] == "new-oa"


def test_malformed_providers_keep_the_saved_graph_on_failure(client, svc, monkeypatch):
    paper = svc.papers.create({"title": "Parent", "doi": "10.1234/parent"})
    svc.citations.store.save(paper["id"], "crossref", [{"title": "Kept"}])
    install_transport(monkeypatch, lambda *a, **k: Response([]))
    result = client.post(f"/api/items/{paper['id']}/references")
    assert result.status_code == 502
    assert svc.citations.store.references(paper["id"])[0]["title"] == "Kept"
    assert svc.citations.store.fetch_info(paper["id"])["source"] == "crossref"


def test_repeated_pagination_falls_through_instead_of_looping(svc, monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if "semanticscholar" in url:
            return Response({**S2, "next": 0})
        if "crossref" in url:
            return Response({"message": {"reference": [{"DOI": "10.1234/cited"}]}})
        return Response({"referenced_works": []})

    install_transport(monkeypatch, get)
    assert (
        svc.plugins.fetch_references({"item": {"doi": "10.1234/parent"}, "email": "", "fresh": False})[0] == "crossref"
    )
    assert len([u for u in calls if "semanticscholar" in u]) == 1


def test_safe_mode_and_saved_choices(svc, tmp_path):
    svc.plugins.disable("reference-sources")
    restarted = create_app(svc.config, start_indexer=False)
    try:
        assert not restarted.extensions["synapse"].plugins.info("reference-sources")["enabled"]
    finally:
        restarted.extensions["synapse"].plugins.stop()
    data = tmp_path / "safe"
    data.mkdir()
    (data / "plugins.json").write_text(json.dumps({"plugins": {"reference-sources": {"enabled": True}}}))
    config = Config.from_env(data)
    config.safe_mode = True
    safe = create_app(config, start_indexer=False)
    try:
        info = safe.extensions["synapse"].plugins.info("reference-sources")
        assert not info["core"] and info["enabled"] and not info["running"] and info["status"] == "safe-mode"
    finally:
        safe.extensions["synapse"].plugins.stop()


def test_arxiv_html_uses_the_shared_queue_once(monkeypatch):
    turns = []
    install_transport(monkeypatch, lambda *a, **k: Response(content=HTML.encode()))
    monkeypatch.setattr(online_http, "ARXIV_SPACING", 3.0)
    monkeypatch.setattr(online_http.HOST_GAPS, "wait", lambda key, seconds: turns.append((key, seconds)))
    http = P.Client(PluginHttp("reference-sources", ["arxiv.org"], lambda: "test"))
    try:
        assert P.arxiv_html.fetch("2101.00001", http=http)
        assert turns == [("arxiv.org", 3.0)]
    finally:
        http.close()


def test_http_errors_do_not_expose_credential_query_values():
    class Http:
        def get(self, url, **kwargs):
            return PublicResponse(503, url + "?api_key=private-secret", {}, b"")

    client = P.Client(Http())
    try:
        with pytest.raises(HttpError) as error:
            client.get("https://api.openalex.org/works", retries=0)
        assert str(error.value).startswith("api.openalex.org:") and "private-secret" not in str(error.value)
    finally:
        client.close()


def test_stop_interrupts_retry_backoff_without_trying_another_provider():
    waiting = threading.Event()
    calls = []

    class Http:
        def get(self, url, **kwargs):
            calls.append(url)
            return PublicResponse(429, url, {"Retry-After": "10"}, b"{}")

    http = P.Client(Http())
    original = http.wait

    def wait(seconds):
        waiting.set()
        original(seconds)

    http.wait = wait
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(P.fetch, {"doi": "10.1234/parent"}, http=http)
        try:
            assert waiting.wait(2)
            http.close()
            with pytest.raises(PluginError, match="stopped"):
                future.result(timeout=2)
            assert len(calls) == 1
        finally:
            http.close()


def test_stop_discards_inflight_results_and_restart_can_fetch():
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Http:
        def get(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                entered.set()
                assert release.wait(3)
            return PublicResponse(200, url, {}, json.dumps(S2).encode())

    old = P.Client(Http())
    new = None
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(P.fetch, {"doi": "10.1234/parent"}, http=old)
        try:
            assert entered.wait(2)
            old.close()
            new = P.Client(old.http)
            assert P.fetch({"doi": "10.1234/parent"}, http=new)[0] == "semanticscholar"
            release.set()
            with pytest.raises(PluginError, match="stopped"):
                future.result(timeout=2)
        finally:
            release.set()
            old.close()
            if new:
                new.close()


@pytest.mark.parametrize("header", ["Retry-After", "retry-after"])
def test_retry_header_names_are_case_insensitive(header):
    class Http:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            return PublicResponse(429 if self.calls == 1 else 200, url, {header: "3"}, b"{}")

    http = P.Client(Http())
    delays = []
    http.wait = delays.append
    try:
        assert http.get("https://api.semanticscholar.org/x").status == 200
        assert delays == [3.0] and http.http.calls == 2
    finally:
        http.close()


def oa_work(n, cited, year=2024):
    return {
        "id": f"https://openalex.org/W{n}",
        "title": f"Citing {n}",
        "publication_year": year,
        "cited_by_count": cited,
    }


def s2_work(n, cited, date):
    return {
        "citingPaper": {
            "title": f"S2 citing {n}",
            "year": int(date[:4]),
            "publicationDate": date,
            "citationCount": cited,
            "externalIds": {"ArXiv": f"2401.0000{n}"},
        }
    }


def test_citing_works_ask_openalex_for_journal_papers_and_s2_for_preprints(svc, monkeypatch):
    calls = []

    def get(url, params=None, **kwargs):
        calls.append((url, params))
        if "openalex" in url:
            if "/works/doi:" in url:
                return Response({"id": "https://openalex.org/W9"})
            return Response({"meta": {"count": 7}, "results": [oa_work(1, 30), oa_work(2, 5)]})
        if url.endswith("/citations"):
            return Response(
                {"data": [s2_work(1, 3, "2024-01-02"), s2_work(2, 50, "2023-05-01"), s2_work(3, 1, "2025-03-04")]}
            )
        return Response({}, status=404)

    install_transport(monkeypatch, get)
    found = svc.plugins.find_citing({"doi": "10.1234/parent", "limit": 2, "sort": "cited", "since_year": 2020})
    assert (
        found["source"] == "OpenAlex"
        and found["total"] == 7
        and [w["title"] for w in found["works"]] == ["Citing 1", "Citing 2"]
    )
    assert found["works"][0]["cited_by_count"] == 30
    url, params = calls[1]
    assert params["filter"] == "cites:W9,publication_year:>2019" and params["sort"] == "cited_by_count:desc"
    assert params["per-page"] == "2"
    calls.clear()
    found = svc.plugins.find_citing({"arxiv": "2101.00001", "limit": 2, "sort": "recent", "since_year": 2024})
    assert found["source"] == "Semantic Scholar" and calls[0][0].endswith("/paper/arXiv:2101.00001/citations")
    assert [w["title"] for w in found["works"]] == ["S2 citing 3", "S2 citing 1"] and found["total"] == 3
    assert svc.plugins.info("reference-sources")["citing_sources"] == [
        {"id": "scholarly", "title": "OpenAlex and Semantic Scholar"}
    ]


def test_citing_works_fall_back_when_a_service_fails_or_knows_none(svc, monkeypatch):
    def get(url, params=None, **kwargs):
        if "semanticscholar" in url:
            return Response({}, status=429)
        if "/works/doi:" in url:
            return Response({"id": "https://openalex.org/W9"})
        return Response({"meta": {"count": 1}, "results": [oa_work(1, 0)]})

    install_transport(monkeypatch, get)
    monkeypatch.setattr(CITING.Client, "wait", lambda self, seconds: None)
    found = svc.plugins.find_citing({"arxiv": "2101.00001", "limit": 5})
    assert found["source"] == "OpenAlex" and found["works"][0]["title"] == "Citing 1"

    install_transport(monkeypatch, lambda url, **kw: Response({}, status=500 if "openalex" in url else 429))
    with pytest.raises(Exception, match="Could not get the citing works") as e:
        svc.plugins.find_citing({"doi": "10.1234/parent", "limit": 5})
    assert "429" in str(e.value)
    install_transport(monkeypatch, lambda url, **kw: Response({}, status=404))
    assert svc.plugins.find_citing({"doi": "10.1234/parent", "limit": 5})["works"] == []
