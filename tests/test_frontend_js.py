"""Runs the JavaScript unit tests (tests/js/*.test.mjs) with Node, if Node is installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

JS = sorted(str(p) for p in (Path(__file__).parent / "js").glob("*.test.mjs"))


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_js_units():
    r = subprocess.run(["node", "--test", *JS], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
