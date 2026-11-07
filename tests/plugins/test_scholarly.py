"""The public scholarly helpers preserve Synapse's offline identifier and name conventions."""

import time

import pytest

from backend.plugins import scholarly


@pytest.mark.parametrize(
    "value,clean,normalized",
    [
        (None, "", None),
        ("  DOI:10.1234/ABC  ", "10.1234/ABC", "10.1234/abc"),
        ("https://dx.doi.org/10.1234/AbC.", "10.1234/AbC.", "10.1234/abc"),
    ],
)
def test_doi_cleaning_and_matching(value, clean, normalized):
    assert scholarly.clean_doi(value) == clean
    assert scholarly.norm_doi(value) == normalized


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), ("https://arxiv.org/pdf/2101.00001v3", "2101.00001"), ("arXiv:hep-TH/9901001v2", "hep-th/9901001")],
)
def test_arxiv_identifiers(value, expected):
    assert scholarly.norm_arxiv(value) == expected


def test_arxiv_identifiers_are_found_in_linear_time():
    start = time.monotonic()
    assert scholarly.norm_arxiv("-" * 200_000) is None
    scholarly.norm_arxiv("-" * 200_000 + "x")
    assert time.monotonic() - start < 2
    assert scholarly.norm_arxiv("see cond-mat/0501001") == "cond-mat/0501001"


def test_arxiv_record_and_pattern():
    assert scholarly.arxiv_id_of({"doi": "10.48550/arXiv.2101.00001"}) == "2101.00001"
    assert scholarly.arxiv_id_of({"title": "No identifier"}) is None
    match = scholarly.ARXIV_ID.search("https://arxiv.org/abs/hep-th/9901001v2")
    assert match and match.group(1) == "hep-th/9901001"


def test_author_pairs_preserve_particles_accents_and_order():
    pairs = [
        scholarly.parse_display_name(name) for name in ["John von Neumann", "Bañuls, Mari Carmen", "Collaboration"]
    ]
    assert pairs == [("von Neumann", "John"), ("Bañuls", "Mari Carmen"), ("Collaboration", "")]
    text = scholarly.join_authors(iter(pairs))
    assert text == "von Neumann, John; Bañuls, Mari Carmen; Collaboration"
    assert scholarly.split_authors(text) == pairs
    assert scholarly.parse_display_name("") == ("", "")
    assert scholarly.split_authors(None) == []


def test_title_similarity_and_plain_abstracts():
    assert scholarly.title_similarity("Étude: a quantum model", "etude a quantum model") == 1.0
    assert scholarly.title_similarity("", "A quantum model") == 0.0
    assert scholarly.title_similarity("A quantum model", "The quantum model") >= scholarly.TITLE_MATCH
    assert scholarly.title_similarity("Quantum transport", "Medieval agriculture") < scholarly.TITLE_MATCH
    assert scholarly.TITLE_MATCH == 0.8
    assert scholarly.strip_tags("<jats:title>Abstract</jats:title><p>A <b>quantum</b> model.</p>") == "A quantum model."
    assert scholarly.strip_tags(None) == ""
