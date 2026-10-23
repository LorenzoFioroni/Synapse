import importlib.util
from pathlib import Path

import pytest

from backend.online import http, metadata

spec = importlib.util.spec_from_file_location(
    "metadata_providers_test", Path(__file__).resolve().parents[1] / "plugins/metadata-sources/server/providers.py"
)
assert spec and spec.loader
providers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(providers)


@pytest.fixture(autouse=True)
def _provider_for_direct_queries(monkeypatch):
    # Standalone core-query tests explicitly supply a source; app calls retain their live host.
    original = metadata.lookup

    def lookup(**kwargs):
        kwargs.setdefault("sources", providers.resolve)
        return original(**kwargs)

    monkeypatch.setattr(metadata, "lookup", lookup)


def test_classify_identifier():
    assert metadata.classify_identifier("https://doi.org/10.1103/PhysRevX.13.011033") == (
        "doi",
        "10.1103/PhysRevX.13.011033",
    )
    assert metadata.classify_identifier("arXiv:2205.03415v2") == ("arxiv", "2205.03415")
    assert metadata.classify_identifier("https://arxiv.org/abs/2205.03415") == ("arxiv", "2205.03415")
    assert metadata.classify_identifier("978-0-262-03384-8") == ("isbn", "9780262033848")
    with pytest.raises(metadata.LookupFailed):
        metadata.classify_identifier("hello world")


def test_resolve_authors_prefers_known_accents():
    known = [("Bañuls", "Mari Carmen"), ("Žnidarič", "Marko")]
    out = metadata.resolve_authors([("Banuls", "Mari Carmen"), ("Znidaric", "M."), ("Cui", "Jian")], known)
    # exact given-name match only: "M." is not expanded to "Marko"
    assert out == "Bañuls, Mari Carmen; Znidaric, M.; Cui, Jian"


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self._d

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


CROSSREF = {
    "message": {
        "type": "journal-article",
        "title": ["Superdiffusive Energy Transport"],
        "DOI": "10.1103/PhysRevX.13.011033",
        "author": [{"family": "Ljubotina", "given": "Marko"}, {"family": "Papic", "given": "Zlatko"}],
        "container-title": ["Physical Review X"],
        "published-print": {"date-parts": [[2023, 2]]},
        "volume": "13",
        "issue": "1",
        "page": "011033",
        "abstract": "<jats:p>We study transport.</jats:p>",
        "ISSN": ["2160-3308"],
        "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
    }
}


def test_lookup_doi(monkeypatch):
    def fake_get(url, **kw):
        if "crossref" in url:
            return FakeResp(CROSSREF)
        return FakeResp({}, 404)  # OpenAlex unavailable

    monkeypatch.setattr(providers.requests, "get", fake_get)
    item = metadata.lookup(doi="https://doi.org/10.1103/PhysRevX.13.011033", known=[("Papić", "Zlatko")])
    assert item["title"] == "Superdiffusive Energy Transport"
    assert item["authors"] == "Ljubotina, Marko; Papić, Zlatko"
    assert item["year"] == "2023" and item["abstract"] == "We study transport."
    assert item["journal"] == "Physical Review X" and item["issn"] == "2160-3308"


def test_api_lookup_and_from_identifier(client, monkeypatch):
    monkeypatch.setattr(
        providers.requests, "get", lambda url, **kw: FakeResp(CROSSREF) if "crossref" in url else FakeResp({}, 404)
    )
    r = client.post("/api/items/from_identifier", json={"identifier": "10.1103/PhysRevX.13.011033"})
    assert r.status_code == 201
    item = r.get_json()
    assert item["citation_key"] == "ljubotinaSuperdiffusive2023"
    # adding again reports the duplicate
    r2 = client.post("/api/items/from_identifier", json={"identifier": "10.1103/PhysRevX.13.011033"})
    assert r2.status_code == 409 and r2.get_json()["duplicate_of"] == item["id"]
    client.patch(f"/api/items/{item['id']}", json={"title": "old title"})
    look = client.post(f"/api/items/{item['id']}/lookup").get_json()
    assert look["current"]["title"] == "old title" and look["fetched"]["title"] == "Superdiffusive Energy Transport"


# ---------------------------------------------------------------- arXiv (bugs.md #1)
def atom(entries):
    body = "".join(
        f"""
  <entry>
    <id>http://arxiv.org/abs/{e["id"]}</id>
    <published>{e.get("published", "2022-05-06T17:59:59Z")}</published>
    <title>{e["title"]}</title>
    <summary>  {e.get("summary", "An abstract.")} </summary>
    <author><name>Jian Cui</name></author><author><name>J. Ignacio Cirac</name></author>
    {f"<arxiv:doi>{e['doi']}</arxiv:doi>" if e.get("doi") else ""}
  </entry>"""
        for e in entries
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query</title>{body}
</feed>""".encode()


class XmlResp(FakeResp):
    def __init__(self, content, status=200):
        super().__init__({}, status)
        self.content = content


PREPRINT = {
    "id": "2205.03415v2",
    "title": "Variational Matrix Product Operators for the Steady State\n  of Dissipative Quantum Systems",
}
CROSSREF_UNRELATED = {
    "message": {
        "items": [{"title": ["Steady states of something else entirely"], "DOI": "10.1/x", "type": "journal-article"}]
    }
}


def fake_net(calls, arxiv_feed=None, crossref=CROSSREF_UNRELATED):
    def get(url, params=None, **kw):
        calls.append((url, params))
        if "arxiv.org" in url:
            return XmlResp(atom(arxiv_feed if arxiv_feed is not None else [PREPRINT]))
        if "crossref" in url:
            return FakeResp(crossref)
        return FakeResp({}, 404)

    return get


def test_title_similarity_tolerates_small_differences():
    s = metadata.title_similarity
    assert s("Superdiffusive energy transport", "The superdiffusive energy transport") > 0.85
    assert s("Variational MPO for steady states", "Variational M.P.O. for steady-states") > 0.95
    assert s("Variational MPO for steady states", "Quantum chemistry of proteins") < 0.6


def test_arxiv_item_is_looked_up_on_arxiv_not_crossref(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.requests, "get", fake_net(calls))
    item = metadata.lookup(title="Variational MPO", arxiv_id="2205.03415")
    assert item["title"].startswith("Variational Matrix Product Operators for the Steady State of Dissipative")
    assert item["item_type"] == "preprint" and item["archive_id"] == "arXiv:2205.03415"
    assert not any("crossref" in u for u, _ in calls)


def test_arxiv_doi_goes_to_arxiv(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.requests, "get", fake_net(calls))
    item = metadata.lookup(doi="https://doi.org/10.48550/arXiv.2205.03415")
    assert item["archive_id"] == "arXiv:2205.03415"
    assert calls[0][1] == {"id_list": "2205.03415"}
    item = metadata.lookup(identifier="10.48550/arXiv.2205.03415")
    assert item["archive_id"] == "arXiv:2205.03415"


def test_title_falls_back_to_arxiv_search(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.requests, "get", fake_net(calls))
    item = metadata.lookup(
        title="Variational matrix product operators for the steady state of dissipative quantum systems"
    )
    assert item["archive_id"] == "arXiv:2205.03415"
    assert [u for u, _ in calls][:2] == ["https://api.crossref.org/works", providers.ARXIV_API]
    assert calls[1][1]["search_query"].startswith("ti:(Variational AND matrix")


def test_no_match_anywhere_has_a_helpful_message(monkeypatch):
    monkeypatch.setattr(
        providers.requests, "get", fake_net([], arxiv_feed=[{"id": "9999.99999", "title": "Unrelated"}])
    )
    with pytest.raises(metadata.LookupFailed, match="Crossref or arXiv"):
        metadata.lookup(title="Variational matrix product operators for the steady state")


def test_published_doi_still_uses_crossref(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.requests, "get", fake_net(calls, crossref=CROSSREF))
    item = metadata.lookup(doi="10.1103/PhysRevX.13.011033", arxiv_id="2205.03415")
    assert item["title"] == "Superdiffusive Energy Transport"


def test_api_lookup_for_arxiv_item(client, svc, monkeypatch):
    calls = []
    monkeypatch.setattr(providers.requests, "get", fake_net(calls))
    it = svc.papers.create({"title": "Variational MPO", "url": "https://arxiv.org/abs/2205.03415"})
    r = client.post(f"/api/items/{it['id']}/lookup")
    assert r.status_code == 200, r.json
    assert r.json["fetched"]["archive_id"] == "arXiv:2205.03415"


def test_doi_lookup_asks_crossref_and_openalex_side_by_side(monkeypatch):
    import threading
    import time as _t

    inside = []
    both = threading.Barrier(2, timeout=3)  # fails if the two requests are not in flight together

    def fake_get(url, **k):
        inside.append(url)
        both.wait()
        if "openalex" in url:
            return type(
                "R",
                (),
                {
                    "ok": True,
                    "json": lambda self: {
                        "authorships": [
                            {"author": {"display_name": "Jian Cui"}},
                            {"author": {"display_name": "J. Ignacio Cirac"}},
                        ]
                    },
                },
            )()
        return type(
            "R",
            (),
            {
                "status_code": 200,
                "raise_for_status": lambda self: None,
                "json": lambda self: {
                    "message": {"title": ["T"], "DOI": "10.1/x", "author": [{"family": "Cui", "given": "J"}]}
                },
            },
        )()

    monkeypatch.setattr(providers.requests, "get", fake_get)
    t0 = _t.monotonic()
    item = metadata.lookup(doi="10.1/x")
    assert item["authors"] == "Cui, Jian; Cirac, J. Ignacio" and _t.monotonic() - t0 < 2
    assert len(inside) == 2


def test_arxiv_requests_are_spaced(monkeypatch):
    import time as _t

    monkeypatch.setattr(http, "ARXIV_SPACING", 0.3)
    stamps = []
    monkeypatch.setattr(
        providers.requests,
        "get",
        lambda *a, **k: (
            stamps.append(_t.monotonic())
            or type(
                "R",
                (),
                {"raise_for_status": lambda self: None, "content": b"<feed xmlns='http://www.w3.org/2005/Atom'/>"},
            )()
        ),
    )
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(3) as ex:
        list(ex.map(lambda _: providers._arxiv_entries({"id_list": "1"}), range(3)))
    stamps.sort()
    assert all(b - a >= 0.28 for a, b in zip(stamps, stamps[1:]))
