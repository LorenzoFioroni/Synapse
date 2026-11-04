"""Papers outside the library: the records plugins hand over, the lists they offer (item sources),
adding a paper with its "+", the services plugins use (lookups, references, how a paper connects to
the library), arXiv's HTML reference lists, and the Suggested Reading example."""

import importlib
import shutil
import time
from pathlib import Path

import pytest

from backend.online.http import LookupFailed
from backend.plugins import Offline, PluginError
from backend.plugins.outside import record
from backend.references import sources as reference_entry

arxiv_html = importlib.import_module("plugins.reference-sources.server.arxiv_html")
ref_sources = importlib.import_module("plugins.reference-sources.server.providers")

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "suggested-reading"

SOURCE = """
from backend.plugins import Plugin, PluginError

def activate(plugin: Plugin):
    store = plugin.storage.json("list")

    @plugin.item_source("today", title="Today", icon="calendar", description="Some papers", badge=lambda: len(store.get("papers", [])))
    def today():
        if store.get("fail"):
            raise PluginError("the list is broken")
        return store.get("papers", [])

    @plugin.route("/set", methods=["POST"])
    def set_list():
        from flask import request
        store.update(request.get_json())
        plugin.refresh_sources()
        return {}

    @plugin.route("/add", methods=["POST"])
    def add():
        from flask import request
        return plugin.actions.add(request.get_json())

    @plugin.route("/lookup")
    def lookup():
        from flask import request
        return plugin.services.lookup(request.args["id"])

    @plugin.route("/references", methods=["POST"])
    def references():
        from flask import request
        return {"refs": plugin.services.references(request.get_json())}

    @plugin.route("/connections", methods=["POST"])
    def connections():
        from flask import request
        b = request.get_json()
        return {"result": plugin.services.connections(b["papers"], b.get("refs"), b.get("folders"))}
"""


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def no_web(monkeypatch):
    """Lookups answer from a table; reference lists from another (no network in tests)."""
    from backend.online import metadata

    known = {
        "arXiv:2610.00001": {
            "title": "Looked-up title",
            "authors": "Doe, Jane",
            "year": 2026,
            "archive_id": "arXiv:2610.00001",
        },
        "10.1/known": {
            "title": "A known paper",
            "authors": "Roe, Rick",
            "year": 2020,
            "doi": "10.1/known",
            "journal": "J. Test",
        },
    }
    refs: dict[str, list[dict]] = {}

    def lookup(identifier=None, **_):
        if identifier not in known:
            raise LookupFailed(f"Nothing found for {identifier}")
        return dict(known[identifier])

    def fetch(item, **_):
        key = item.get("doi") or item.get("archive_id")
        if key == "offline":
            raise reference_entry.FetchFailed("no network")
        return "test", refs.get(key, [])

    monkeypatch.setattr(metadata, "lookup", lookup)
    monkeypatch.setattr(reference_entry, "fetch", fetch)
    return {"known": known, "refs": refs}


@pytest.fixture
def plugin(client, svc, make_plugin, no_web):
    make_plugin("lists", SOURCE, permissions={"library": ["read", "add"]})
    assert client.post("/api/plugin-host/lists/enable").get_json()["plugin"]["running"]
    return svc.plugins._active["lists"].plugin


def set_list(client, **values):
    assert client.post("/api/plugins/lists/set", json=values).status_code == 200


# ------------------------------------------------------------------ records
def test_records():
    r = record(
        {
            "arxiv": "arXiv:2610.01234v2",
            "title": "  A paper ",
            "authors": ["Jane Doe", "Roe, Rick"],
            "year": "2026-10-21",
            "categories": ["quant-ph", "cond-mat.str-el"],
            "reasons": [
                "Plain words",
                {"kind": "cites", "text": "Cites one of your papers", "items": ["abc"]},
                {"no": "text"},
            ],
            "extra": {"score": 3},
        },
        source="p.s",
    )
    assert r["id"] == "x:arxiv:2610.01234" and r["key"] == "arxiv:2610.01234" and r["_outside"]
    assert r["archive_id"] == "arXiv:2610.01234" and r["url"] == "https://arxiv.org/abs/2610.01234"
    assert r["authors"] == "Doe, Jane; Roe, Rick" and r["year"] == 2026 and r["item_type"] == "preprint"
    assert r["categories"] == "quant-ph, cond-mat.str-el" and r["_source"] == "p.s"
    assert r["reasons"] == [
        {"text": "Plain words", "kind": "", "items": []},
        {"text": "Cites one of your papers", "kind": "cites", "items": ["abc"]},
    ]
    assert r["extra_data"] == {"score": 3}
    d = record({"doi": "https://doi.org/10.1103/X.1", "journal": "PRL"})
    assert (
        d["key"] == "doi:10.1103/x.1"
        and d["url"] == "https://doi.org/10.1103/x.1"
        and d["item_type"] == "journalArticle"
    )
    with pytest.raises(PluginError):
        record({"title": "No identifier"})


@pytest.mark.parametrize(
    ("highlight", "expected"),
    [
        ({"label": "Following", "text": "Following: Jane Doe"}, {"label": "Following", "text": "Following: Jane Doe"}),
        ({"label": "Notice"}, {"label": "Notice", "text": "Notice"}),
        ({"label": "a" * 50, "text": "b" * 400}, {"label": "a" * 40, "text": "b" * 300}),
        ({"label": ""}, None),
        ("Following", None),
    ],
)
def test_source_highlight_is_bounded_plain_text(highlight, expected):
    paper = record({"doi": "10.1/example", "highlight": highlight})
    assert paper.get("highlight") == expected


# ------------------------------------------------------------------ lists in the app
def test_a_source_lists_library_items_and_outside_papers(client, svc, plugin):
    mine = client.post("/api/items", json={"title": "Mine", "doi": "10.1/mine", "abstract": "Long"}).get_json()
    set_list(
        client,
        papers=[
            {"arxiv": "2610.00002", "title": "New one", "abstract": "Kept for the pane", "reasons": ["Because"]},
            {"doi": "10.1/MINE", "reasons": ["Yours"]},
            {"arxiv": "2610.00002"},  # twice: listed once
        ],
    )
    src = client.get("/api/plugin-sources").get_json()["sources"]
    assert src == [
        {
            "id": "lists.today",
            "title": "Today",
            "icon": "calendar",
            "description": "Some papers",
            "plugin": "lists",
            "plugin_name": "Lists",
            "badge": "3",
        }
    ]
    items = client.get("/api/items?view=source:lists.today&lean=1").get_json()["items"]
    assert [i["id"] for i in items] == ["x:arxiv:2610.00002", mine["id"]]
    out, lib = items
    assert out["_outside"] and out["abstract"] == "Kept for the pane" and out["_rank"] == 0
    assert out["reasons"] == [{"text": "Because", "kind": "", "items": []}]
    assert lib["_rank"] == 1 and lib["_source"] == "lists.today" and lib["reasons"][0]["text"] == "Yours"
    assert "abstract" not in lib  # a library item stays lean
    # the search works on them as on any list
    found = client.get("/api/items?view=source:lists.today&q=new").get_json()["items"]
    assert [i["id"] for i in found] == ["x:arxiv:2610.00002"]
    assert "lists.today" in [s["id"] for s in client.get("/api/plugin-host").get_json()["plugins"][0]["sources"]]


def test_a_failing_or_unknown_source(client, svc, plugin):
    set_list(client, fail=True)
    r = client.get("/api/items?view=source:lists.today")
    assert r.status_code == 502 and "the list is broken" in r.get_json()["error"]
    assert client.get("/api/items?view=source:lists.nothing").status_code == 404
    set_list(client, fail=False, papers=[{"title": "no id"}])
    assert client.get("/api/items?view=source:lists.today").status_code == 502
    client.post("/api/plugin-host/lists/disable")
    assert client.get("/api/items?view=source:lists.today").status_code == 404
    assert client.get("/api/plugin-sources").get_json()["sources"] == []


def test_a_changed_list_is_announced(client, svc, plugin):
    since = svc.changes.seq
    set_list(client, papers=[])
    assert "sources" in svc.changes.since(since, svc.changes.epoch, None)["changes"]


# ------------------------------------------------------------------ adding
def test_adding_looks_the_paper_up(client, svc, plugin, no_web):
    folder = client.post("/api/folders", json={"name": "Reading"}).get_json()
    paper = record({"arxiv": "2610.00001", "title": "Listed title"})
    r = client.post("/api/outside/add", json={"paper": paper, "folder_id": folder["id"]})
    assert r.status_code == 201
    item = r.get_json()["item"]
    assert item["title"] == "Looked-up title" and item["archive_id"] == "arXiv:2610.00001"
    assert folder["id"] in item["folder_ids"] and item["citation_key"]
    again = client.post("/api/outside/add", json={"paper": {"arxiv": "2610.00001v3"}})
    assert again.status_code == 200 and again.get_json()["duplicate"] and again.get_json()["item"]["id"] == item["id"]
    # in the list it is now the library item
    set_list(client, papers=[{"arxiv": "2610.00001"}])
    assert client.get("/api/items?view=source:lists.today").get_json()["items"][0]["id"] == item["id"]


def test_adding_offline_keeps_what_the_list_said(client, svc, plugin):
    r = client.post(
        "/api/outside/add", json={"paper": {"doi": "10.1/unknown", "title": "As listed", "authors": ["Ann Lee"]}}
    )
    assert r.status_code == 201
    item = r.get_json()["item"]
    assert item["title"] == "As listed" and item["doi"] == "10.1/unknown" and item["authors"] == "Lee, Ann"
    assert client.post("/api/outside/add", json={"paper": {"title": "x"}}).status_code == 400
    assert client.post("/api/outside/add", json={}).status_code == 400


def test_plugins_add_with_the_add_permission(client, svc, plugin, make_plugin):
    r = client.post("/api/plugins/lists/add", json={"doi": "10.1/known"})
    assert r.status_code == 200
    assert r.get_json()["title"] == "A known paper" and not r.get_json()["duplicate"]
    assert client.post("/api/plugins/lists/add", json={"doi": "10.1/known"}).get_json()["duplicate"]
    make_plugin("reader", SOURCE, permissions={"library": ["read"]})
    client.post("/api/plugin-host/reader/enable")
    r = client.post("/api/plugins/reader/add", json={"doi": "10.1/other"})
    assert r.status_code == 400 and '"add"' in r.get_json()["error"]


# ------------------------------------------------------------------ services
def test_lookup_and_references(client, svc, plugin, no_web):
    got = client.get("/api/plugins/lists/lookup?id=arXiv:2610.00001").get_json()
    assert got["key"] == "arxiv:2610.00001" and got["title"] == "Looked-up title" and got["_outside"]
    r = client.get("/api/plugins/lists/lookup?id=10.1/none")
    assert r.status_code == 400 and "Nothing found" in r.get_json()["error"]
    no_web["refs"]["10.1/known"] = [{"doi": "10.1/a", "title": "A"}]
    assert (
        client.post("/api/plugins/lists/references", json={"doi": "10.1/known"}).get_json()["refs"][0]["doi"]
        == "10.1/a"
    )
    with pytest.raises(Offline):
        plugin.services.references({"doi": "offline"})


def test_connections(client, svc, plugin):
    lib = [
        client.post(
            "/api/items",
            json={
                "title": "Tensor network contraction for quantum circuits",
                "abstract": "We contract tensor networks to simulate quantum circuits.",
                "authors": "Cirac, J. Ignacio; Verstraete, Frank",
                "doi": "10.1/tn",
            },
        ).get_json(),
        client.post(
            "/api/items", json={"title": "Bird migration patterns", "authors": "Smith, Ann", "doi": "10.1/birds"}
        ).get_json(),
    ]
    for i in range(3):  # enough papers for word weights to mean something
        client.post("/api/items", json={"title": f"Unrelated topic number {i} about gardens and soil"})
    # a library paper cites the outside paper
    svc.citations.store.save(lib[1]["id"], "test", [{"doi": "10.1/new", "title": "x"}])
    paper = record(
        {
            "doi": "10.1/new",
            "title": "Contracting tensor networks of quantum circuits faster",
            "abstract": "Simulating quantum circuits by tensor network contraction.",
            "authors": ["J. I. Cirac", "Someone Else"],
        }
    )
    refs = {paper["key"]: [{"doi": "10.1/tn"}, {"doi": "10.1/elsewhere"}]}
    res = client.post("/api/plugins/lists/connections", json={"papers": [paper], "refs": refs}).get_json()["result"][0]
    kinds = {r["kind"]: r for r in res["reasons"]}
    assert set(kinds) == {"cites", "cited_by", "authors", "similar"}
    assert kinds["cites"]["items"] == [lib[0]["id"]] and kinds["cited_by"]["items"] == [lib[1]["id"]]
    assert "Cirac" in kinds["authors"]["text"] and kinds["similar"]["items"][0] == lib[0]["id"]
    assert res["score"] > 6
    # only papers in a folder count
    f = client.post("/api/folders", json={"name": "Birds"}).get_json()
    assert client.post("/api/items/folders", json={"ids": [lib[1]["id"]], "add": [f["id"]]}).status_code == 200
    res = client.post(
        "/api/plugins/lists/connections", json={"papers": [paper], "refs": refs, "folders": [f["id"]]}
    ).get_json()
    assert [r["kind"] for r in res["result"][0]["reasons"]] == ["cited_by"]
    # a paper with nothing in common
    far = record({"arxiv": "2610.00009", "title": "Volcanic ash", "authors": "Nobody, N."})
    assert client.post("/api/plugins/lists/connections", json={"papers": [far]}).get_json()["result"] == [
        {"score": 0.0, "reasons": []}
    ]


def test_authored_citations_require_a_matching_library_reference(client, svc, plugin):
    mine = client.post(
        "/api/items", json={"title": "My own work", "doi": "10.1/own", "authors": "Doe, Jane"}
    ).get_json()
    other = client.post(
        "/api/items", json={"title": "A reference", "doi": "10.1/ref", "authors": "Smith, Ann"}
    ).get_json()
    paper = {"key": "new", "authors": ["Jane Doe"]}
    refs = {"new": [{"doi": "10.1/own"}, {"doi": "10.1/ref"}, {"doi": "10.1/own"}, {"doi": "10.1/missing"}]}
    result = svc.connections.score([paper], refs, author_names=["Jane Doe"])[0]
    reasons = {r["kind"]: r for r in result["reasons"]}
    assert reasons["cites_own"]["items"] == [mine["id"]]
    assert reasons["cites"]["items"] == [other["id"]]
    assert result["score"] == 9.5  # two distinct citations plus a small shared-author signal
    missing = svc.connections.score([paper], {"new": [{"doi": "10.1/missing"}]}, author_names=["Jane Doe"])[0]
    assert not any(r["kind"] == "cites_own" for r in missing["reasons"])


def test_shared_author_signal_is_capped(client, svc, plugin):
    for i in range(8):
        client.post("/api/items", json={"title": f"Paper {i}", "authors": "Doe, Jane"})
    result = svc.connections.score([{"authors": ["Jane Doe"]}])[0]
    assert result["score"] == 1.5
    assert len(result["reasons"][0]["items"]) == 8


# ------------------------------------------------------------------ arXiv's HTML references
HTML = """<html><body><article>
<section class="ltx_bibliography" id="bib">
<ul class="ltx_biblist">
<li class="ltx_bibitem" id="bib.bib1"><span class="ltx_tag ltx_tag_bibitem">[1]</span>
<span class="ltx_bibblock">A. Author and B. Author, “Quantum things in a box,” <em class="ltx_emph ltx_font_italic">Phys. Rev. Lett.</em> <b>120</b>, 1 (2018).</span>
<span class="ltx_bibblock"><a href="https://doi.org/10.1103/PhysRevLett.120.010001" class="ltx_ref">doi</a></span></li>
<li class="ltx_bibitem" id="bib.bib2"><span class="ltx_tag ltx_tag_bibitem">[2]</span>
<span class="ltx_bibblock">C. Writer, <span class="ltx_text ltx_font_italic">An italic title that is long enough</span>, arXiv:2101.01234 (2021).</span></li>
<li class="ltx_bibitem" id="bib.bib3"><span class="ltx_tag ltx_tag_bibitem">[3]</span>
<span class="ltx_bibblock">D. Person, Preprint, <a href="https://doi.org/10.48550/arXiv.1905.05555">10.48550/arXiv.1905.05555</a>.</span></li>
<li class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[4]</span></li>
</ul></section></article></body></html>"""


def test_arxiv_html_references():
    refs = arxiv_html.parse(HTML)
    assert len(refs) == 3
    a, b, c = refs
    assert (
        a["doi"] == "10.1103/physrevlett.120.010001" and a["title"] == "Quantum things in a box" and a["year"] == 2018
    )
    assert "[1]" not in a["raw"]
    assert b["arxiv_id"] == "2101.01234" and b["title"] == "An italic title that is long enough" and b["year"] == 2021
    assert c["arxiv_id"] == "1905.05555" and c["doi"] is None
    assert arxiv_html.parse("<html>no bibliography</html>") == []


@pytest.fixture
def fetch_references():
    client = ref_sources.Client(None)
    yield lambda item, **kwargs: ref_sources.fetch(item, http=client, **kwargs)
    client.close()


def test_arxiv_html_is_the_last_source(monkeypatch, fetch_references):
    calls = []
    for name in ("semantic_scholar", "openalex", "crossref"):
        monkeypatch.setattr(ref_sources, name, lambda *a, _n=name, **k: calls.append(_n) or [])
    monkeypatch.setattr(
        arxiv_html, "fetch", lambda aid, **kwargs: calls.append(f"html {aid}") or [{"title": "From HTML"}]
    )
    assert fetch_references({"archive_id": "arXiv:2610.00003"}) == (
        "arxiv-html",
        ref_sources.usable([{"title": "From HTML"}]),
    )
    assert calls[-1] == "html 2610.00003"
    calls.clear()
    assert fetch_references({"doi": "10.1/x"}) == ("none", [])  # no arXiv id: not asked
    assert not any(c.startswith("html") for c in calls)


def test_recent_arxiv_papers_get_the_longer_list(monkeypatch, fetch_references):
    from datetime import date

    assert ref_sources.recent_arxiv("2610.01234", date(2026, 10, 22))
    assert ref_sources.recent_arxiv("2609.01234", date(2026, 10, 22))
    assert ref_sources.recent_arxiv("2612.01234", date(2027, 1, 5))
    assert not ref_sources.recent_arxiv("2608.01234", date(2026, 10, 22))
    assert not ref_sources.recent_arxiv("quant-ph/0101001", date(2026, 10, 22))
    monkeypatch.setattr(ref_sources, "recent_arxiv", lambda aid, today=None: aid.startswith("26"))
    s2 = [{"doi": "10.1/a"}]
    html = [{"doi": f"10.1/{i}"} for i in range(5)]
    calls = []
    monkeypatch.setattr(ref_sources, "semantic_scholar", lambda *a, **k: calls.append("s2") or s2)
    monkeypatch.setattr(arxiv_html, "fetch", lambda aid, **kwargs: calls.append("html") or html)
    # a recent paper: the HTML list is longer, so it wins
    name, refs = fetch_references({"archive_id": "arXiv:2610.00003"})
    assert name == "arxiv-html" and len(refs) == 5 and calls == ["s2", "html"]
    # an older one: the first list is kept
    calls.clear()
    assert fetch_references({"archive_id": "arXiv:2101.00003"})[0] == "semanticscholar" and calls == ["s2"]
    # fresh: the HTML version first, alone when it has a list
    calls.clear()
    assert fetch_references({"archive_id": "arXiv:2610.00003"}, fresh=True)[0] == "arxiv-html" and calls == ["html"]


# ------------------------------------------------------------------ the example
def test_the_suggested_reading_example(client, svc, no_web):
    shutil.copytree(EXAMPLE, svc.config.plugins_dir / "suggested-reading", ignore=shutil.ignore_patterns("__pycache__"))
    mine = client.post("/api/items", json={"title": "Mine", "authors": "Doe, Jane", "doi": "10.1/mine"}).get_json()
    assert client.post("/api/plugin-host/suggested-reading/enable").get_json()["plugin"]["running"]
    no_web["refs"]["arXiv:2610.00001"] = [{"doi": "10.1/mine"}]
    client.put(
        "/api/plugin-host/suggested-reading/settings", json={"papers": ["arXiv:2610.00001", "10.1/known", "10.1/none"]}
    )
    view = "/api/items?view=source:suggested-reading.suggested&lean=1"
    assert wait(
        lambda: len(client.get(view).get_json()["items"]) == 2 and "connection" in str(client.get(view).get_json())
    )
    items = client.get(view).get_json()["items"]
    first = items[0]
    assert first["id"] == "x:arxiv:2610.00001"  # it cites a library paper and shares an author
    assert {r["kind"] for r in first["reasons"]} >= {"cites", "authors"}
    assert first["_f"]["suggested-reading.connection"] > items[1]["_f"].get("suggested-reading.connection", 0)
    src = client.get("/api/plugin-sources").get_json()["sources"][0]
    assert src["badge"] == "2"
    added = client.post("/api/outside/add", json={"paper": first}).get_json()["item"]
    assert client.get("/api/plugin-sources").get_json()["sources"][0]["badge"] == "1"
    assert client.get(f"/api/items/{added['id']}/fields").get_json()["fields"][0]["value"] > 0  # the score came along
    assert mine


def test_multiple_source_badges_are_bounded_and_sanitized():
    badges = [
        {"label": "Following", "tone": "gold", "icon": "star"},
        {"label": "Cites you", "tone": "red", "icon": "megaphone"},
        {"label": "Unknown", "tone": "<style>", "icon": "<svg>"},
    ]
    paper = record({"doi": "10.1/example", "highlights": badges})
    assert [b["label"] for b in paper["highlights"]] == ["Following", "Cites you", "Unknown"]
    assert paper["highlights"][-1] == {"label": "Unknown", "text": "Unknown", "tone": "gold", "icon": "star"}
    assert len(record({"doi": "10.1/example", "highlights": badges * 10})["highlights"]) == 8
