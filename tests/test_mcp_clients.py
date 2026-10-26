"""Which AI app made a change: the MCP server takes the app's name from ``initialize`` and notes and
tasks record it. Nothing in the server is particular to Claude: Codex and other clients get the
same answers. See backend/mcp/clients.py and backend/library/agent.py."""

import json
import sqlite3

import pytest

from backend.mcp import build_server
from backend.mcp.clients import client_label
from backend.tasks.store import TaskStore

from .test_mcp_http import from_other_pc


@pytest.mark.parametrize(
    "info, label",
    [
        ({"name": "claude-ai", "version": "0.1.0"}, "Claude"),
        ({"name": "claude-code", "version": "2.1"}, "Claude"),
        ({"name": "codex-mcp-client", "title": "Codex", "version": "0.50.0"}, "Codex"),
        ({"name": "codex_vscode"}, "Codex"),
        ({"name": "cursor-vscode"}, "Cursor"),
        ({"name": "gemini-cli-mcp-client"}, "Gemini"),
        ({"name": "Visual Studio Code"}, "VS Code"),
        ({"name": "my-agent", "title": "Lab Assistant"}, "Lab Assistant"),
        ({"name": "research-bot-mcp-client"}, "Research Bot"),
        ({"name": ""}, None),
        (None, None),
        ("codex", None),  # not an object: ignored
    ],
)
def test_client_label(info, label):
    assert client_label(info) == label


def init(server, info):
    params = {"protocolVersion": "2025-06-18", "capabilities": {}}
    if info is not None:
        params["clientInfo"] = info
    r = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": params})
    assert "result" in r, r


def call(server, name, **args):
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = r["result"]
    assert not res["isError"], res
    return json.loads(res["content"][0]["text"])


@pytest.fixture
def note(svc):
    svc.note_service.create(path="plan.md", title="Plan", text="# Plan\n\nFirst idea.\n")
    return "plan.md"


def test_note_edits_name_the_app(svc, note):
    codex = build_server(svc)
    init(codex, {"name": "codex-mcp-client", "title": "Codex", "version": "0.50.0"})
    call(codex, "edit_note", note=note, edits=[{"old": "First idea.", "new": "First idea, revised."}])
    claude = build_server(svc)
    init(claude, {"name": "claude-code", "version": "2.1"})
    call(claude, "edit_note", note=note, append="Second idea.", summary="Added an idea")
    anon = build_server(svc)  # a client that never said who it is
    call(anon, "edit_note", note=note, append="Third idea.")
    entries = [e for e in svc.note_service.history_of(note)["entries"] if e["by"] == "ai"]
    assert [e.get("agent") for e in entries] == [None, "Claude", "Codex"]  # newest first
    assert [e["by"] for e in entries] == ["ai"] * 3
    assert entries[2]["summary"] == "Edited by Codex" and entries[0]["summary"] == "Edited by an AI app"
    # and the app's own saves are the user's, from this computer
    text, h = svc.vault.read(note)
    svc.note_service.save(note, text + "Mine.\n", h)
    svc.note_service.checkpoint(note)
    mine = svc.note_service.history_of(note)["entries"][0]
    assert mine["by"] == "human" and mine["device"] == "This computer" and not mine["agent"]


def test_new_notes_name_the_app(svc):
    server = build_server(svc)
    init(server, {"name": "codex-mcp-client", "title": "Codex"})
    r = call(server, "write_note", title="Reading list", text="Some papers.")
    text = svc.vault.read(r["saved"])[0]
    assert "author: ai" in text and "agent: Codex" in text


def test_task_changes_name_the_app(svc):
    server = build_server(svc)
    init(server, {"name": "codex-mcp-client", "title": "Codex"})
    tid = call(server, "create_task", title="Check the proof")["created"]
    t = svc.task_store.get(tid)
    assert t["created_by"] == t["updated_by"] == "ai" and t["updated_agent"] == "Codex"
    other = build_server(svc)
    init(other, {"name": "claude-ai"})
    call(other, "update_task", task=tid, status="done")
    t = svc.task_store.get(tid)
    assert t["updated_agent"] == "Claude"
    assert [(h["by"], h["agent"]) for h in svc.task_store.history(tid)] == [("ai", "Claude"), ("ai", "Codex")]
    # the user's own change in the app clears it
    svc.task_store.update(tid, {"status": "open"})
    t = svc.task_store.get(tid)
    assert t["updated_by"] == "human" and t["updated_agent"] is None
    assert svc.task_store.history(tid)[0]["agent"] is None


def test_each_token_keeps_its_app(svc, tmp_path):
    from backend import create_app
    from backend.config import Config

    cfg = Config.from_env(tmp_path / "lan")
    cfg.lan = True
    app = create_app(cfg, start_indexer=False)
    app.config["TESTING"] = True
    c = app.test_client()
    lan = app.extensions["synapse"]
    lan.note_service.create(path="plan.md", title="Plan", text="# Plan\n\nFirst idea.\n")
    tokens = [c.post("/api/mcp/tokens", json={"name": n}).get_json()["token"] for n in ("Laptop", "Desktop")]

    def initialize(token, info):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "clientInfo": info},
        }
        assert from_other_pc(c, msg, token).status_code == 200

    def edit(token, line):
        msg = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "edit_note", "arguments": {"note": "plan.md", "append": line}},
        }
        r = from_other_pc(c, msg, token)
        assert r.status_code == 200 and not r.get_json()["result"]["isError"], r.get_json()

    initialize(tokens[0], {"name": "codex-mcp-client", "title": "Codex"})
    initialize(tokens[1], {"name": "claude-code"})
    edit(tokens[0], "From the laptop.")
    edit(tokens[1], "From the desktop.")
    edit(tokens[0], "Laptop again.")
    entries = [e for e in lan.note_service.history_of("plan.md")["entries"] if e["by"] == "ai"]
    assert [e.get("agent") for e in entries] == ["Codex", "Claude", "Codex"]


def test_old_task_tables_gain_the_columns(svc, tmp_path):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE tasks (id TEXT PRIMARY KEY, parent_id TEXT, position REAL NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
            due TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, done_at TEXT,
            created_by TEXT NOT NULL DEFAULT 'human', updated_by TEXT NOT NULL DEFAULT 'human',
            deleted_at TEXT, deleted_batch TEXT);
        CREATE TABLE task_history (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, at TEXT NOT NULL,
            by TEXT NOT NULL, action TEXT NOT NULL, before TEXT, after TEXT, undone INTEGER NOT NULL DEFAULT 0);
        INSERT INTO tasks (id, title, created_at, updated_at, updated_by) VALUES ('abcdefgh', 'Old', 'x', 'x', 'ai');
        """
    )
    c.commit()
    c.close()
    from backend.library.db import Database

    store = TaskStore(Database(path))
    old = store.get("abcdefgh")
    assert old["title"] == "Old" and old["updated_by"] == "ai" and old["updated_agent"] is None
    store.update("abcdefgh", {"title": "Old, renamed"})
    assert store.history("abcdefgh")[0]["agent"] is None
