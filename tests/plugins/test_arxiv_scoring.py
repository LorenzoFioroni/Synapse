"""The arXiv Today match (plugins/arxiv-today/backend/scoring.py) on its own: specific links beat
many generic ones, current interest raises a link, namesakes do not match, a paper is never
compared with itself, rare shared references count, a topic counts only where it stands out from
its day; and the comparison report."""

import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

scoring = importlib.import_module("plugins.arxiv-today.backend.scoring")
report = importlib.import_module("plugins.arxiv-today.backend.report")

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=800)).isoformat()


def item(pid, title, *, doi=None, arxiv=None, authors="Someone, Sam", key=None, abstract="", added=OLD):
    return {
        "id": pid,
        "title": title,
        "doi": doi,
        "archive_id": f"arXiv:{arxiv}" if arxiv else None,
        "authors": authors,
        "citation_key": key or pid,
        "abstract": abstract,
        "date_added": added,
    }


LIBRARY = [
    item("textbook", "Quantum computation and quantum information textbook", doi="10.1/nc"),
    item("review", "Noisy intermediate scale quantum computing review", doi="10.1/nisq"),
    item("lindblad", "On the generators of quantum dynamical semigroups", doi="10.1/lindblad"),
    item("niche", "Variational matrix product operators for dissipative steady states", doi="10.1/vmpo"),
    item("mine", "Steady states of driven dissipative cavities", doi="10.1/mine", authors="Fioroni, Lorenzo"),
    item("wang", "Spin liquids on the kagome lattice", doi="10.1/kagome", authors="Wang, Yi"),
]
# 1000 announced papers: the textbook, the review and Lindblad are cited by hundreds, the niche paper by 2
BACKGROUND = scoring.Background(
    papers=1000,
    cites={"doi:10.1/nc": 420, "doi:10.1/nisq": 380, "doi:10.1/lindblad": 300, "doi:10.1/vmpo": 2},
    authored=1000,
    authors={"wang|y": 60, "wang|yi": 25, "hauke|p": 3, "hauke|philipp": 3},
)


def lib(**kw):
    return scoring.build_library(LIBRARY, background=BACKGROUND, now=NOW, **kw)


def paper(key, *, title="A paper", abstract="", authors=("Ann Author",), kind="new", day="2026-11-04"):
    return {
        "key": key,
        "arxiv": key,
        "title": title,
        "abstract": abstract,
        "authors": list(authors),
        "kind": kind,
        "day": day,
    }


def run(papers, cites, library=None, refs=None):
    """``cites``: {paper key: [library ids it cites]}."""
    refs = refs or {k: [{"doi": f"10.9/{k}/{i}"} for i in range(len(v))] for k, v in cites.items()}
    matched = {k: list(cites.get(k, [])) + [None] * (len(refs[k]) - len(cites.get(k, []))) for k in refs}
    return scoring.score(papers, library or lib(), BACKGROUND, refs=refs, matched=matched)


def test_one_specific_citation_beats_three_generic_ones():
    generic, specific = run(
        [paper("generic"), paper("specific")],
        {"generic": ["textbook", "review", "lindblad"], "specific": ["niche"]},
    )
    assert specific["score"] > generic["score"]
    assert generic["tier"] == "rest"
    assert generic["reasons"][0]["text"] == "Cites 3 widely cited papers in your library · counts little"
    assert (
        specific["reasons"][0]["text"] == "Cites “Variational matrix product operators for dissipative steady states”"
    )


def test_notes_on_a_paper_raise_its_citations():
    links = [{"source": "Notes/steady.md", "kind": "cite", "target": "niche"}]
    notes = [{"path": "Notes/steady.md", "mtime": (NOW - timedelta(days=3)).timestamp()}]
    plain = run([paper("p")], {"p": ["niche"]})[0]
    noted = run([paper("p")], {"p": ["niche"]}, library=lib(notes=notes, links=links))[0]
    assert noted["score"] > plain["score"]
    assert "your notes cite it lately" in noted["reasons"][0]["text"]
    assert noted["tier"] == "skim"  # one link, however good, is not enough for Read on its own


def test_citing_your_own_paper_is_read():
    (res,) = run([paper("p")], {"p": ["mine"]}, library=lib(author_names=["Lorenzo Fioroni"]))
    assert res["tier"] == "read"
    assert res["reasons"][0] == {"kind": "cites_own", "text": "Cites 1 paper you authored", "items": ["mine"]}


@pytest.mark.parametrize(
    ("author", "matches"),
    [
        ("Yi Wang", True),  # the same full name
        ("Y. Wang", True),  # initials only: may be the same person
        ("Yu Wang", False),  # both first names known, and different
        ("Jun Wang", False),
        ("Wang Yi", False),  # "Given Family" order: family name Yi
    ],
)
def test_names_match_on_full_first_names(author, matches):
    (res,) = run([paper("p", authors=[author])], {})
    assert any(r["kind"] == "authors" for r in res["reasons"]) is matches


def test_a_common_name_counts_less_than_a_rare_one():
    library = scoring.build_library(
        [*LIBRARY, item("hauke", "Yukawa SYK in a cavity", doi="10.1/syk", authors="Hauke, Philipp")],
        background=BACKGROUND,
        now=NOW,
    )
    common, rare = run([paper("a", authors=["Y. Wang"]), paper("b", authors=["Philipp Hauke"])], {}, library=library)
    assert rare["features"]["people"] > 2 * common["features"]["people"]


def test_names_with_latex_accents():
    a, b = scoring.name("Daniel P\\'erez Garc\\'ia"), scoring.name("García, Daniel Pérez")
    assert a is not None and b is not None and a.same(b)


def test_a_library_paper_is_not_compared_with_itself():
    library = scoring.build_library(
        [*LIBRARY, item("self", "Adaptive qubit encoding for optimization", arxiv="2611.03987", authors="Ann, Author")],
        background=BACKGROUND,
        now=NOW,
    )
    p = paper("2611.03987", title="Adaptive qubit encoding for optimization", authors=["Author Ann"])
    (res,) = run([p], {"2611.03987": ["self"]}, library=library)
    assert res["reasons"] == [] and res["tier"] == "rest"


def test_rare_references_shared_with_the_library_count():
    graph = {
        "nodes": [{"id": "doi:10.5/rare", "doi": "10.5/rare"}, {"id": "doi:10.5/common", "doi": "10.5/common"}],
        "edges": [
            {"source": "item:niche", "target": "doi:10.5/rare"},
            {"source": "item:niche", "target": "doi:10.5/common"},
            {"source": "item:review", "target": "doi:10.5/common"},
        ],
    }
    bg = scoring.Background(papers=1000, cites={**BACKGROUND.cites, "doi:10.5/rare": 1, "doi:10.5/common": 500})
    library = scoring.build_library(LIBRARY, graph=graph, background=bg, now=NOW)
    papers = [paper("rare"), paper("common")]
    refs = {"rare": [{"doi": "10.5/rare"}, {"doi": "10.9/x"}], "common": [{"doi": "10.5/common"}, {"doi": "10.9/y"}]}
    matched = {"rare": [None, None], "common": [None, None]}
    rare, common = scoring.score(papers, library, bg, refs=refs, matched=matched)
    assert rare["features"]["shared"] > 3 * common["features"]["shared"]
    assert rare["reasons"][0]["kind"] == "shared" and rare["reasons"][0]["items"] == ["niche"]


def test_a_topic_counts_only_where_it_stands_out_from_its_day():
    library = scoring.build_library(
        [
            *LIBRARY,
            item("t1", "Dissipative phase transitions in driven cavities", abstract="Liouvillian gap cavity photons"),
            item("t2", "Liouvillian spectral gap of open quantum systems", abstract="dissipative gap Lindblad"),
        ],
        background=BACKGROUND,
        now=NOW,
    )
    filler = [
        paper(f"f{i}", title=f"Unrelated chemistry topic number {i}", abstract="molecules solvents") for i in range(8)
    ]
    close = paper(
        "close", title="Liouvillian gap closing in driven dissipative cavities", abstract="dissipative cavity gap"
    )
    results = run([*filler, close], {}, library=library)
    assert results[-1]["features"]["topic"] > 0 and results[-1]["reasons"][0]["kind"] == "similar"
    assert all(r["features"]["topic"] == 0 for r in results[:-1])


def test_cross_lists_start_a_little_lower():
    new, cross = run([paper("a"), paper("b", kind="cross")], {})
    assert cross["score"] < new["score"]


def test_rarity_is_shrunk_while_the_background_is_small():
    assert scoring.rarity(0, 0) == 0.5
    assert 0.49 < scoring.rarity(0, 3) < 0.52  # three papers say almost nothing
    assert scoring.rarity(0, 10000) > 0.9 > 0.3 > scoring.rarity(2000, 10000)


def test_ref_keys():
    assert scoring.ref_keys({"doi": "10.48550/arXiv.2610.00001"}) == {"arxiv:2610.00001"}
    assert scoring.ref_keys({"doi": "10.1/X", "arxiv_id": "2610.00001v2"}) == {"doi:10.1/x", "arxiv:2610.00001"}
    assert scoring.ref_keys({"title": "Quantum dynamical semigroups of N-level systems"}) == {
        "t:quantum dynamical semigroups of n level systems"
    }
    assert scoring.ref_keys({"title": "Too short"}) == set()


def test_the_report_compares_ranks():
    rows = [
        {
            "arxiv": "a",
            "day": "d",
            "title": "A",
            "score": 90,
            "score_v1": 1,
            "tier": "read",
            "dismissed": 0,
            "reasons": "[]",
        },
        {
            "arxiv": "b",
            "day": "d",
            "title": "B",
            "score": 10,
            "score_v1": 20,
            "tier": "rest",
            "dismissed": 1,
            "reasons": json.dumps([{"text": "x"}]),
        },
        {
            "arxiv": "c",
            "day": "d",
            "title": "C",
            "score": 50,
            "score_v1": 5,
            "tier": "skim",
            "dismissed": 0,
            "reasons": "[]",
        },
    ]
    r = report.compare(rows, {"a"}, top=1)
    assert r["papers"] == 3 and r["tiers"] == {"read": 1, "rest": 1, "skim": 1}
    assert r["added"]["median_rank_earlier"] == 3 and r["added"]["median_rank_match"] == 1
    assert r["dismissed"]["in_top_1_earlier"] == 1 and r["dismissed"]["in_top_1_match"] == 0
    assert r["rose_most"][0]["arxiv"] == "a" and r["fell_most"][0]["arxiv"] == "b"
