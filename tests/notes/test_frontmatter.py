from backend.notes import frontmatter as fm


def test_parse_subset():
    text = (
        '---\nid: 01ABC\ntitle: "A: quoted"\ntags: [a, "b c"]\nproject:\n  - p1\n  - p2\n'
        "# a comment\nnested:\n  key: value\nempty:\n---\nbody"
    )
    d = fm.read(text)
    assert d["id"] == "01ABC"
    assert d["title"] == "A: quoted"
    assert d["tags"] == ["a", "b c"]
    assert d["project"] == ["p1", "p2"]
    assert d["nested"] == "key: value" or "key: value" in d["nested"]
    assert d["empty"] == ""


def test_no_frontmatter():
    assert fm.split("hello")[0] is None
    assert fm.read("hello") == {}
    assert fm.split("---\nunterminated")[0] is None


def test_set_fields_touches_only_given_keys():
    text = "---\n# keep me\nid: 1\ncustom:   odd   spacing\ntags: [x]\n---\nBody\n---\nnot fm\n"
    out = fm.set_fields(text, {"tags": ["x", "y"], "updated": "2026-10-09T09:00:00+02:00"})
    assert out == (
        "---\n# keep me\nid: 1\ncustom:   odd   spacing\ntags: [x, y]\n"
        "updated: 2026-10-09T09:00:00+02:00\n---\nBody\n---\nnot fm\n"
    )
    assert fm.set_fields(out, {"custom": None}).count("custom") == 0


def test_set_fields_adds_header_and_keeps_crlf():
    assert fm.set_fields("Body", {"title": "T"}) == "---\ntitle: T\n---\nBody"
    out = fm.set_fields("---\r\nid: 1\r\n---\r\nBody\r\n", {"title": "T"})
    assert out == "---\r\nid: 1\r\ntitle: T\r\n---\r\nBody\r\n"


def test_round_trip_of_values():
    for v in ["plain", "with: colon", 'quote "q"', "back\\slash", "#hash", "", " pad "]:
        out = fm.set_fields("x", {"k": v})
        assert fm.read(out)["k"] == v, v
    out = fm.set_fields("x", {"k": ["a,b", "c"]})
    assert fm.read(out)["k"] == ["a,b", "c"]
    out = fm.set_fields("x", {"k": ["", "x y", "[b]"]})
    assert fm.read(out)["k"] == ["", "x y", "[b]"]
