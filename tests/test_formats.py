from backend import formats

BIB = r"""
@string{prl = "Physical Review Letters"}
% a comment
@Article{cuiVariational2015,
  title     = {Variational Matrix Product Operators for the Steady State of {Dissipative} Quantum Systems},
  author    = {Cui, Jian and Cirac, J. Ignacio and Ba{\~n}uls, Mari Carmen},
  journal   = prl,
  year      = 2015,
  volume    = {114},
  number    = {22},
  pages     = {220601--220605},
  doi       = {10.1103/PhysRevLett.114.220601},
  keywords  = {tensor networks, open systems},
}
@misc{ljubotina2023,
  title = "Superdiffusive Energy Transport in {K}inetically Constrained Models",
  author = {Marko Ljubotina and Jean-Yves Desaules and Zlatko Papi\'{c}},
  year = {2023}, eprint = {2205.03415}, archivePrefix = {arXiv}
}
@book{whoReport, title={Report}, author={{World Health Organization}}, publisher={WHO}, year={2020}}
"""


def test_bibtex_parse():
    fmt, items = formats.parse(BIB)
    assert fmt == "bibtex" and len(items) == 3
    a = items[0]
    assert a["citation_key"] == "cuiVariational2015"
    assert a["authors"] == "Cui, Jian; Cirac, J. Ignacio; Bañuls, Mari Carmen"
    assert a["journal"] == "Physical Review Letters"
    assert a["title"].endswith("of Dissipative Quantum Systems")
    assert a["pages"] == "220601-220605" and a["issue"] == "22" and a["year"] == "2015"
    assert a["keywords"] == "tensor networks; open systems"
    b = items[1]
    assert b["item_type"] == "preprint" and b["archive_id"] == "arXiv:2205.03415"
    assert b["authors"] == "Ljubotina, Marko; Desaules, Jean-Yves; Papić, Zlatko"
    assert items[2]["authors"] == "World Health Organization"


def test_bibtex_roundtrip_ascii_and_unicode():
    _, items = formats.parse(BIB)
    for uni in (True, False):
        out = formats.dump(items, "bibtex", unicode=uni)
        if not uni:
            assert r"Ba{\~{n}}uls" in out
        _, again = formats.parse(out)
        assert [i["authors"] for i in again] == [i["authors"] for i in items]
        assert [i["title"] for i in again] == [i["title"] for i in items]
        assert again[1]["archive_id"] == "arXiv:2205.03415"


def test_bibtex_broken_entry_is_skipped():
    _, items = formats.parse("@article{bad, title = {unclosed\n@article{good, title={Fine}}")
    assert [i.get("citation_key") for i in items][-1] == "good"


ITEM = {
    "item_type": "journalArticle",
    "title": "Asymmetry in energy versus spin transport",
    "authors": "Mendoza-Arenas, Juan José; Žnidarič, Marko",
    "year": 2019,
    "journal": "Physical Review B",
    "volume": "99",
    "issue": "9",
    "pages": "094435-094440",
    "doi": "10.1103/PhysRevB.99.094435",
    "abstract": "We study & compare 50% of things.",
    "keywords": "transport; disorder",
    "citation_key": "mendozaarenasAsymmetry2019",
    "url": "https://example.org/x",
    "issn": "2469-9950",
}

KEYS = [
    "item_type",
    "title",
    "authors",
    "journal",
    "volume",
    "issue",
    "pages",
    "doi",
    "abstract",
    "keywords",
    "citation_key",
    "url",
]


def _check(fmt):
    text = formats.dump([ITEM], fmt)
    detected, items = formats.parse(text)
    assert detected == fmt
    got = items[0]
    for k in KEYS:
        assert str(got.get(k)) == str(ITEM[k]), (fmt, k, got.get(k))
    assert str(got["year"]) == "2019"


def test_ris_roundtrip():
    _check("ris")


def test_endnote_roundtrip():
    _check("endnote")


def test_endnote_xml_roundtrip():
    _check("endnote_xml")


def test_csljson_roundtrip():
    _check("csljson")


def test_ris_from_other_tools():
    ris = """TY  - JOUR
AU  - Cirac, J. Ignacio
A1  - Zoller, Peter
TI  - Quantum computations
  with cold trapped ions
JO  - Phys. Rev. Lett.
PY  - 1995/05/15/
SP  - 4091
EP  - 4094
SN  - 0031-9007
KW  - ions
KW  - quantum computing
ER  - 
"""
    _, items = formats.parse(ris)
    it = items[0]
    assert it["title"] == "Quantum computations with cold trapped ions"
    assert it["authors"] == "Cirac, J. Ignacio; Zoller, Peter"
    assert it["year"] == "1995" and it["pages"] == "4091-4094" and it["issn"] == "0031-9007"
    assert it["keywords"] == "ions; quantum computing"


def test_detect_unknown():
    import pytest

    with pytest.raises(formats.FormatError):
        formats.parse("just some text")
