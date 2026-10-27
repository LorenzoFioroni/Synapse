"""Task/notes permissions, legacy tool aliases, and contributed Tasks boundaries."""

import ast
import json

import pytest

from backend import create_app
from backend.config import APP_ROOT, Config
from backend.plugins import PluginError

PROBE = """
from flask import request

def activate(p):
    @p.route("/records")
    def records(): return {"tasks": p.tasks.records.list()}
    @p.route("/create", methods=["POST"])
    def create(): return p.tasks.create(request.get_json())
    @p.route("/overview")
    def overview(): return p.tasks.overview()
"""


def test_task_reads_and_writes_are_guarded(client, svc, make_plugin):
    make_plugin("reader", PROBE, permissions={"tasks": ["read"]})
    make_plugin("writer", PROBE, permissions={"tasks": ["read", "edit"]})
    make_plugin("denied", PROBE)
    for pid in ("reader", "writer", "denied"):
        svc.plugins.enable(pid)
    assert client.get("/api/plugins/denied/records").status_code == 400
    assert client.get("/api/plugins/reader/records").json == {"tasks": []}
    assert client.post("/api/plugins/reader/create", json={"title": "Denied"}).status_code == 400
    made = client.post("/api/plugins/writer/create", json={"title": "Kept"})
    assert made.status_code == 200 and made.json["task"]["title"] == "Kept"
    task = made.json["task"]
    reader = svc.plugins._active["reader"].plugin
    for method, args in [("update", (task["id"], {"title": "Denied"})), ("delete", ([task["id"]],))]:
        with pytest.raises(PluginError, match="edit"):
            getattr(reader.tasks.records, method)(*args)
    assert svc.task_store.get(task["id"])["title"] == "Kept"


def test_note_metadata_and_note_writes_need_their_own_permission(client, svc, make_plugin):
    make_plugin("writer", PROBE, permissions={"tasks": ["read", "edit"]})
    svc.plugins.enable("writer")
    note = client.post("/api/notes/note", json={"title": "Protected"}).json
    before = note["text"]
    assert client.get("/api/plugins/writer/overview").status_code == 400
    denied = client.post("/api/plugins/writer/create", json={"title": "Denied", "note": note["path"]})
    assert denied.status_code == 400
    assert svc.task_store.list() == []
    writer = svc.plugins._active["writer"].plugin
    with pytest.raises(PluginError, match="notes"):
        writer.tasks.add_image("x.png", b"not an image")
    assert client.get("/api/notes/note", query_string={"path": note["path"]}).json["text"] == before


def test_legacy_tools_are_reserved_to_the_bundled_tasks_plugin(client, svc, make_plugin, bundled):
    svc.plugins.enable("tasks")
    expected = {"list_tasks", "get_task", "create_task", "update_task", "delete_task"}
    assert expected <= {tool.name for tool in svc.plugins.mcp_tools()}
    svc.plugins.disable("tasks")
    make_plugin(
        "tasks",
        """
        def activate(p):
            @p.mcp_tool("Read tasks", name="list_tasks")
            def list_tasks(): return []
    """,
        permissions={"mcp": True},
        core=True,
    )
    svc.plugins.enable("tasks")
    names = {tool.name for tool in svc.plugins.mcp_tools()}
    assert "tasks__list_tasks" in names and "list_tasks" not in names
    assert not svc.plugins.info("tasks")["core"]


def test_safe_mode_keeps_task_records_and_note_tokens(tmp_path, bundled):
    data = tmp_path / "data"
    cfg = Config.from_env(data)
    app = create_app(cfg, start_indexer=False)
    services = app.extensions["synapse"]
    try:
        assert services.plugins.info("tasks")["remote_mcp"]
        task = services.tasks.create({"title": "Kept"})["task"]
        note = services.note_service.create(title="Kept note")
        services.tasks.add_to_note(task["id"], note["path"])
    finally:
        services.plugins.stop()
    cfg.safe_mode = True
    app = create_app(cfg, start_indexer=False)
    try:
        services = app.extensions["synapse"]
        info = services.plugins.info("tasks")
        assert info["enabled"] and not info["core"] and not info["running"]
        assert services.task_store.get(task["id"])["title"] == "Kept"
        assert "{{task:" + task["id"] + "}}" in services.note_service.get(note["path"])["text"]
        assert app.test_client().get("/api/tasks").status_code == 404
        assert not {"list_tasks", "get_task"} & {tool.name for tool in services.plugins.mcp_tools()}
    finally:
        services.plugins.stop()


def test_tasks_import_only_public_plugin_interfaces():
    folder = APP_ROOT / "plugins" / "tasks"
    for path in (folder / "server").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("backend."):
                assert node.module in {"backend.plugins", "backend.plugins.tasks"}, (path, node.module)
        assert "plugin.synapse" not in path.read_text()
    for path in (folder / "frontend").glob("*.ts"):
        for line in path.read_text().splitlines():
            if 'from "../../../' in line or "from '../../../" in line:
                assert "frontend/src/plugins/" in line, (path, line)
    manifest = json.loads((folder / "manifest.json").read_text())
    assert not manifest.get("core") and manifest["enabled_by_default"]


def test_task_image_upload_uses_plugin_route_and_core_validation(client, svc, bundled):
    import base64
    import io

    svc.plugins.enable("tasks")
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXwAAAABJRU5ErkJggg=="
    )
    response = client.post("/api/plugins/tasks/images", data={"file": (io.BytesIO(png), "pixel.png")})
    assert response.status_code == 201, response.json
    assert response.json["path"].startswith("attachments/")
    bad = client.post("/api/plugins/tasks/images", data={"file": (io.BytesIO(b"not an image"), "bad.png")})
    assert bad.status_code == 400
