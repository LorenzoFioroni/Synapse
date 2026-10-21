"""Synapse must run fully offline: the pages may link to the web (a DOI opens doi.org when clicked),
but must never *load* code, styles, fonts or images from it. Libraries are vendored under
frontend/vendor/ instead (see frontend/vendor/README.md)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

REMOTE = r"""["'`]?(?:https?:)?//"""  # http://, https:// or protocol-relative //
LOADS = {
    ".html": [
        rf"<(?:script|img|iframe|audio|video|source|embed)\b[^>]*\bsrc\s*=\s*{REMOTE}",
        rf"<link\b[^>]*\bhref\s*=\s*{REMOTE}",
    ],
    ".css": [
        rf"url\(\s*{REMOTE}",
        rf"@import\s+{REMOTE}",
    ],
    ".js": [
        rf"\bimport\s*(?:[\w{{}}*,\s]+\s+from\s*)?\(?\s*{REMOTE}",  # import x from '...', import('...')
        rf"\bimportScripts\(\s*{REMOTE}",
        rf"\bnew\s+(?:Shared)?Worker\(\s*{REMOTE}",
        rf"\.src\s*=\s*{REMOTE}",
    ],
}
LOADS[".ts"] = LOADS[".js"]
# not ours, or generated from what is checked here (frontend/js is built from frontend/src)
SKIP = {"vendor", "node_modules", "js"}


def own_files():
    for path in sorted(FRONTEND.rglob("*")):
        if path.suffix in LOADS and not SKIP & set(path.relative_to(FRONTEND).parts):
            yield path
    # vendored pages and stylesheets still must not reach out (their scripts are checked when vendoring)
    for path in sorted((FRONTEND / "vendor").rglob("*")):
        if path.suffix in (".html", ".css"):
            yield path


def test_frontend_loads_nothing_from_the_network():
    found = []
    for path in own_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in LOADS[path.suffix]:
            for m in re.finditer(pattern, text, flags=re.IGNORECASE):
                line = text.count("\n", 0, m.start()) + 1
                found.append(f"{path.relative_to(ROOT)}:{line}: {m.group(0)[:80]}")
    assert not found, "remote resources (vendor them under frontend/vendor/ instead):\n" + "\n".join(found)


def test_the_check_catches_remote_loads(tmp_path):
    samples = {
        ".html": '<script src="https://cdn.example.com/x.js"></script>',
        ".css": "@font-face { src: url(https://fonts.example.com/f.woff2) }",
        ".js": "import { a } from 'https://esm.example.com/a.js'",
    }
    for suffix, text in samples.items():
        assert any(re.search(p, text, flags=re.IGNORECASE) for p in LOADS[suffix]), suffix
    # a link the user clicks is fine
    assert not any(re.search(p, "a.href = `https://doi.org/${doi}`") for p in LOADS[".js"])


def test_vendored_modules_and_wasm_are_served_with_their_types(client):
    # browsers refuse to run an ES module sent with a non-JavaScript type (pdf.js is one)
    for path, mimetype in [
        ("/vendor/pdfjs/pdf.min.mjs", "text/javascript"),
        ("/vendor/pdfjs/pdf.worker.min.mjs", "text/javascript"),
        ("/vendor/pdfjs/wasm/openjpeg.wasm", "application/wasm"),
    ]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.mimetype == mimetype, path
        r.close()
