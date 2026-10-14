"""The display scanner (frontend/src/views/notes/mdscan.ts) must agree with the backend parser.

The backend decides what a note links to; the frontend only uses its port to draw the preview.
This test runs both on every fixture and on random documents and requires identical results.
"""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.notes import frontmatter, parser

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures"
FIELDS = ("kind", "target", "line", "heading", "alias", "locator", "page", "prefix", "suppress_author", "group")

pytestmark = [
    pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed"),
    pytest.mark.usefixtures("built_frontend"),  # scan_dump.mjs runs the built frontend/js
]


def js_scan(texts):
    r = subprocess.run(
        ["node", str(ROOT / "tests/js/scan_dump.mjs")],
        input=json.dumps(texts),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def py_scan(text):
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    _, body, _ = frontmatter.split(t)
    p = parser.parse(text)
    inline_tags = []
    for ml in parser.mask(body).split("\n"):
        for m in parser._TAG.finditer(ml):
            tag = m.group(1).rstrip("/-")
            if tag and tag not in inline_tags:
                inline_tags.append(tag)
    return {
        "masked": parser.mask(body),
        "links": [{k: getattr(l, k) for k in FIELDS} for l in p.links],
        "headings": [[h.level, h.text, h.line] for h in p.headings],
        "tags": inline_tags,
    }


def compare(texts):
    for text, js in zip(texts, js_scan(texts)):
        py = py_scan(text)
        assert js["masked"] == py["masked"], repr(text)
        assert js["links"] == py["links"], repr(text)
        assert js["headings"] == py["headings"], repr(text)
        assert js["tags"] == py["tags"], repr(text)


def test_fixtures_agree():
    compare([p.read_bytes().decode("utf-8") for p in sorted(FIX.glob("*.md"))])


def test_random_documents_agree():
    rnd = random.Random(42)
    pieces = [
        "a",
        "b",
        " ",
        "  ",
        "\n",
        "\n\n",
        "\t",
        "@",
        "#",
        "#tag",
        "$",
        "$$",
        "`",
        "```",
        "~~~",
        "[",
        "]",
        "[[",
        "]]",
        "[@",
        "(",
        ")",
        "!",
        "|",
        "\\",
        "-",
        ";",
        ",",
        "p. 3",
        "<!--",
        "-->",
        "---\n",
        "é",
        "Ünï",
        "key2021",
        "[@k1]",
        "[[Note|x]]",
        "![a](b.png)",
        "$x$",
        "$5",
        "# H",
        "> ",
        "- ",
        "1. ",
        "```py\n",
        "\n```\n",
    ]
    texts = ["".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 60))) for _ in range(1500)]
    compare(texts)
