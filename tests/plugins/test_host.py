"""The plugin host: turning plugins on and off, their routes, tools, events and settings, failures,
safe mode and the MCP server following plugins.json."""

import json
import shutil
import sys
import threading

from backend import create_app
from backend.config import Config
from backend.mcp import build_server
from backend.plugins.context import schema_of
from backend.services import Services
from tests.plugins.conftest import EXAMPLE, drain, write_plugin

LISTENER = """
from backend.plugins import Plugin, PluginError

seen = []

def activate(plugin: Plugin):
    for event in ("item.added", "item.changed", "item.trashed", "item.restored", "item.deleted",
                  "citekey.changed", "note.saved"):
        plugin.on(event, lambda e: plugin.storage.json("seen").update({str(len(plugin.storage.json("seen").all())): e}))

    @plugin.route("/hello/<name>", methods=["GET", "POST"])
    def hello(name):
        if name == "nobody":
            raise PluginError("Say who")
        if name == "crash":
            raise RuntimeError("boom")
        return {"hello": name, "role": plugin.role}

    @plugin.route("/text")
    def text():
        return "plain"
"""


def events(svc, pid="listener"):
    drain(svc, pid)
    seen = svc.plugins._active[pid].plugin.storage.json("seen").all()
    return [seen[k] for k in sorted(seen, key=int)]


def mcp_call(server, name, **args):
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r["result"]


def tool_names(server):
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    return {t["name"] for t in r["result"]["tools"]}


# ------------------------------------------------------------------ listing, turning on and off
def test_plugins_are_listed_off_until_turned_on(client, svc, example):
    data = client.get("/api/plugin-host").get_json()
    assert data["safe_mode"] is False and data["api"] == "1.10"
    assert data["dir"] == str(svc.config.plugins_dir)
    [p] = data["plugins"]
    assert p["id"] == "recently-added" and p["status"] == "off" and not p["running"]
    assert p["source"] == "user" and p["permissions"]["mcp"] is True
    assert p["values"] == {"keep_days": 90, "skip_tags": []}
    assert client.get("/api/plugins/recently-added/recent").status_code == 404


def test_turning_on_and_off(client, svc, example):
    since = svc.changes.seq
    p = client.post("/api/plugin-host/recently-added/enable").get_json()["plugin"]
    assert p["status"] == "on" and p["running"]
    assert p["tools"] == ["recently-added__recently_added"]
    assert p["routes"] == [
        {"rule": "/recent", "methods": ["GET"]},
        {"rule": "/item/<item_id>", "methods": ["GET"]},
        {"rule": "/forget", "methods": ["POST"]},
    ]
    assert p["frontend"] == "frontend/main.js" and p["styles"] == "frontend/style.css" and p["started"]
    assert p["events"] == ["item.added", "item.deleted"]
    assert "plugins" in svc.changes.since(since, svc.changes.epoch, None)["changes"]
    assert json.loads(svc.config.plugins_state_path.read_text())["plugins"]["recently-added"]["enabled"] is True

    item = client.post("/api/items", json={"title": "Fresh paper", "tags": ["x"]}).get_json()
    drain(svc)
    got = client.get("/api/plugins/recently-added/recent?days=3").get_json()
    assert [i["title"] for i in got["items"]] == ["Fresh paper"]
    assert client.get("/api/plugins/recently-added/recent?days=0").status_code == 400  # PluginError → 400

    p = client.post("/api/plugin-host/recently-added/disable").get_json()["plugin"]
    assert p["status"] == "off" and not p["running"] and "tools" not in p
    assert client.get("/api/plugins/recently-added/recent").status_code == 404
    # its data stays for the next time
    assert (svc.config.plugin_data_dir / "recently-added" / "added.sqlite").is_file()
    client.post("/api/plugin-host/recently-added/enable")
    assert client.get("/api/plugins/recently-added/recent").get_json()["items"][0]["item_id"] == item["id"]


def test_plugins_that_are_on_start_with_the_app(tmp_path):
    cfg = Config.from_env(tmp_path / "other")
    shutil.copytree(EXAMPLE, cfg.plugins_dir / "recently-added")
    cfg.plugins_state_path.write_text(json.dumps({"plugins": {"recently-added": {"enabled": True}}}))
    svc = create_app(cfg, start_indexer=False).extensions["synapse"]
    assert svc.plugins.role == "app"
    assert svc.plugins.info("recently-added")["running"]


def test_routes(client, svc, make_plugin):
    make_plugin("listener", LISTENER)
    client.post("/api/plugin-host/listener/enable")
    assert client.get("/api/plugins/listener/hello/ann").get_json() == {"hello": "ann", "role": "app"}
    assert client.post("/api/plugins/listener/hello/bob").get_json()["hello"] == "bob"
    assert client.delete("/api/plugins/listener/hello/bob").status_code == 405
    assert client.get("/api/plugins/listener/nothing").status_code == 404
    r = client.get("/api/plugins/listener/hello/nobody")
    assert r.status_code == 400 and r.get_json()["error"] == "Say who"
    r = client.get("/api/plugins/listener/text")
    assert r.data == b"plain" and r.mimetype == "text/plain"
    r = client.get("/api/plugins/listener/hello/crash")
    assert r.status_code == 500 and "log" in r.get_json()["error"]
    assert any("RuntimeError: boom" in line for line in client.get("/api/plugin-host/listener/log").get_json()["lines"])


def test_events(client, svc, make_plugin):
    make_plugin("listener", LISTENER)
    client.post("/api/plugin-host/listener/enable")
    a = client.post("/api/items", json={"title": "A", "citation_key": "a2020"}).get_json()
    client.patch(f"/api/items/{a['id']}", json={"year": 2020})
    client.patch(f"/api/items/{a['id']}", json={"citation_key": "renamed"})
    svc.papers.trash([a["id"]])
    svc.papers.restore([a["id"]])
    svc.papers.delete_permanently([a["id"]])
    svc.note_service.create(path="n.md", title="N")
    got = [(e["event"], e.get("ids") or e.get("path") or e.get("new")) for e in events(svc)]
    assert got == [
        ("item.added", [a["id"]]),
        ("item.changed", [a["id"]]),
        ("citekey.changed", "renamed"),
        ("item.changed", [a["id"]]),
        ("item.trashed", [a["id"]]),
        ("item.restored", [a["id"]]),
        ("item.deleted", [a["id"]]),
        ("note.saved", "n.md"),
    ]
    changed = [e for e in events(svc) if e["event"] == "item.changed"]
    assert changed[0]["fields"] == ["year"] and changed[1]["fields"] == ["citation_key"]
    renamed = next(e for e in events(svc) if e["event"] == "citekey.changed")
    assert (renamed["old"], renamed["new"], renamed["id"]) == ("a2020", "renamed", a["id"])
    assert events(svc)[-1]["by"] == "app"


# ------------------------------------------------------------------ settings
def test_settings(client, svc, example):
    client.post("/api/plugin-host/recently-added/enable")
    r = client.put("/api/plugin-host/recently-added/settings", json={"keep_days": "30", "skip_tags": "draft, old"})
    assert r.get_json()["values"] == {"keep_days": 30, "skip_tags": ["draft", "old"]}
    for bad in ({"keep_days": 0}, {"nope": 1}):
        r = client.put("/api/plugin-host/recently-added/settings", json=bad)
        assert r.status_code == 400
    drain(svc)
    assert any("Settings changed: keep_days, skip_tags" in line for line in svc.plugins.log_lines("recently-added"))
    # skipped by tag
    client.post("/api/items", json={"title": "Draft", "tags": ["Draft"]})
    client.post("/api/items", json={"title": "Kept"})
    drain(svc)
    titles = [i["title"] for i in client.get("/api/plugins/recently-added/recent").get_json()["items"]]
    assert titles == ["Kept"]


def test_a_value_that_no_longer_fits_falls_back_to_the_default(svc, example):
    svc.plugins.state.update("recently-added", settings={"keep_days": 99999, "skip_tags": ["a"]})
    svc.plugins.scan()
    assert svc.plugins.values("recently-added") == {"keep_days": 90, "skip_tags": ["a"]}


# ------------------------------------------------------------------ Claude
def test_tools_reach_claude(client, svc, example):
    server = build_server(svc)
    assert "recently-added__recently_added" not in tool_names(server)
    client.post("/api/plugin-host/recently-added/enable")
    client.post("/api/items", json={"title": "For Claude"})
    drain(svc)
    assert "recently-added__recently_added" in tool_names(server)  # no restart needed
    res = mcp_call(server, "recently-added__recently_added", days=2)
    assert not res["isError"] and "For Claude" in res["content"][0]["text"]
    res = mcp_call(server, "recently-added__recently_added", days=99999)
    assert res["isError"] and "between 1 and 3650" in res["content"][0]["text"]  # PluginError: readable
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
    tool = next(t for t in listed if t["name"] == "recently-added__recently_added")
    assert tool["description"].startswith("[Recently Added] ")
    assert tool["inputSchema"] == {"type": "object", "properties": {"days": {"type": "integer"}}}


def test_other_computers_get_tools_only_when_allowed(client, svc, example):
    client.post("/api/plugin-host/recently-added/enable")
    remote = build_server(svc, remote=True)
    assert "recently-added__recently_added" not in tool_names(remote)
    p = client.put("/api/plugin-host/recently-added/remote-mcp", json={"on": True}).get_json()["plugin"]
    assert p["remote_mcp"] is True
    assert "recently-added__recently_added" in tool_names(remote)


def test_the_mcp_server_follows_plugins_turned_on_in_the_app(client, svc, example):
    mcp_services = Services(svc.config)
    mcp_services.plugins.start("mcp")
    server = build_server(mcp_services)
    assert "recently-added__recently_added" not in tool_names(server)
    client.post("/api/plugin-host/recently-added/enable")
    assert "recently-added__recently_added" in tool_names(server)
    assert mcp_services.plugins.info("recently-added")["running"]
    assert mcp_services.plugins._active["recently-added"].plugin.role == "mcp"
    client.post("/api/plugin-host/recently-added/disable")
    assert "recently-added__recently_added" not in tool_names(server)
    mcp_services.plugins.stop()


def test_tool_schemas_from_signatures():
    from typing import Literal

    def f(
        query: str, limit: int = 10, exact: bool = False, cats: list[str] | None = None, how: Literal["a", "b"] = "a"
    ):
        pass

    assert schema_of(f) == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "exact": {"type": "boolean"},
            "cats": {"type": "array", "items": {"type": "string"}},
            "how": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["query"],
    }


# ------------------------------------------------------------------ failures
def test_a_plugin_that_cannot_start_is_turned_off_with_the_reason(client, svc, make_plugin):
    make_plugin("broken", "def activate(plugin):\n    raise ValueError('no config')\n")
    r = client.post("/api/plugin-host/broken/enable").get_json()["plugin"]
    assert r["status"] == "failed" and not r["enabled"] and not r["running"]
    assert r["problem"] == "Could not start: ValueError: no config"
    assert any("Traceback" in line for line in svc.plugins.log_lines("broken"))
    make_plugin("noactivate", "x = 1\n")
    assert (
        client.post("/api/plugin-host/noactivate/enable")
        .get_json()["plugin"]["problem"]
        .endswith("no activate(plugin) function")
    )


def test_a_plugin_failing_repeatedly_is_turned_off(client, svc, make_plugin, monkeypatch):
    make_plugin("flaky", "def activate(plugin):\n    plugin.on('item.added', lambda e: 1 / 0)\n")
    client.post("/api/plugin-host/flaky/enable")
    since = svc.changes.seq
    published = threading.Event()
    changed = svc.plugins.changed
    assert changed is not None

    def notified():
        changed()
        published.set()

    monkeypatch.setattr(svc.plugins, "changed", notified)
    for i in range(3):
        client.post("/api/items", json={"title": f"t{i}"})
    # Failure state is written before the shutdown thread publishes the change feed.
    assert published.wait(5), "plugin failure was not published"
    info = svc.plugins.info("flaky")
    assert info["status"] == "failed" and not info["enabled"] and not info["running"]
    assert "failing repeatedly" in info["problem"] and "ZeroDivisionError" in info["problem"]
    assert "plugins" in svc.changes.since(since, svc.changes.epoch, None)["changes"]
    # turning it on again clears the failure
    assert client.post("/api/plugin-host/flaky/enable").get_json()["plugin"]["status"] == "on"


def test_unload_hooks_run_when_turned_off(client, svc, make_plugin, tmp_path):
    flag = tmp_path / "stopped"
    make_plugin(
        "tidy",
        f"""
        from pathlib import Path

        def activate(plugin):
            plugin.on_unload(lambda: Path({str(flag)!r}).write_text("unload"))

        def deactivate(plugin):
            Path({str(flag)!r} + "2").write_text("deactivate")
        """,
    )
    client.post("/api/plugin-host/tidy/enable")
    client.post("/api/plugin-host/tidy/disable")
    assert flag.read_text() == "unload" and (tmp_path / "stopped2").read_text() == "deactivate"


def test_invalid_and_incompatible_plugins_cannot_be_turned_on(client, svc, make_plugin):
    (svc.config.plugins_dir / "junk").mkdir(parents=True)
    make_plugin("future", api="^1.99")
    listed = {p["id"]: p for p in client.get("/api/plugin-host").get_json()["plugins"]}
    assert listed["junk"]["status"] == "invalid" and "missing" in listed["junk"]["problem"]
    assert listed["future"]["status"] == "incompatible"
    for pid in ("junk", "future"):
        r = client.post(f"/api/plugin-host/{pid}/enable")
        assert r.status_code == 400
    assert client.post("/api/plugin-host/nothing/enable").status_code == 400


def test_permissions_are_needed(client, svc, make_plugin):
    make_plugin("nosy", "def activate(plugin):\n    plugin.library.items()\n")
    assert "library" in client.post("/api/plugin-host/nosy/enable").get_json()["plugin"]["problem"]
    make_plugin("talker", "def activate(plugin):\n    plugin.mcp_tool('x')(lambda: 1)\n")
    assert '"mcp": true' in client.post("/api/plugin-host/talker/enable").get_json()["plugin"]["problem"]


def test_safe_mode_starts_no_plugin(tmp_path):
    cfg = Config.from_env(tmp_path / "other")
    shutil.copytree(EXAMPLE, cfg.plugins_dir / "recently-added")
    cfg.plugins_state_path.write_text(json.dumps({"plugins": {"recently-added": {"enabled": True}}}))
    cfg.safe_mode = True
    safe = create_app(cfg, start_indexer=False)
    info = safe.extensions["synapse"].plugins.info("recently-added")
    assert info["status"] == "safe-mode" and not info["running"] and info["enabled"]
    assert safe.test_client().get("/api/plugin-host").get_json()["safe_mode"] is True


def test_restart_picks_up_changed_code(client, svc, make_plugin):
    make_plugin("ver", "def activate(plugin):\n    plugin.route('/v')(lambda: {'v': 1})\n")
    client.post("/api/plugin-host/ver/enable")
    assert client.get("/api/plugins/ver/v").get_json() == {"v": 1}
    # a different length: Python's bytecode cache only notices changes by size and by the second
    make_plugin("ver", "def activate(plugin):\n    plugin.route('/v')(lambda: {'v': 22})\n")
    assert client.get("/api/plugins/ver/v").get_json() == {"v": 1}
    client.post("/api/plugin-host/ver/restart")
    assert client.get("/api/plugins/ver/v").get_json() == {"v": 22}


def test_a_user_plugin_replaces_a_bundled_one(svc, tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    write_plugin(bundled, "dup", name="Bundled")
    write_plugin(bundled, "only-bundled")
    write_plugin(svc.config.plugins_dir, "dup", name="Mine")
    monkeypatch.setattr(type(svc.config), "bundled_plugins_dir", property(lambda self: bundled))
    listed = {p["id"]: p for p in svc.plugins.listing()}
    assert listed["dup"]["name"] == "Mine" and listed["dup"]["replaces_bundled"]
    assert listed["only-bundled"]["source"] == "bundled"


def test_packages_with_submodules_load_and_unload(client, svc, example):
    before = set(sys.modules)
    client.post("/api/plugin-host/recently-added/enable")
    names = set(sys.modules) - before
    assert len(names) == 2 and any(n.endswith(".store") for n in names)
    client.post("/api/plugin-host/recently-added/disable")
    assert not names & set(sys.modules)


# ------------------------------------------------------------------ frontend files
def test_frontend_files_are_served_while_running(client, svc, example, make_plugin):
    assert client.get("/api/plugin-files/recently-added/frontend/main.js").status_code == 404  # off
    client.post("/api/plugin-host/recently-added/enable")
    r = client.get("/api/plugin-files/recently-added/frontend/main.js")
    assert r.status_code == 200 and r.mimetype == "text/javascript" and b"export function activate" in r.data
    assert client.get("/api/plugin-files/recently-added/frontend/style.css").mimetype == "text/css"
    for path in ("backend/__init__.py", "manifest.json", "frontend/../backend/store.py", "frontend/nothing.js"):
        assert client.get(f"/api/plugin-files/recently-added/{path}").status_code == 404, path
    # a frontend at the top of the folder still does not make the Python code readable
    make_plugin("flat", "def activate(plugin):\n    pass\n", frontend="main.js")
    (svc.config.plugins_dir / "flat" / "main.js").write_text("export function activate() {}")
    client.post("/api/plugin-host/flat/enable")
    assert client.get("/api/plugin-files/flat/main.js").status_code == 200
    assert client.get("/api/plugin-files/flat/plugin.py").status_code == 404
    assert client.get("/api/plugin-files/flat/manifest.json").status_code == 404


def test_a_restart_gives_a_new_start_time(client, svc, example):
    first = client.post("/api/plugin-host/recently-added/enable").get_json()["plugin"]["started"]
    again = client.post("/api/plugin-host/recently-added/restart").get_json()["plugin"]["started"]
    assert first and again and first != again


def test_safe_mode_follows_core_plugin_changes(tmp_path):
    from backend.config import Config
    from backend.plugins.host import PluginHost
    from tests.plugins.conftest import write_plugin

    cfg = Config.from_env(tmp_path / "data")
    write_plugin(cfg.bundled_plugins_dir, "core-test", core=True, enabled_by_default=True)
    cfg.safe_mode = True
    app_host, mcp_host = PluginHost(cfg), PluginHost(cfg)
    try:
        app_host.start("app")
        mcp_host.start("mcp")
        assert mcp_host.info("core-test")["running"]
        app_host.disable("core-test")
        mcp_host.refresh()
        assert not mcp_host.info("core-test")["running"]
        app_host.enable("core-test")
        mcp_host.refresh()
        assert mcp_host.info("core-test")["running"]
    finally:
        app_host.stop()
        mcp_host.stop()


def test_a_bundled_plugin_may_start_on_without_being_core(tmp_path):
    """A contributed plugin that comes with Synapse starts on the first time when its manifest says
    so, then follows the user's choice; an installed one with the same claim waits to be turned on,
    and safe mode stops both."""
    from backend.config import Config
    from backend.plugins.host import PluginHost
    from tests.plugins.conftest import write_plugin

    cfg = Config.from_env(tmp_path / "data")
    write_plugin(cfg.bundled_plugins_dir, "bundled-on", enabled_by_default=True, permissions={"mcp": True})
    write_plugin(cfg.bundled_plugins_dir, "bundled-off")
    write_plugin(cfg.plugins_dir, "installed-on", enabled_by_default=True)
    host = PluginHost(cfg)
    try:
        host.start("app")
        on = host.info("bundled-on")
        assert on["running"] and not on["core"] and not on["remote_mcp"]  # other computers: the user's choice
        assert not host.info("bundled-off")["enabled"] and not host.info("installed-on")["enabled"]
        host.disable("bundled-on")
    finally:
        host.stop()
    again = PluginHost(cfg)
    try:
        again.start("app")
        assert not again.info("bundled-on")["enabled"]  # the default applies only the first time
    finally:
        again.stop()
    cfg.safe_mode = True
    safe = PluginHost(cfg)
    try:
        safe.start("app")
        safe.enable("bundled-on")
        assert safe.info("bundled-on")["status"] == "safe-mode"
    finally:
        safe.stop()
