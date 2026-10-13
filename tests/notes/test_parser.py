"""Parser golden tests: each fixtures/<name>.md has a hand-written fixtures/<name>.json.

Only the fields listed in the expectation are compared, so expectations stay readable.
"""
import json
from pathlib import Path

import pytest

from backend.notes import parser

FIX = Path(__file__).parent / "fixtures"
CASES = sorted(p.stem for p in FIX.glob("*.md"))


@pytest.mark.parametrize("name", CASES)
def test_golden(name):
    text = (FIX / f"{name}.md").read_bytes().decode("utf-8")
    exp = json.loads((FIX / f"{name}.json").read_text("utf-8"))
    got = parser.parse(text)
    assert got.title == exp["title"]
    assert sorted(got.tags) == sorted(exp["tags"])
    assert [[h.level, h.text, h.line] for h in got.headings] == exp["headings"]
    links = [l.to_dict() for l in got.links]
    assert len(links) == len(exp["links"]), [(l["kind"], l["target"]) for l in links]
    for g, e in zip(links, exp["links"]):
        assert {k: g[k] for k in e} == e


def test_mask_keeps_length_and_lines():
    body = "a `[@x]` b\n```\n[@y]\n```\n$m$ [@z]"
    m = parser.mask(body)
    assert len(m) == len(body)
    assert m.count("\n") == body.count("\n")
    assert "[@x]" not in m and "[@y]" not in m and "[@z]" in m


def test_unclosed_fence_hides_the_rest():
    # Same behaviour as the renderer: an unclosed fence runs to the end of the note.
    got = parser.parse("[@a]\n```\n[@b]\n")
    assert [l.target for l in got.citations] == ["a"]


def test_context_is_the_line_and_long_lines_are_trimmed():
    got = parser.parse("intro\n\nThe result [@k] holds.")
    assert got.links[0].context == "The result [@k] holds."
    long = "word " * 200 + "[@k] " + "tail " * 200
    ctx = parser.parse(long).links[0].context
    assert "[@k]" in ctx and len(ctx) <= parser.CONTEXT_CHARS + 2


def test_frontmatter_lines_are_skipped_and_counted():
    got = parser.parse("---\ntitle: T\nnote: '[@not]'\n---\n\n[@yes]")
    assert [l.target for l in got.citations] == ["yes"]
    assert got.citations[0].line == 6
    assert got.meta["note"] == "[@not]"


def test_fallback_title():
    assert parser.parse("no heading", fallback_title="file").title == "file"


def test_fuzz_never_crashes_and_mask_is_aligned():
    import random
    rnd = random.Random(7)
    alphabet = list("ab @#$`~[]()!|\\-*>:;,.\n\t") + ["[[", "]]", "[@", "```", "$$", "<!--", "-->", "---\n", "é"]
    for _ in range(2000):
        s = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 80)))
        m = parser.mask(s.replace("\r\n", "\n"))
        assert len(m) == len(s.replace("\r\n", "\n"))
        p = parser.parse(s)
        for l in p.links:
            assert l.line >= 1 and l.target is not None


def test_attachment_qualifier_names_the_pdf():
    # [@key, p. 4, att:12]: the page and the attachment id are both read; without att: there is none.
    assert parser.attachment_of("p. 4, att:12") == 12
    assert parser.attachment_of("att:7") == 7
    assert parser.attachment_of("ATT:9, p. 2") == 9
    assert parser.attachment_of("p. 4") is None
    assert parser.attachment_of("p. 4, attx:3") is None   # not a qualifier
    assert parser.attachment_of("") is None and parser.attachment_of(None) is None
    cites = parser._parse_cite_group("@smith2021, p. 4, att:12")
    assert cites[0]["key"] == "smith2021" and cites[0]["page"] == 4
    assert parser.attachment_of(cites[0]["locator"]) == 12
