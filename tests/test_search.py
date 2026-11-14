import time

from backend.library.search import parse_query, search

PAPERS = [
    {
        "id": "1",
        "title": "Variational Matrix Product Operators",
        "authors": "Bañuls, Mari Carmen",
        "abstract": "steady state",
        "journal": "PRL",
        "doi": "10.1/abc",
        "attachments": [],
    },
    {
        "id": "2",
        "title": "Superdiffusive Energy Transport",
        "authors": "Ljubotina, Marko",
        "abstract": "",
        "journal": "Physical Review X",
        "attachments": [{"name": "supplement.pdf"}],
    },
]


class FakeFT:
    def match(self, term):
        return {"2": f"…<mark>{term}</mark>…"} if term.lower() == "lindblad" else {}


def ids(r):
    return [p["id"] for p in r[0]]


def test_parse_query():
    assert parse_query('a "b c" -d') == [("a", False, None), ("b c", False, None), ("d", True, None)]


def test_basic_is_accent_insensitive():
    assert ids(search(PAPERS, "banuls", "basic")) == ["1"]


def test_basic_does_not_search_journal():
    assert ids(search(PAPERS, "physical", "basic")) == []
    assert ids(search(PAPERS, "physical", "all")) == ["2"]
    assert ids(search(PAPERS, "supplement", "all")) == ["2"]


def test_and_and_not():
    assert ids(search(PAPERS, "energy transport", "basic")) == ["2"]
    assert ids(search(PAPERS, "-energy", "basic")) == ["1"]


def test_fulltext():
    r = search(PAPERS, "lindblad", "fulltext", FakeFT())
    assert ids(r) == ["2"] and "mark" in r[1]["2"]["fulltext"]
    assert ids(search(PAPERS, "lindblad", "all", FakeFT())) == []


ORG = [
    {
        "id": "a",
        "title": "Spin transport",
        "authors": "Doe, J",
        "year": 2019,
        "item_type": "journalArticle",
        "tags": ["Transport", "open systems"],
        "folder_ids": [2, 3],
        "attachments": [{"name": "x.pdf"}],
        "doi": "10.1/a",
    },
    {
        "id": "b",
        "title": "Open quantum systems",
        "authors": "Breuer, H",
        "year": 2002,
        "item_type": "book",
        "tags": [],
        "folder_ids": [],
        "attachments": [],
    },
    {
        "id": "c",
        "title": "Energy transport",
        "authors": "Roe, R",
        "year": 2023,
        "item_type": "journalArticle",
        "tags": ["transport"],
        "folder_ids": [1],
        "attachments": [],
    },
]
CTX = {
    "folder_paths": {1: ["Physics"], 2: ["Physics", "Transport"], 3: ["Reading group"]},
    "type_labels": {"book": "Book", "bookSection": "Book Section", "journalArticle": "Journal Article"},
}


def f(q, mode="basic"):
    return [p["id"] for p in search(ORG, q, mode, None, CTX)[0]]


def test_filters():
    assert f("tag:transport") == ["a", "c"]  # case-insensitive
    assert f('tag:"open systems"') == ["a"]
    assert f("tag:none") == ["b"]
    assert f("-tag:transport") == ["b"]
    assert f("folder:physics") == ["a", "c"]  # any folder on the path
    assert f('folder:"Physics/Transport"') == ["a"]
    assert f("folder:none") == ["b"]
    assert f('folder:"reading group"') == ["a"]  # any of the item's folders
    assert f("has:folder") == ["a", "c"]
    assert f("type:book") == ["b"] and f("type:journal") == ["a", "c"]
    assert f("year:2019") == ["a"] and f("year:2000-2020") == ["a", "b"] and f("year:>2010") == ["a", "c"]
    assert f("has:pdf") == ["a"] and f("has:doi") == ["a"] and f("-has:pdf") == ["b", "c"]
    assert f("author:roe") == ["c"]


def test_type_names_a_whole_type_or_the_start_of_one():
    items = [{"id": t, "title": "x", "item_type": t} for t in ("book", "bookSection", "journalArticle")]
    t = lambda q: [p["id"] for p in search(items, q, "basic", None, CTX)[0]]
    assert t("type:book") == ["book"]  # not bookSection
    assert t('type:"book section"') == t("type:bookSection") == ["bookSection"]
    assert t("-type:book") == ["bookSection", "journalArticle"]
    assert t("type:boo") == ["book", "bookSection"]  # not a whole name: any type starting so
    assert t("type:journal") == t("type:article") == ["journalArticle"]


def test_filters_combine_with_text():
    assert f("transport year:>2020") == ["c"]
    assert f("tag:transport -energy") == ["a"]
    assert f("arXiv:2205.03415") == []  # unknown prefix = plain text, no crash


def test_search_finds_items_through_notes(client, svc):
    a = svc.papers.create({"authors": "Cui, Jian", "title": "Variational operators", "year": 2015})
    b = svc.papers.create({"authors": "Doe, J", "title": "Unrelated", "year": 2001})
    svc.papers.create({"authors": "Roe, K", "title": "Also unrelated", "year": 2002})
    client.post("/api/notes/note", json={"item_id": a["id"], "text": "Uses Lindblad steady states."})
    client.put(
        "/api/notes/note",
        json={
            "path": "Idea.md",
            "create": True,
            "text": f"Doe's trick [@{b['citation_key']}] avoids sign problems.\nOther line about steady states.",
        },
    )
    ids = lambda r: {i["id"] for i in r.json["items"]}
    r = client.get("/api/items", query_string={"q": "steady states", "mode": "all"})
    assert ids(r) == {a["id"]}  # paper note text; the other line does not cite b
    assert (
        "Note" in r.json["hits"][a["id"]]["fulltext"] and "<mark>steady</mark>" in r.json["hits"][a["id"]]["fulltext"]
    )
    r = client.get("/api/items", query_string={"q": "sign problems", "mode": "fulltext"})
    assert ids(r) == {b["id"]}  # the line that cites b
    assert client.get("/api/items", query_string={"q": "sign problems", "mode": "basic"}).json["items"] == []
    r = client.get("/api/items", query_string={"q": "-lindblad", "mode": "all"})
    assert a["id"] not in ids(r) and b["id"] in ids(r)  # negation works through notes too
    # the smart-folder editor's count agrees with what the folder will show
    sf = client.post("/api/smart", json={"name": "Sign", "query": "sign problems", "mode": "all"}).json
    shown = ids(client.get("/api/items", query_string={"view": f"smart:{sf['id']}"}))
    preview = client.get("/api/search/preview", query_string={"q": "sign problems", "mode": "all"}).json
    assert shown == {b["id"]} and preview["count"] == 1 and preview["sample"][0]["id"] == b["id"]
    svc.settings.update({"notes_enabled": False})
    assert client.get("/api/items", query_string={"q": "sign problems", "mode": "all"}).json["items"] == []
    assert client.get("/api/search/preview", query_string={"q": "sign problems", "mode": "all"}).json["count"] == 0


def test_notes_search_sees_edits_made_by_another_process(client, svc):
    """The MCP server edits notes and updates notes_index.db in its own process."""
    from backend.notes import NotesIndex, Vault

    a = svc.papers.create({"authors": "Cui, Jian", "title": "Variational operators", "year": 2015})
    client.post("/api/notes/note", json={"item_id": a["id"], "text": "Uses Lindblad steady states."})
    found = lambda q: {i["id"] for i in client.get("/api/items", query_string={"q": q, "mode": "all"}).json["items"]}
    assert found("lindblad") == {a["id"]}
    assert found("kraus") == set()

    vault = Vault(svc.config.notes_dir)  # what the other process has: its own objects, same files
    other = NotesIndex(svc.config.notes_index_path, vault)
    (path,) = [n["path"] for n in other.list()]
    text, h = vault.read(path)
    vault.write(path, text.replace("Lindblad", "Kraus"), expected_hash=h)
    other.update(path)

    assert found("kraus") == {a["id"]}
    assert found("lindblad") == set()


def test_renamed_attachment_is_searched_by_its_new_name(client, svc, monkeypatch):
    from backend.library import repository

    item = svc.papers.create({"title": "Plain title"})
    att = svc.papers.add_attachment(item["id"], "Supplement", "s.pdf")
    found = lambda q: [i["id"] for i in client.get("/api/items", query_string={"q": q, "mode": "all"}).json["items"]]
    assert found("supplement") == [item["id"]]
    monkeypatch.setattr(repository, "utcnow", lambda: "2099-01-01T00:00:00Z")
    client.patch(f"/api/attachments/{att['id']}", json={"name": "Appendix"})
    assert svc.papers.get(item["id"])["date_modified"] == "2099-01-01T00:00:00Z"  # the item changed
    assert found("supplement") == []
    assert found("appendix") == [item["id"]]


def test_haystack_follows_attachment_names_even_within_one_second():
    paper = {"id": "x", "title": "t", "date_modified": "2024-01-01T00:00:00Z", "attachments": [{"name": "Old"}]}
    assert ids(search([paper], "old", "all")) == ["x"]
    paper = {**paper, "attachments": [{"name": "New"}]}  # same date_modified (it has one-second steps)
    assert ids(search([paper], "old", "all")) == []
    assert ids(search([paper], "new", "all")) == ["x"]


def test_a_long_query_is_parsed_in_linear_time():
    start = time.monotonic()
    assert parse_query("A" * 200_000)
    assert parse_query("A:" * 100_000)
    assert time.monotonic() - start < 2
    assert parse_query("x" * 100 + ":v") == [("x" * 100 + ":v", False, None)]


def test_year_ranges_may_be_open():
    papers = [{"id": str(y), "title": "t", "year": y, "attachments": []} for y in (1999, 2010, 2024)]
    ids = lambda q: [p["id"] for p in search(papers, q)[0]]  # noqa: E731
    assert ids("year:2010-") == ["2010", "2024"]
    assert ids("year:-2010") == ["1999", "2010"]
    assert ids("year:2000-2020") == ["2010"]


def test_added_filter():
    papers = [
        {"id": "a", "title": "t", "date_added": "2026-10-11T11:44:12Z", "attachments": []},
        {"id": "b", "title": "t", "date_added": "2026-11-02T08:00:00Z", "attachments": []},
        {"id": "c", "title": "t", "date_added": "", "attachments": []},
    ]
    ids = lambda q: [p["id"] for p in search(papers, q)[0]]  # noqa: E731
    assert ids("added:2026-10") == ["a"]
    assert ids("added:2026") == ["a", "b"]
    assert ids("added:>2026-10") == ["b"]  # a partial date stands for its whole period
    assert ids("added:>=2026-10-12") == ["b"]
    assert ids("added:<2026-11-01") == ["a"]
    assert ids("added:2026-10-01..2026-10-31") == ["a"]
    assert ids("-added:2026-10") == ["b", "c"]
    assert ids("added:garbage") == []
    from datetime import datetime, timezone

    from backend.library.search import _added_matches

    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    assert _added_matches("2026-11-02", "14d", now) and not _added_matches("2026-10-11", "2w", now)


def test_unknown_filters_are_reported():
    from backend.library.search import unknown_filters

    assert unknown_filters('sort:added tag:x "a b" arXiv:2205.03415') == ["arXiv:", "sort:"]
    assert unknown_filters("citations:>100", extra_keys=["citations"]) == []


def test_captions_start_at_the_number_even_when_the_title_touches_it():
    from backend.pdf.regions import CAPTION

    for line, label in (
        ("Figure 14Luttinger parameter. Thermodynamic limit", "14"),
        ("Figure 3Tunable-range interaction", "3"),
        ("FIG. 3. Control of the dynamics", "3"),
        ("Figure 3 | Two-body correlations", "3"),
        ("Table II. Parameters", "II"),
    ):
        assert CAPTION.match(line).group(2) == label, line
    for line in ("Fig.19(a). These observations", "Fig. 27a). Therefore", "Fig.11(Koziol et al., 2021)"):
        assert not CAPTION.match(line), line  # a reference in running text, not a caption
