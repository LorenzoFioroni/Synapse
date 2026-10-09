from backend.search import parse_query, search

PAPERS = [
    {"id": "1", "title": "Variational Matrix Product Operators", "authors": "Bañuls, Mari Carmen",
     "abstract": "steady state", "journal": "PRL", "doi": "10.1/abc", "attachments": []},
    {"id": "2", "title": "Superdiffusive Energy Transport", "authors": "Ljubotina, Marko",
     "abstract": "", "journal": "Physical Review X", "attachments": [{"name": "supplement.pdf"}]},
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
    {"id": "a", "title": "Spin transport", "authors": "Doe, J", "year": 2019, "item_type": "journalArticle",
     "tags": ["Transport", "open systems"], "folder_ids": [2, 3], "attachments": [{"name": "x.pdf"}], "doi": "10.1/a"},
    {"id": "b", "title": "Open quantum systems", "authors": "Breuer, H", "year": 2002, "item_type": "book",
     "tags": [], "folder_ids": [], "attachments": []},
    {"id": "c", "title": "Energy transport", "authors": "Roe, R", "year": 2023, "item_type": "journalArticle",
     "tags": ["transport"], "folder_ids": [1], "attachments": []},
]
CTX = {"folder_paths": {1: ["Physics"], 2: ["Physics", "Transport"], 3: ["Reading group"]}, "type_labels": {"book": "Book", "journalArticle": "Journal Article"}}


def f(q, mode="basic"):
    return [p["id"] for p in search(ORG, q, mode, None, CTX)[0]]


def test_filters():
    assert f("tag:transport") == ["a", "c"]          # case-insensitive
    assert f('tag:"open systems"') == ["a"]
    assert f("tag:none") == ["b"]
    assert f("-tag:transport") == ["b"]
    assert f("folder:physics") == ["a", "c"]         # any folder on the path
    assert f('folder:"Physics/Transport"') == ["a"]
    assert f("folder:none") == ["b"]
    assert f('folder:"reading group"') == ["a"]       # any of the item's folders
    assert f("has:folder") == ["a", "c"]
    assert f("type:book") == ["b"] and f("type:journal") == ["a", "c"]
    assert f("year:2019") == ["a"] and f("year:2000-2020") == ["a", "b"] and f("year:>2010") == ["a", "c"]
    assert f("has:pdf") == ["a"] and f("has:doi") == ["a"] and f("-has:pdf") == ["b", "c"]
    assert f("author:roe") == ["c"]


def test_filters_combine_with_text():
    assert f("transport year:>2020") == ["c"]
    assert f("tag:transport -energy") == ["a"]
    assert f("arXiv:2205.03415") == []                # unknown prefix = plain text, no crash
