"""Reading ```gallery blocks (backend/notes/gallery.py), as plugins/extended-markdown/frontend/figures/gallery.ts does."""

from backend.notes import gallery


def test_panels_captions_and_width():
    g = gallery.parse_block(
        [
            "Shown first.",
            "![first|300](attachments/a.png)",
            "*Clean system, $x$.*",
            "![](b.png) With speckle",
            "and a second line.",
            "",
            "The gallery caption [@k].",
            "![](e.png) ![](f.png) Two at once.",
        ],
        "gallery|600",
    )
    assert g["width"] == 600
    assert g["caption"] == "Shown first.\nThe gallery caption [@k]."
    assert [(p["src"], p["alt"], p["caption"], p["offset"]) for p in g["panels"]] == [
        ("attachments/a.png", "first", "Clean system, $x$.", 1),
        ("b.png", "", "With speckle\nand a second line.", 3),
        ("e.png", "", "", 7),
        ("f.png", "", "Two at once.", 7),
    ]
    assert gallery.parse_block(["![](a.png)", "*one* and *two*"])["panels"][0]["caption"] == "*one* and *two*"
    assert gallery.parse_block([], "gallery width=300")["width"] == 300


def test_blocks_of_a_note_with_lines():
    text = "---\ntitle: x\n---\n# T\n\n```gallery\n![](a.png)\n![](b.png)\n```\n\n```\n```gallery\n![](no.png)\n```\n"
    gs = gallery.galleries(text)
    assert len(gs) == 1  # a ```gallery line inside a code block is code
    assert gs[0]["line"] == 6 and [(p["line"], p["label"]) for p in gs[0]["panels"]] == [(7, "a"), (8, "b")]
    assert [gallery.panel_label(i) for i in (0, 25, 26)] == ["a", "z", "27"]
