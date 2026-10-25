"""One name (Synapse) and one icon everywhere."""

import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from backend.config import Config

ROOT = Path(__file__).resolve().parent.parent
USER_FACING = [
    "README.md",
    "server.py",
    "mcp_server.py",
    "frontend/index.html",
    "frontend/viewer.html",
    "frontend/src/main.ts",
    "browser_extension/manifest.json",
    "browser_extension/README.md",
    "browser_extension/popup.html",
    "browser_extension/options.html",
    "backend/online/metadata.py",
    "backend/online/pdf_finder.py",
    "backend/api/connector.py",
]


@pytest.mark.parametrize("rel", USER_FACING)
def test_no_old_name(rel):
    text = (ROOT / rel).read_text("utf-8")
    assert not re.search(r"zotero[ -]?clone", text, re.I), rel


def test_titles_and_ping(client):
    assert "<title>Library — Synapse</title>" in (ROOT / "frontend/index.html").read_text("utf-8")
    assert client.get("/api/connector/ping").get_json()["app"] == "synapse"
    import json

    assert json.loads((ROOT / "browser_extension/manifest.json").read_text())["name"] == "Save to Synapse"


def test_extension_accepts_both_ping_names():
    """An extension that has not been reloaded yet keeps talking to a new server, and the
    new extension still recognises an old server."""
    for f in ("popup.js", "options.js"):
        js = (ROOT / "browser_extension" / f).read_text("utf-8")
        assert "['synapse', 'zotero-clone'].includes(" in js, f


def test_data_dir_env(monkeypatch, tmp_path):
    monkeypatch.delenv("SYNAPSE_DATA_DIR", raising=False)
    monkeypatch.setenv("ZCLONE_DATA_DIR", str(tmp_path / "old"))
    assert Config.from_env().data_dir == (tmp_path / "old").resolve()  # old name still works
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "new"))
    assert Config.from_env().data_dir == (tmp_path / "new").resolve()  # new name wins
    assert Config.from_env(tmp_path / "arg").data_dir == (tmp_path / "arg").resolve()


def test_favicon_svg_is_a_valid_svg():
    root = ET.parse(ROOT / "frontend/assets/favicon.svg").getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg"


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


@pytest.mark.parametrize(
    "rel,size",
    [
        ("frontend/assets/favicon.png", 16),
        ("frontend/assets/favicon-32.png", 32),
        ("frontend/assets/apple-touch-icon.png", 180),
        ("frontend/assets/icon-192.png", 192),
        ("frontend/assets/icon-512.png", 512),
        ("browser_extension/icons/icon16.png", 16),
        ("browser_extension/icons/icon32.png", 32),
        ("browser_extension/icons/icon48.png", 48),
        ("browser_extension/icons/icon128.png", 128),
    ],
)
def test_icon_files(rel, size):
    assert png_size(ROOT / rel) == (size, size)


def test_toolbar_shows_the_icon_not_a_letter():
    js = (ROOT / "frontend/src/main.ts").read_text("utf-8")
    assert '<img class="brand-logo" src="/assets/favicon.svg' in js
    assert '<div class="brand-logo">Z</div>' not in js


def test_page_links_a_large_android_icon():
    html = (ROOT / "frontend/index.html").read_text("utf-8")
    assert 'rel="icon" type="image/png" sizes="192x192" href="/assets/icon-192.png?v=3"' in html
