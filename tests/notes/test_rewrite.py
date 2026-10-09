from pathlib import Path

from backend.notes import parser

FIX = Path(__file__).parent / "fixtures"


def test_spans_point_at_exactly_the_targets_in_every_fixture():
    for f in sorted(FIX.glob("*.md")):
        text = f.read_bytes().decode("utf-8")      # CRLF kept
        spans = parser.link_spans(text)
        parsed = [l for l in parser.parse(text).links if l.kind in ("cite", "note", "embed")]
        for s in spans:
            assert text[s["start"]:s["end"]] == s["target"], (f.name, s)
        # same multiset of (kind, target) as the parser
        assert sorted((s["kind"], s["target"]) for s in spans) == sorted((l.kind, l.target) for l in parsed), f.name


def test_tricky_positions():
    text = "---\r\ntitle: x\r\n---\r\nA [see  @smith2021, p. 3;  -@doe2020] and [[ Folder/Note #H| alias ]] and [[a\\|b]]\r\n`[@code]`"
    spans = parser.link_spans(text)
    assert [(s["kind"], s["target"]) for s in spans] == [("cite", "smith2021"), ("cite", "doe2020"),
                                                        ("note", "Folder/Note"), ("note", "a")]
    for s in spans:
        assert text[s["start"]:s["end"]] == s["target"]


def test_replace_spans_only_touches_targets():
    text = "[@k1] [@k10] `[@k1]` k1 [[k1]] $[@k1]$ [@k1; @k1a]"
    edits = [(s["start"], s["end"], "new") for s in parser.link_spans(text) if s["target"] == "k1"]
    assert parser.replace_spans(text, edits) == "[@new] [@k10] `[@k1]` k1 [[new]] $[@k1]$ [@new; @k1a]"


def test_spans_on_random_documents_point_at_targets():
    import random
    rnd = random.Random(3)
    pieces = ["a", " ", "\n", "\r\n", "[", "]", "[[", "]]", "@", "k1", ";", ",", "p. 3", "#", "|", "\\|", "`", "$", "-", "(", "é"]
    for _ in range(3000):
        text = "".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 40)))
        parsed = sorted((l.kind, l.target) for l in parser.parse(text).links if l.kind in ("cite", "note", "embed") and l.target)
        spans = parser.link_spans(text)
        for s in spans:
            assert text[s["start"]:s["end"]] == s["target"], (text, s)
        # parse() normalises CRLF first; compare only when that changes nothing
        if "\r" not in text:
            assert sorted((s["kind"], s["target"]) for s in spans) == parsed, text
