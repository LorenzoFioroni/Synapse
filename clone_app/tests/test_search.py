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
    assert parse_query('a "b c" -d') == [("a", False), ("b c", False), ("d", True)]


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
