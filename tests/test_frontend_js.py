"""Runs the JavaScript unit tests (tests/js/*.test.mjs) with Node on the built frontend (frontend/js)."""

import subprocess
from pathlib import Path

JS = sorted(str(p) for p in (Path(__file__).parent / "js").glob("*.test.mjs"))


def test_js_units(built_frontend):
    r = subprocess.run(["node", "--test", *JS], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
