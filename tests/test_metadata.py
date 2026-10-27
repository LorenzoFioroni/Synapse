import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.online import http, metadata
from backend.plugins import PluginError
from backend.plugins.http import PluginHttp
from backend.plugins.loading import load_module
from tests.fake_metadata import Response as FakeResp
from tests.fake_metadata import install_transport

package, _ = load_module("metadata_test", Path(__file__).resolve().parents[1] / "plugins/metadata-sources/server")
providers = package.providers
network = SimpleNamespace(get=None)


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Unexpected metadata HTTP request")

    monkeypatch.setattr(network, "get", unexpected)
    install_transport(monkeypatch, lambda *args, **kwargs: network.get(*args, **kwargs))
    instance = providers.Providers(
        PluginHttp(
            "metadata-test",
            ["api.crossref.org", "api.openalex.org", "export.arxiv.org", "openlibrary.org"],
            lambda: "test",
        )
    )
    original = metadata.lookup

    def source(query):
        try:
            return instance.resolve(query)
        except PluginError as e:
            raise metadata.LookupFailed(str(e)) from e

    def lookup(**kwargs):
        kwargs.setdefault("sources", source)
        return original(**kwargs)

    monkeypatch.setattr(metadata, "lookup", lookup)
    yield instance
    instance.close()


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

    monkeypatch.setattr(network, "get", fake_get)
    item = metadata.lookup(doi="https://doi.org/10.1103/PhysRevX.13.011033", known=[("Papić", "Zlatko")])
    assert item["title"] == "Superdiffusive Energy Transport"
    assert item["authors"] == "Ljubotina, Marko; Papić, Zlatko"
    assert item["year"] == "2023" and item["abstract"] == "We study transport."
    assert item["journal"] == "Physical Review X" and item["issn"] == "2160-3308"


@pytest.mark.parametrize("by_title", [False, True])
def test_lookup_uses_the_papers_author_names_not_openalex_profile_names(monkeypatch, by_title, resolver):
    fixture = json.loads((Path(__file__).parent / "fixtures/metadata_author_names.json").read_text())
    message = fixture["crossref"]["message"]

    def fake_get(url, **kwargs):
        if "openalex" in url:
            return FakeResp(fixture["openalex"])
        return FakeResp({"message": {"items": [message]}} if by_title else fixture["crossref"])

    monkeypatch.setattr(network, "get", fake_get)
    query = {"title": message["title"][0]} if by_title else {"doi": message["DOI"]}
    assert metadata.join_authors(resolver.openalex_authors(message["DOI"])) == fixture["expected"]
    assert metadata.lookup(**query)["authors"] == fixture["expected"]


@pytest.mark.parametrize(
    ("raw", "profile", "expected"),
    [
        ("Avery Rivera", "Avery Rivera X", ("Rivera", "Avery")),
        ("Mina de Vries", "M. de Vries", ("de Vries", "Mina")),
        ("García, Alex", "Alex Garcia", ("García", "Alex")),
        ("A. Rivera", "Avery Jordan Rivera", ("Rivera", "A.")),
        ("  Avery   Rivera  ", None, ("Rivera", "Avery")),
    ],
    ids=["spurious-suffix", "expanded-given-name", "accented-comma-name", "shorter-work-name", "no-profile"],
)
def test_openalex_prefers_work_author_name(monkeypatch, raw, profile, expected, resolver):
    monkeypatch.setattr(
        network,
        "get",
        lambda *args, **kwargs: FakeResp(
            {"authorships": [{"raw_author_name": raw, "author": {"display_name": profile} if profile else None}]}
        ),
    )
    assert resolver.openalex_authors("10.1234/author-name-precedence") == [expected]


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_openalex_authors_falls_back_to_profile_when_paper_name_is_blank(monkeypatch, raw, resolver):
    monkeypatch.setattr(
        network,
        "get",
        lambda *args, **kwargs: FakeResp(
            {"authorships": [{"raw_author_name": raw, "author": {"display_name": "Avery Rivera"}}]}
        ),
    )
    assert resolver.openalex_authors("10.1234/author-name-precedence") == [("Rivera", "Avery")]


def test_api_lookup_and_from_identifier(client, monkeypatch):
    monkeypatch.setattr(
        network, "get", lambda url, **kw: FakeResp(CROSSREF) if "crossref" in url else FakeResp({}, 404)
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
        super().__init__({}, status, content=content)


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
    monkeypatch.setattr(network, "get", fake_net(calls))
    item = metadata.lookup(title="Variational MPO", arxiv_id="2205.03415")
    assert item["title"].startswith("Variational Matrix Product Operators for the Steady State of Dissipative")
    assert item["item_type"] == "preprint" and item["archive_id"] == "arXiv:2205.03415"
    assert not any("crossref" in u for u, _ in calls)


def test_arxiv_doi_goes_to_arxiv(monkeypatch):
    calls = []
    monkeypatch.setattr(network, "get", fake_net(calls))
    item = metadata.lookup(doi="https://doi.org/10.48550/arXiv.2205.03415")
    assert item["archive_id"] == "arXiv:2205.03415"
    assert calls[0][1] == {"id_list": "2205.03415"}
    item = metadata.lookup(identifier="10.48550/arXiv.2205.03415")
    assert item["archive_id"] == "arXiv:2205.03415"


def test_title_falls_back_to_arxiv_search(monkeypatch):
    calls = []
    monkeypatch.setattr(network, "get", fake_net(calls))
    item = metadata.lookup(
        title="Variational matrix product operators for the steady state of dissipative quantum systems"
    )
    assert item["archive_id"] == "arXiv:2205.03415"
    assert [u for u, _ in calls][:2] == ["https://api.crossref.org/works", providers.ARXIV_API]
    assert calls[1][1]["search_query"].startswith("ti:(Variational AND matrix")


def test_no_match_anywhere_has_a_helpful_message(monkeypatch):
    monkeypatch.setattr(network, "get", fake_net([], arxiv_feed=[{"id": "9999.99999", "title": "Unrelated"}]))
    with pytest.raises(metadata.LookupFailed, match="Crossref or arXiv"):
        metadata.lookup(title="Variational matrix product operators for the steady state")


def test_published_doi_still_uses_crossref(monkeypatch):
    calls = []
    monkeypatch.setattr(network, "get", fake_net(calls, crossref=CROSSREF))
    item = metadata.lookup(doi="10.1103/PhysRevX.13.011033", arxiv_id="2205.03415")
    assert item["title"] == "Superdiffusive Energy Transport"


def test_api_lookup_for_arxiv_item(client, svc, monkeypatch):
    calls = []
    monkeypatch.setattr(network, "get", fake_net(calls))
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
            return FakeResp(
                {
                    "authorships": [
                        {"author": {"display_name": "Jian Cui"}},
                        {"author": {"display_name": "J. Ignacio Cirac"}},
                    ]
                }
            )
        return FakeResp({"message": {"title": ["T"], "DOI": "10.1/x", "author": [{"family": "Cui", "given": "J"}]}})

    monkeypatch.setattr(network, "get", fake_get)
    t0 = _t.monotonic()
    item = metadata.lookup(doi="10.1/x")
    assert item["authors"] == "Cui, Jian; Cirac, J. Ignacio" and _t.monotonic() - t0 < 2
    assert len(inside) == 2


def test_arxiv_requests_are_spaced(monkeypatch, resolver):
    import time as _t

    monkeypatch.setattr(http, "ARXIV_SPACING", 0.3)
    stamps = []
    monkeypatch.setattr(
        network,
        "get",
        lambda *a, **k: stamps.append(_t.monotonic()) or XmlResp(b"<feed xmlns='http://www.w3.org/2005/Atom'/>"),
    )

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(3) as ex:
        list(ex.map(lambda _: resolver._arxiv_entries({"id_list": "1"}), range(3)))
    stamps.sort()
    assert all(b - a >= 0.28 for a, b in zip(stamps, stamps[1:]))


def test_isbn_lookup_maps_open_library_fields(monkeypatch):
    isbn = "9780262033848"
    monkeypatch.setattr(
        network,
        "get",
        lambda *a, **k: FakeResp(
            {
                f"ISBN:{isbn}": {
                    "title": "Quantum systems",
                    "subtitle": "An introduction",
                    "publish_date": "May 2016",
                    "authors": [{"name": "Mina de Vries"}],
                    "publishers": [{"name": "Example Press"}],
                    "publish_places": [{"name": "Cambridge"}],
                    "url": "https://openlibrary.org/books/example",
                }
            }
        ),
    )
    item = metadata.lookup(identifier=isbn)
    assert item["title"] == "Quantum systems: An introduction"
    assert item["authors"] == "de Vries, Mina" and item["year"] == "2016"
    assert item["isbn"] == isbn and item["publisher"] == "Example Press" and item["place"] == "Cambridge"


@pytest.mark.parametrize("data", [None, [], {"message": []}, {"message": {"title": None, "author": [None]}}])
def test_malformed_crossref_data_is_a_readable_failure(monkeypatch, data):
    monkeypatch.setattr(network, "get", lambda url, **kwargs: FakeResp(data if "crossref" in url else {}, 200))
    with pytest.raises(metadata.LookupFailed, match="invalid data"):
        metadata.lookup(doi="10.1234/invalid")


def test_malformed_arxiv_feed_is_a_readable_failure(monkeypatch):
    monkeypatch.setattr(network, "get", lambda *a, **k: XmlResp(b"<not-an-atom-feed"))
    with pytest.raises(metadata.LookupFailed, match="invalid data"):
        metadata.lookup(arxiv_id="2101.00001")


def test_crossref_404_preserves_the_identifier_in_the_error(monkeypatch):
    monkeypatch.setattr(network, "get", lambda *a, **k: FakeResp({}, 404))
    with pytest.raises(metadata.LookupFailed, match="DOI 10.1234/missing was not found on Crossref"):
        metadata.lookup(doi="10.1234/missing")


def test_failed_arxiv_fallback_preserves_crossref_no_match(monkeypatch):
    monkeypatch.setattr(
        network,
        "get",
        lambda url, **kwargs: FakeResp({}, 503) if "arxiv.org" in url else FakeResp({"message": {"items": []}}),
    )
    with pytest.raises(metadata.LookupFailed, match="No match found on Crossref"):
        metadata.lookup(title="A research paper with no matching result")
