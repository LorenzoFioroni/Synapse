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


def test_shell_lists_what_the_app_loads_and_changes_with_it(tree):
    for name, text in {
        "viewer.html": "<html>",
        "index.html": "<html>",
        "js/main.js": "x",
        "js/main.js.map": "{}",
        "css/base.css": "a{}",
        "vendor/pdfjs/worker.mjs": "w",
        "vendor/README.md": "# x",
    }.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(text)
    shell = F.shell_files(tree)
    assert shell["files"] == ["/", "/viewer.html", "/js/main.js", "/css/base.css", "/vendor/pdfjs/worker.mjs"]
    assert F.shell_files(tree)["version"] == shell["version"]
    (tree / "js" / "main.js").write_text("changed")
    assert F.shell_files(tree)["version"] != shell["version"]


def test_shell_is_served(client):
    got = client.get("/api/offline/shell").get_json()
    assert "/" in got["files"] and "/viewer.html" in got["files"] and got["version"]
