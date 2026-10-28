"""Graph uses public, permission-gated reads and preserves its existing data contracts."""

import ast
import json

import pytest

from backend import create_app
from backend.config import APP_ROOT, Config
from backend.plugins.manifest import ManifestError, parse

READS = """
def activate(p):
    @p.route("/notes")
    def notes(): return {"notes": p.notes.list(), "links": p.notes.links()}
    @p.route("/citations")
    def citations(): return p.library.citation_graph()
"""


@pytest.mark.parametrize("notes", [["write"], "read", True, None, ["read", "admin"]])
def test_notes_permission_is_read_only(tmp_path, notes):
    with pytest.raises(ManifestError, match='"notes"'):
        parse(
            {"id": "demo", "name": "Demo", "version": "1", "api": "^1.7", "permissions": {"notes": notes}},
            tmp_path / "demo",
        )


def test_note_and_citation_reads_require_independent_permissions(client, svc, make_plugin):
    make_plugin("denied", READS)
    make_plugin("notes-only", READS, permissions={"notes": ["read"]})
    make_plugin("library-only", READS, permissions={"library": ["read"]})
    for pid in ("denied", "notes-only", "library-only"):
        svc.plugins.enable(pid)
    assert client.get("/api/plugins/denied/notes").status_code == 400
    assert client.get("/api/plugins/denied/citations").status_code == 400
    assert client.get("/api/plugins/notes-only/notes").status_code == 200
    assert client.get("/api/plugins/notes-only/citations").status_code == 400
    assert client.get("/api/plugins/library-only/notes").status_code == 400
    assert client.get("/api/plugins/library-only/citations").status_code == 200


def test_public_reads_preserve_links_empty_fetches_and_trash(client, svc, make_plugin, monkeypatch):
    monkeypatch.setattr("requests.request", lambda *a, **k: pytest.fail("graph reads attempted network"))
    make_plugin("reader", READS, permissions={"notes": ["read"], "library": ["read"]})
    svc.plugins.enable("reader")
    paper = svc.papers.create({"title": "Paper", "citation_key": "paper2026"})
    trashed = svc.papers.create({"title": "Trash"})
    svc.citations.store.save(paper["id"], "crossref", [])
    svc.citations.store.save(trashed["id"], "crossref", [])
    svc.papers.trash([trashed["id"]])
    client.post("/api/notes/note", json={"title": "Target", "folder": "topics"})
    source = client.post("/api/notes/note", json={"title": "Source"}).json
    client.put(
        "/api/notes/note",
        json={
            "path": source["path"],
            "text": source["text"] + "\n[[topics/Target]] ![[topics/Target]] [@paper2026] [[Missing]]",
            "hash": source["hash"],
        },
    )
    got = client.get("/api/plugins/reader/notes").json
    assert {n["path"] for n in got["notes"]} >= {"Source.md", "topics/Target.md"}
    assert {tuple(row[k] for k in ("source", "kind", "target")) for row in got["links"]} >= {
        ("Source.md", "note", "topics/Target.md"),
        ("Source.md", "cite", "paper2026"),
    }
    assert all(row["target"] != "Missing" for row in got["links"])
    graph = client.get("/api/plugins/reader/citations").json
    assert graph["fetched"] == [paper["id"]]
    assert not any(n.get("item_id") == trashed["id"] for n in graph["nodes"])


def test_graph_is_contributed_and_safe_mode_preserves_enabled_choice(tmp_path, bundled):
    data = tmp_path / "safe"
    data.mkdir()
    (data / "plugins.json").write_text(json.dumps({"plugins": {"graph": {"enabled": True}}}))
    config = Config.from_env(data)
    config.safe_mode = True
    app = create_app(config, start_indexer=False)
    try:
        host = app.extensions["synapse"].plugins
        info = host.info("graph")
        assert not info["core"] and info["enabled"] and not info["running"]
        assert info["status"] == "safe-mode"
        assert app.test_client().get("/api/plugins/graph/data").status_code == 404
    finally:
        app.extensions["synapse"].plugins.stop()


def test_graph_imports_only_public_backend_and_frontend_interfaces():
    folder = APP_ROOT / "plugins" / "graph"
    for path in (folder / "server").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("backend."):
                assert node.module == "backend.plugins"
    assert "plugin.synapse" not in (folder / "server" / "__init__.py").read_text()
    for path in (folder / "frontend").glob("*.ts"):
        for line in path.read_text().splitlines():
            if 'from "../../../' in line or "from '../../../" in line:
                assert "import type" in line and "frontend/src/plugins/api.js" in line, (path, line)


def test_graph_add_uses_public_duplicate_and_library_actions(client, svc, bundled, monkeypatch):
    monkeypatch.setattr(svc.plugins, "lookup_metadata", lambda q: {"title": "Fetched title", "doi": q["value"]})
    svc.plugins.enable("graph")
    paper = {"doi": "10.1234/from-graph", "title": "Graph title"}
    added = client.post("/api/plugins/graph/add", json=paper)
    assert added.status_code == 200 and not added.json["duplicate"]
    again = client.post("/api/plugins/graph/add", json=paper)
    assert again.status_code == 200 and again.json["duplicate"]
    assert again.json["id"] == added.json["id"]
    assert len(svc.papers.list()) == 1
