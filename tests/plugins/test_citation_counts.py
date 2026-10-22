"""The Citation Counts plugin (plugins/citation-counts) against fake OpenAlex and Semantic Scholar
answers, and what it needed from the API: the services' keys in plugin.http, and a refresher
answering LATER for papers it could not ask about."""

import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.plugins import http as H
from backend.plugins.fields import LATER, declare
from backend.plugins.http import PluginHttp, service_credentials
from backend.plugins.itemfields import FieldIndex

PID = "citation-counts"
FIELD = f"{PID}.citations"


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self.reason = "OK" if status < 400 else "Error"
        self.headers = headers or {}
        self._body = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.encoding = "utf-8"

    def iter_content(self, n):
        yield self._body

    def close(self):
        pass


class Web:
    """OpenAlex and Semantic Scholar, as far as the plugin asks them."""

    def __init__(self):
        self.openalex = {"10.1/known": 152, "10.48550/arxiv.2101.00002": 3}  # DOI -> count
        self.s2 = {"ARXIV:2101.00001": 40, "DOI:10.1/s2only": 7}
        self.s2_status = 200
        self.calls: list[tuple[str, str, dict, dict]] = []

    def __call__(self, method, url, data=None, json=None, headers=None, **kw):
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.calls.append((parts.hostname, method, q, dict(headers or {})))
        if parts.hostname == "api.openalex.org":
            dois = q["filter"].removeprefix("doi:").split("|")
            results = [
                {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/{d}", "cited_by_count": self.openalex[d]}
                for i, d in enumerate(dois)
                if d in self.openalex
            ]
            return FakeResp(200, {"results": results})
        if parts.hostname == "api.semanticscholar.org":
            if self.s2_status != 200:
                return FakeResp(self.s2_status, {"message": "Too Many Requests"})
            return FakeResp(
                200,
                [
                    {"citationCount": self.s2[i], "url": f"https://www.semanticscholar.org/paper/{i}"}
                    if i in self.s2
                    else None
                    for i in json["ids"]
                ],
            )
        raise AssertionError(url)


@pytest.fixture(autouse=True)
def _bundled(bundled):
    """This plugin comes with Synapse (plugins/)."""


@pytest.fixture
def web(monkeypatch):
    w = Web()
    monkeypatch.setattr(H.requests, "request", w)
    monkeypatch.setattr(H, "DEFAULT_GAP", 0)
    return w


@pytest.fixture
def plugin(client, svc, web):
    svc.plugins.private_network = True  # no name lookups for the fake hosts
    svc.settings.update(
        {"contact_email": "me@example.org", "openalex_api_key": "OAKEY", "semantic_scholar_api_key": "S2KEY"}
    )
    assert client.post(f"/api/plugin-host/{PID}/enable").get_json()["plugin"]["running"]
    return svc.plugins._active[PID].plugin


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def values(svc):
    return {k: (r["value"], r["source_url"]) for k, r in svc.plugins.fields.store.rows(PID, "citations").items()}


def add(client, **item):
    return client.post("/api/items", json={"title": "A paper", **item}).get_json()


# ------------------------------------------------------------------ the plugin
def test_it_is_bundled_and_off(client):
    p = next(p for p in client.get("/api/plugin-host").get_json()["plugins"] if p["id"] == PID)
    assert p["source"] == "bundled" and not p["enabled"]


def test_counts_from_both_services(client, svc, web, plugin):
    a = add(client, doi="10.1/known")
    b = add(client, archive_id="arXiv:2101.00001")  # a preprint: Semantic Scholar first
    c = add(client, doi="10.1/s2only")  # OpenAlex does not know it
    d = add(client, archive_id="arXiv:2101.00002")  # only OpenAlex knows it, by its arXiv DOI
    e = add(client, doi="10.1/nowhere")
    assert wait(lambda: len(values(svc)) == 5)
    v = values(svc)
    assert v["doi:10.1/known"] == (152, "https://openalex.org/W0")
    assert v["arxiv:2101.00001"] == (40, "https://www.semanticscholar.org/paper/ARXIV:2101.00001")
    assert v["doi:10.1/s2only"][0] == 7
    assert v["arxiv:2101.00002"][0] == 3
    assert v["doi:10.1/nowhere"] == (None, None)  # every service answered: not found
    # the list, the search and the item pane
    items = {i["id"]: i for i in client.get("/api/items?lean=1").get_json()["items"]}
    assert items[a["id"]]["_f"][FIELD] == 152 and FIELD not in items[e["id"]].get("_f", {})
    found = {i["id"] for i in client.get("/api/items?q=citations:>10").get_json()["items"]}
    assert found == {a["id"], b["id"]}
    pane = client.get(f"/api/items/{c['id']}/fields").get_json()["fields"][0]
    assert pane["value"] == 7 and pane["source"] == "OpenAlex / Semantic Scholar"
    assert d
    # Synapse's keys went to the services that use them, and to no other
    oa = [c for c in web.calls if c[0] == "api.openalex.org"]
    s2 = [c for c in web.calls if c[0] == "api.semanticscholar.org"]
    assert all(q.get("api_key") == "OAKEY" and q.get("mailto") == "me@example.org" for _, _, q, _ in oa)
    assert all(h.get("x-api-key") == "S2KEY" and "api_key" not in q for _, _, q, h in s2)
    assert all("x-api-key" not in h for _, _, _, h in oa)
    assert "plugin citation-counts" in oa[0][3]["User-Agent"]


def test_without_semantic_scholar(client, svc, web, plugin):
    client.put(f"/api/plugin-host/{PID}/settings", json={"semantic_scholar": False})
    add(client, archive_id="arXiv:2101.00001")
    add(client, archive_id="arXiv:2101.00002")
    assert wait(lambda: len(values(svc)) == 2)
    assert values(svc)["arxiv:2101.00001"] == (None, None) and values(svc)["arxiv:2101.00002"][0] == 3
    assert not [c for c in web.calls if c[0] == "api.semanticscholar.org"]


def test_a_service_that_is_down_means_later(client, svc, web):
    web.s2_status = 429
    add(client, doi="10.1/known")
    add(client, archive_id="arXiv:2101.00001")  # only Semantic Scholar has it
    svc.plugins.private_network = True
    client.post(f"/api/plugin-host/{PID}/enable")  # both in one batch
    plugin = svc.plugins._active[PID].plugin
    assert wait(lambda: "doi:10.1/known" in values(svc))
    time.sleep(0.3)
    assert "arxiv:2101.00001" not in values(svc)  # not stored as "not found"
    d = plugin.fields.defs["citations"]
    assert [p["id"] for p in svc.plugins.fields.due(d, svc.papers.list())] == []  # not handed over at once
    later = [p["id"] for p in svc.plugins.fields.due(d, svc.papers.list(), now=time.time() + 2 * 3600)]
    assert len(later) == 1  # but in an hour
    assert svc.plugins.info(PID)["running"]  # not a fault


def test_every_service_down_leaves_the_batch(client, svc, web, plugin):
    web.s2_status = 503
    add(client, archive_id="arXiv:2101.00001")
    time.sleep(0.5)
    assert values(svc) == {}
    d = plugin.fields.defs["citations"]
    assert len(svc.plugins.fields.due(d, svc.papers.list())) == 1  # asked again at the next round


# ------------------------------------------------------------------ the API it needed
def test_service_credentials():
    s = {"openalex_api_key": "K", "contact_email": "a@b.c", "semantic_scholar_api_key": "S"}
    assert service_credentials("api.openalex.org", s) == ({"api_key": "K", "mailto": "a@b.c"}, {})
    assert service_credentials("api.semanticscholar.org", s) == ({}, {"x-api-key": "S"})
    assert service_credentials("example.org", s) == ({}, {})
    assert service_credentials("api.semanticscholar.org", {}) == ({}, {})


def test_keys_stay_with_their_service_after_a_redirect(monkeypatch):
    seen = []

    def fake(method, url, headers=None, **kw):
        seen.append((urlsplit(url).hostname, dict(headers)))
        if len(seen) == 1:
            return FakeResp(302, b"", {"Location": "https://elsewhere.org/x"})
        return FakeResp(200, b"ok")

    monkeypatch.setattr(H.requests, "request", fake)
    monkeypatch.setattr(H, "DEFAULT_GAP", 0)
    http = PluginHttp(
        "p",
        ["api.semanticscholar.org", "elsewhere.org"],
        lambda: "UA",
        private=True,
        credentials=lambda host: service_credentials(host, {"semantic_scholar_api_key": "S"}),
    )
    http.get("https://api.semanticscholar.org/x")
    assert seen[0][1]["x-api-key"] == "S" and "x-api-key" not in seen[1][1]


def test_later(tmp_path):
    asked = []

    def refresher(papers):
        asked.append([p["key"] for p in papers])
        return {p["key"]: (LATER if p["key"] == "doi:10.1/b" else 1) for p in papers}

    d = declare("p", "P", "n", type="int", refresh="7d", refresher=refresher)
    idx = FieldIndex(tmp_path / "fields.db", lambda: [d])
    items = [{"id": "1", "doi": "10.1/a", "title": "A"}, {"id": "2", "doi": "10.1/b", "title": "B"}]
    import logging

    assert idx.refresh(d, items, logging.getLogger("t")) == 2
    assert set(idx.store.rows("p", "n")) == {"doi:10.1/a"}
    assert idx.refresh(d, items, logging.getLogger("t")) == 0  # b waits
    assert [p["id"] for p in idx.due(d, items, now=time.time() + 3601)] == ["2"]
