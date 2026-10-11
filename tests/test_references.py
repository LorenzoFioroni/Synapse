"""Reference lists / citation graph (network mocked)."""
import pytest

from backend import references as R
from backend.metadata import LookupError_


class Resp:
    def __init__(self, data, status=200, headers=None):
        self._d, self.status_code, self.ok, self.headers = data, status, status < 400, headers or {}

    def json(self):
        return self._d

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


S2_PAGE1 = {"offset": 0, "next": 2, "data": [
    {"citedPaper": {"paperId": "s2a", "title": "Matrix product states", "year": 2008, "venue": "Adv. Phys.",
                    "externalIds": {"DOI": "10.1080/14789940801912366"}, "authors": [{"name": "Frank Verstraete"}]}},
    {"citedPaper": {"paperId": None, "title": None, "externalIds": None}},     # unmatched: dropped
]}
S2_PAGE2 = {"offset": 2, "data": [
    {"citedPaper": {"paperId": "s2b", "title": "A preprint", "year": 2021, "venue": "",
                    "externalIds": {"ArXiv": "2101.00001v2"}, "authors": [{"name": "Ann Smith"}, {"name": "Bo Li"}]}},
]}
OPENALEX_WORK = {"id": "https://openalex.org/W1", "referenced_works": ["https://openalex.org/W9", "https://openalex.org/W8"]}
OPENALEX_LIST = {"results": [   # not in reference order
    {"id": "https://openalex.org/W8", "doi": None, "title": "On arXiv only", "publication_year": 2020,
     "authorships": [{"author": {"display_name": "Cara Diaz"}}],
     "primary_location": {"landing_page_url": "https://arxiv.org/abs/2002.12345v3", "source": None}},
    {"id": "https://openalex.org/W9", "doi": "https://doi.org/10.1103/PhysRevLett.1.1", "title": "Letter",
     "publication_year": 1999, "authorships": [], "primary_location": {"source": {"display_name": "PRL"}}},
]}
CROSSREF = {"message": {"reference": [
    {"key": "r1", "DOI": "10.1000/ABC", "year": "2001", "journal-title": "J"},
    {"key": "r2", "unstructured": "A. Author, Some  book (Publisher, 1990)."},
]}}


def router(routes, calls=None):
    def get(url, params=None, headers=None, timeout=None):
        if calls is not None:
            calls.append((url, dict(params or {})))
        for needle, answer in routes:
            if needle in url:
                return answer(url, params or {}) if callable(answer) else answer
        return Resp({}, 404)
    return get


def test_identifiers():
    assert R.identifiers({"doi": "https://doi.org/10.1103/PhysRevX.13.011033"}) == ("10.1103/physrevx.13.011033", None)
    assert R.identifiers({"doi": "10.48550/arXiv.2101.00001"}) == ("10.48550/arxiv.2101.00001", "2101.00001")
    assert R.identifiers({"archive_id": "arXiv:2101.00001v3"}) == (None, "2101.00001")
    with pytest.raises(LookupError_):
        R.fetch({"title": "No ids"})


def test_semantic_scholar_first_with_paging(monkeypatch):
    calls = []
    monkeypatch.setattr(R.requests, "get", router([
        ("semanticscholar", lambda url, p: Resp(S2_PAGE1 if not p.get("offset") else S2_PAGE2))], calls))
    source, refs = R.fetch({"doi": "10.1/X"}, s2_key="k")
    assert source == "semanticscholar"
    assert [r["title"] for r in refs] == ["Matrix product states", "A preprint"]
    assert refs[0]["doi"] == "10.1080/14789940801912366" and refs[0]["authors"] == "Verstraete, Frank"
    assert refs[1]["arxiv_id"] == "2101.00001" and refs[1]["authors"] == "Smith, Ann; Li, Bo"
    assert "/paper/DOI:10.1/x/references" in calls[0][0] and len(calls) == 2


def test_arxiv_items_ask_by_arxiv_id(monkeypatch):
    calls = []
    monkeypatch.setattr(R.requests, "get", router([("semanticscholar", Resp(S2_PAGE2))], calls))
    R.fetch({"doi": "10.48550/arXiv.2101.00001"})
    assert "/paper/arXiv:2101.00001/references" in calls[0][0]


def test_openalex_when_semantic_scholar_has_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(R.requests, "get", router([
        ("semanticscholar", Resp({"data": []})),
        ("openalex.org/works/doi:", Resp(OPENALEX_WORK)),
        ("openalex.org/works", Resp(OPENALEX_LIST))], calls))
    source, refs = R.fetch({"doi": "10.1/x"}, openalex_key="KEY")
    assert source == "openalex"
    assert [r["openalex_id"] for r in refs] == ["W9", "W8"]                   # reference order kept
    assert refs[0]["doi"] == "10.1103/physrevlett.1.1" and refs[0]["venue"] == "PRL"
    assert refs[1]["arxiv_id"] == "2002.12345" and refs[1]["authors"] == "Diaz, Cara"
    oa = [p for u, p in calls if "openalex" in u]
    assert all(p.get("api_key") == "KEY" for p in oa) and oa[1]["filter"] == "openalex:W9|W8"


def test_crossref_last(monkeypatch):
    monkeypatch.setattr(R.requests, "get", router([("crossref", Resp(CROSSREF))]))
    source, refs = R.fetch({"doi": "10.1/x"})
    assert source == "crossref"
    assert refs[0]["doi"] == "10.1000/abc" and refs[0]["year"] == 2001
    assert refs[1]["raw"] == "A. Author, Some book (Publisher, 1990)."


def test_errors(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    monkeypatch.setattr(R.requests, "get", router([("", Resp({}, 503))]))
    with pytest.raises(R.FetchFailed, match="Could not get the references"):
        R.fetch({"doi": "10.1/x"})
    # every service answered "nothing": not an error
    monkeypatch.setattr(R.requests, "get", router([]))
    assert R.fetch({"doi": "10.1/x"}) == ("none", [])


def test_rate_limit_is_retried(monkeypatch):
    slept = []
    monkeypatch.setattr(R.time, "sleep", slept.append)
    answers = iter([Resp({}, 429, {"Retry-After": "3"}), Resp(S2_PAGE2)])
    monkeypatch.setattr(R.requests, "get", router([("semanticscholar", lambda u, p: next(answers))]))
    assert R.fetch({"doi": "10.1/x"})[0] == "semanticscholar" and slept == [3.0]


def test_api_graph_and_cleanup(client, svc, monkeypatch):
    a = svc.papers.create({"title": "Citing paper", "doi": "10.1/X", "year": 2020})
    b = svc.papers.create({"title": "Matrix product states", "doi": "10.1080/14789940801912366", "year": 2008})
    c = svc.papers.create({"title": "A preprint", "archive_id": "arXiv:2101.00001"})
    monkeypatch.setattr(R.requests, "get", router([
        ("semanticscholar", lambda url, p: Resp(S2_PAGE1 if not p.get("offset") else S2_PAGE2))]))

    v = client.get(f"/api/items/{a['id']}/references").json
    assert v["fetch"] is None and v["references"] == [] and v["can_fetch"] is True
    v = client.post(f"/api/items/{a['id']}/references").json
    assert v["fetch"]["source"] == "semanticscholar" and v["fetch"]["count"] == 2
    assert [r["library_id"] for r in v["references"]] == [b["id"], c["id"]]
    assert [x["id"] for x in client.get(f"/api/items/{b['id']}/references").json["cited_by"]] == [a["id"]]
    assert [x["id"] for x in client.get(f"/api/items/{c['id']}/references").json["cited_by"]] == [a["id"]]

    g = client.get("/api/graph/citations").json
    assert {(e["source"], e["target"]) for e in g["edges"]} == {(f"item:{a['id']}", f"item:{b['id']}"),
                                                              (f"item:{a['id']}", f"item:{c['id']}")}
    assert {n["id"]: n["cited_by_library"] for n in g["nodes"]}[f"item:{b['id']}"] == 1
    assert g["stats"]["items_with_references"] == 1

    # no identifier: a clear 404, and the earlier list of another item is untouched
    d = svc.papers.create({"title": "No ids"})
    r = client.post(f"/api/items/{d['id']}/references")
    assert r.status_code == 404 and "DOI" in r.json["error"]
    assert client.post("/api/items/nope/references").status_code == 404
    # every service failing is a 502 (try again later), not "no identifier"
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    monkeypatch.setattr(R.requests, "get", router([("", Resp({}, 503))]))
    assert client.post(f"/api/items/{b['id']}/references").status_code == 502

    svc.papers.trash([a["id"]])
    client.post("/api/items/delete", json={"ids": [a["id"]]})
    assert svc.citations.store.references(a["id"]) == [] and svc.citations.store.fetch_info(a["id"]) is None


def test_fetched_automatically_after_add_by_identifier(client, svc, monkeypatch):
    from backend import metadata
    crossref_item = {"message": {"type": "journal-article", "title": ["Citing paper"], "DOI": "10.1234/X",
                                 "author": [{"family": "Cui", "given": "Jian"}], "published-print": {"date-parts": [[2015]]}}}
    monkeypatch.setattr(metadata.requests, "get", router([
        ("semanticscholar", lambda url, p: Resp(S2_PAGE2)), ("crossref", Resp(crossref_item))]))
    svc.settings.update({"add_fetch_references": "always"})
    item = client.post("/api/items/from_identifier", json={"identifier": "10.1234/X"}).json
    svc.citations.wait(10)
    assert [r["title"] for r in svc.citations.store.references(item["id"])] == ["A preprint"]

    svc.settings.update({"add_fetch_references": "never"})
    other = client.post("/api/items/from_identifier", json={"identifier": "10.1234/X", "allow_duplicate": True,
                                                            "fetch_references": True}).json
    svc.citations.wait(5)
    assert svc.citations.store.fetch_info(other["id"]) is None   # "never" wins over the request

    # "ask": the answer sent with the request decides
    svc.settings.update({"add_fetch_references": "ask"})
    no = client.post("/api/items/from_identifier", json={"identifier": "10.1234/X", "allow_duplicate": True}).json
    yes = client.post("/api/items/from_identifier", json={"identifier": "10.1234/X", "allow_duplicate": True,
                                                          "fetch_references": True}).json
    svc.citations.wait(10)
    assert svc.citations.store.fetch_info(no["id"]) is None
    assert svc.citations.store.fetch_info(yes["id"]) is not None


def test_queue_endpoint(client, svc, monkeypatch):
    monkeypatch.setattr(R.requests, "get", router([("semanticscholar", Resp(S2_PAGE2))]))
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    ids = [svc.papers.create({"title": f"P{i}", "doi": f"10.1/{i}"})["id"] for i in range(3)]
    assert client.post("/api/references/queue", json={"ids": ids + ids}).json["queued"] == 3
    svc.citations.wait(10)
    assert all(svc.citations.store.fetch_info(i)["count"] == 1 for i in ids)
    assert client.get("/api/references/status").json["queued"] == 0


def test_semantic_scholar_falls_back_to_arxiv_id_and_quotes_dois(monkeypatch):
    calls = []
    monkeypatch.setattr(R.requests, "get", router([("arXiv:2101.00001", Resp(S2_PAGE2))], calls))
    source, refs = R.fetch({"doi": "10.1002/(SICI)1097-0118;2-#", "archive_id": "arXiv:2101.00001v1"})
    assert source == "semanticscholar" and len(refs) == 1
    assert "DOI:10.1002/%28sici%291097-0118%3B2-%23/references" in calls[0][0]   # a raw "#" would cut the URL
    assert "/paper/arXiv:2101.00001/references" in calls[1][0]


def test_openalex_works_without_details_are_kept(monkeypatch):
    monkeypatch.setattr(R.requests, "get", router([
        ("semanticscholar", Resp({"data": []})),
        ("openalex.org/works/doi:", Resp({"referenced_works": ["https://openalex.org/W5"]})),
        ("openalex.org/works", Resp({"results": []}))]))
    source, refs = R.fetch({"doi": "10.1/x"})
    assert source == "openalex" and [r["openalex_id"] for r in refs] == ["W5"]


def test_retry_after_as_a_date(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    answers = iter([Resp({}, 429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), Resp(S2_PAGE2)])
    monkeypatch.setattr(R.requests, "get", router([("semanticscholar", lambda u, p: next(answers))]))
    assert R.fetch({"doi": "10.1/x"})[0] == "semanticscholar"


def test_title_key():
    assert R.title_key("Matrix-Product  States: <i>a</i> Review!") == R.title_key("matrix product states a review")
    assert R.title_key("Bañuls et al.: $\\mathcal{O}(N)$ scaling") == R.title_key("Banuls et al O N scaling")
    assert R.title_key("Introduction") is None   # too short to trust


def test_references_matched_by_title_when_identifiers_differ(client, svc, monkeypatch):
    """Library items found by title (+ year within one) when the reference has no identifier
    or a different one (e.g. the arXiv version of a paper stored with its journal DOI)."""
    a = svc.papers.create({"title": "Citing paper", "doi": "10.1/X", "year": 2020})
    journal = svc.papers.create({"title": "Tensor networks for open systems", "doi": "10.1103/PhysRevB.1.2", "year": 2022})
    noid = svc.papers.create({"title": "Matrix Product States.", "year": 2008})
    svc.papers.create({"title": "Some book on physics", "year": 1950})   # same title as a reference, years too far apart
    preprint = {"data": [
        {"citedPaper": {"paperId": "s2a", "title": "Matrix product states", "year": 2008, "externalIds": {}}},
        {"citedPaper": {"paperId": "s2b", "title": "Tensor Networks for Open Systems", "year": 2021,
                        "externalIds": {"ArXiv": "2101.00001", "DOI": "10.48550/arXiv.2101.00001"}}}]}
    monkeypatch.setattr(R.requests, "get", router([("semanticscholar", Resp(preprint))]))
    v = client.post(f"/api/items/{a['id']}/references").json
    assert [r["library_id"] for r in v["references"]] == [noid["id"], journal["id"]]
    assert [x["id"] for x in client.get(f"/api/items/{journal['id']}/references").json["cited_by"]] == [a["id"]]
    edges = {(e["source"], e["target"]) for e in client.get("/api/graph/citations").json["edges"]}
    assert (f"item:{a['id']}", f"item:{journal['id']}") in edges

    svc.citations.store.save(a["id"], "crossref", [{"title": "Some book on physics", "year": 1990}, {"raw": "Some book on physics, 1950"}])
    assert [r["library_id"] for r in client.get(f"/api/items/{a['id']}/references").json["references"]] == [None, None]


def test_library_index_is_reused_until_the_library_changes(svc, monkeypatch):
    """The item pane asks for references on every selection; the library is read only when it changed (#24)."""
    a = svc.papers.create({"title": "Cited work", "doi": "10.1/a"})
    b = svc.papers.create({"title": "Citing work", "doi": "10.1/b"})
    calls = []
    real = svc.papers.list
    monkeypatch.setattr(svc.papers, "list", lambda *x, **k: calls.append(1) or real(*x, **k))
    svc.citations.item_view(a["id"])
    svc.citations.item_view(b["id"])
    assert len(calls) == 1
    svc.papers.update(b["id"], {"title": "Citing work, revised"})
    svc.citations.item_view(a["id"])
    assert len(calls) == 2
    svc.papers.trash([b["id"]])
    svc.citations.item_view(a["id"])
    assert len(calls) == 3
    svc.citations.item_view(a["id"])
    assert len(calls) == 3
    index, items = svc.citations._library_index()
    assert "abstract" not in next(iter(items.values()))
