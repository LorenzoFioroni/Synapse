import json
import shutil
import textwrap
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "recently-added"


def write_plugin(root: Path, pid: str, code: str | None = None, **manifest) -> Path:
    """A plugin folder ``root/pid`` with a manifest (defaults filled in) and, given ``code``, a
    one-file backend."""
    folder = root / pid
    folder.mkdir(parents=True, exist_ok=True)
    data = {"id": pid, "name": pid.replace("-", " ").title(), "version": "0.1.0", "api": "^1.0", **manifest}
    if code is not None:
        (folder / "plugin.py").write_text(textwrap.dedent(code), "utf-8")
        data.setdefault("backend", "plugin.py")
    (folder / "manifest.json").write_text(json.dumps(data), "utf-8")
    return folder


@pytest.fixture(autouse=True)
def _no_bundled_plugins(monkeypatch, tmp_path):
    """The plugins that come with Synapse (plugins/) are left out, so each test sees only the ones it
    installs; test_citation_counts.py brings its own back."""
    empty = tmp_path / "no-bundled-plugins"
    empty.mkdir()
    from backend.config import Config

    monkeypatch.setattr(Config, "bundled_plugins_dir", property(lambda self: empty))


@pytest.fixture
def make_plugin(svc):
    """Write a plugin into the library's plugins folder: ``make_plugin("x", code, **manifest)``."""

    def make(pid: str, code: str | None = None, **manifest) -> Path:
        return write_plugin(svc.config.plugins_dir, pid, code, **manifest)

    return make


@pytest.fixture
def example(svc):
    """The example plugin from docs/examples, installed (off)."""
    dest = svc.config.plugins_dir / "recently-added"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def drain(svc, pid=None):
    assert svc.plugins.drain(pid), "plugin listeners did not finish"
