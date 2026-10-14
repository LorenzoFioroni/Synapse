"""When the server builds frontend/js from frontend/src (backend/frontend.py)."""

import os
import time

import pytest

from backend import frontend as F


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export {};\n")
    return tmp_path


def built(tree, age: float = 0):
    (tree / "js").mkdir(exist_ok=True)
    (tree / "js" / "main.js").write_text("export {};\n")
    stamp = tree / "js" / ".build-stamp"
    stamp.write_text("x")
    t = time.time() - age
    os.utime(stamp, (t, t))


def test_needs_build(tree):
    assert F.needs_build(tree)  # never built
    built(tree)
    assert not F.needs_build(tree)
    built(tree, age=60)  # a source changed after the last build
    assert F.needs_build(tree)


def test_missing_build_without_tools_says_what_to_do(tree, monkeypatch):
    monkeypatch.setattr(F.shutil, "which", lambda name: None)
    with pytest.raises(F.BuildError, match="npm ci && npm run build"):
        F.ensure_built(tree)


def test_stale_build_without_tools_is_served_with_a_warning(tree, monkeypatch, caplog):
    built(tree, age=60)
    monkeypatch.setattr(F.shutil, "which", lambda name: None)
    assert F.ensure_built(tree) is False
    assert "old build is served" in caplog.text


def test_builds_when_the_tools_are_there(tree, monkeypatch):
    (tree / "node_modules" / "esbuild").mkdir(parents=True)
    monkeypatch.setattr(F.shutil, "which", lambda name: "/usr/bin/node")
    calls = []

    def run(cmd, cwd, **kw):
        calls.append((cmd, cwd))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(F.subprocess, "run", run)
    assert F.ensure_built(tree) is True
    assert calls == [(["/usr/bin/node", "build.mjs"], tree)]
