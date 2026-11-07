"""What plugins get from the host besides the basics: background work wired to the app, Python
packages, TypeScript frontends, a new plugin from the scaffold, and dev plugins reloading."""

import json
import os
import sys
import time

import pytest

from backend.config import APP_ROOT
from backend.plugins import deps
from backend.plugins.build import esbuild
from backend.plugins.errors import PluginError
from backend.plugins.manifest import ManifestError, parse
from backend.plugins.scaffold import create

HAS_ESBUILD = bool(esbuild(APP_ROOT / "frontend"))


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------------ background work in the app
WORKER = """
from backend.plugins import Plugin

def activate(plugin: Plugin):
    log = plugin.storage.json("log")

    @plugin.job("remember")
    def remember(text):
        log.set(text, True)

    @plugin.schedule("first", every="1h")
    def first():
        log.set("scheduled", True)

    @plugin.on("item.added")
    def added(e):
        for i in e["ids"]:
            plugin.jobs.enqueue("remember", i, key=i)
"""


def test_jobs_and_schedules_run_in_the_app(client, svc, make_plugin):
    make_plugin("worker", WORKER)
    p = client.post("/api/plugin-host/worker/enable").get_json()["plugin"]
    assert p["schedules"] == [{"name": "first", "rule": "every 3600 s"}]
    item = client.post("/api/items", json={"title": "x"}).get_json()
    store = lambda: json.loads((svc.config.plugin_data_dir / "worker" / "log.json").read_text())  # noqa: E731
    assert wait(lambda: (svc.config.plugin_data_dir / "worker" / "log.json").exists() and item["id"] in store())
    assert wait(lambda: store().get("scheduled"))  # catch_up: the first time at once
    client.post("/api/plugin-host/worker/disable")
    assert not svc.plugins._active


def test_the_mcp_server_queues_but_does_not_run(svc, make_plugin):
    from backend.services import Services

    make_plugin("worker", WORKER)
    svc.plugins.state.update("worker", enabled=True)
    mcp = Services(svc.config)
    mcp.plugins.start("mcp")
    act = mcp.plugins._active["worker"]
    assert act.runner is None  # no background thread in the MCP server
    assert act.plugin.jobs.enqueue("remember", "queued by Claude")
    svc.plugins.refresh()
    svc.plugins.scan()
    svc.plugins._sync()
    store = svc.config.plugin_data_dir / "worker" / "log.json"
    assert wait(lambda: store.exists() and "queued by Claude" in json.loads(store.read_text()))
    mcp.plugins.stop()


def test_http_follows_the_manifest(svc, make_plugin):
    make_plugin(
        "web",
        "def activate(plugin):\n    plugin.storage.json('s').set('hosts', plugin.http.hosts)\n",
        permissions={"network": ["Export.arxiv.org", "*.example.org"]},
    )
    svc.plugins.enable("web")
    assert svc.plugins._active["web"].plugin.http.hosts == ["*.example.org", "export.arxiv.org"]
    ua = svc.plugins._active["web"].plugin.http.user_agent()
    assert "plugin web" in ua and "mailto:" in ua


# ------------------------------------------------------------------ Python packages
def test_requirements_are_checked():
    core = deps.core_packages(APP_ROOT)
    assert {"flask", "requests", "pypdf"} <= core
    assert deps.check(["scirate>=0.0.7", "feedparser==6.0.11", "lxml[html]"], core)
    for bad in ("--index-url=http://x", "git+https://x/y", "../local", "flask>=1", "Requests"):
        with pytest.raises(PluginError):
            deps.check([bad], core)


def test_requirement_syntax_is_checked_without_backtracking():
    for good in ("a", "a>=1", "a[x,y]>=1.0, <2", "a == 1.*", "a>=1,"):
        assert deps.is_requirement(good), good
    for bad in ("", "a>=", "a>=1 <2", "a,b", ">=1", "a>=1,,<2"):
        assert not deps.is_requirement(bad), bad
    start = time.monotonic()
    assert not deps.is_requirement("a>=0" + " <!" * 50_000)
    assert time.monotonic() - start < 2


def test_a_manifest_with_bad_requirements_is_invalid(tmp_path):
    (tmp_path / "demo").mkdir()
    with pytest.raises(ManifestError, match="Synapse's own"):
        parse({"id": "demo", "name": "D", "version": "1", "api": "^1.0", "python": ["flask"]}, tmp_path / "demo")


def test_packages_are_installed_once_and_on_the_path_while_running(client, svc, make_plugin):
    installs = []

    def fake_pip(reqs, target):
        installs.append(list(reqs))
        (target / "fakepkg_demo").mkdir()
        (target / "fakepkg_demo" / "__init__.py").write_text("VALUE = 42\n")

    svc.plugins.installer = fake_pip
    make_plugin(
        "uses-pkg",
        "import fakepkg_demo\n\ndef activate(plugin):\n    plugin.route('/v')(lambda: {'v': fakepkg_demo.VALUE})\n",
        python=["fakepkg-demo>=1.0"],
    )
    client.post("/api/plugin-host/uses-pkg/enable")
    assert client.get("/api/plugins/uses-pkg/v").get_json() == {"v": 42}
    target = str(svc.config.plugin_data_dir / "uses-pkg" / ".python")
    assert target in sys.path
    client.post("/api/plugin-host/uses-pkg/restart")
    assert installs == [["fakepkg-demo>=1.0"]]  # not again
    client.post("/api/plugin-host/uses-pkg/disable")
    assert target not in sys.path
    sys.modules.pop("fakepkg_demo", None)


def test_a_failed_install_keeps_the_plugin_off(client, svc, make_plugin):
    def offline(reqs, target):
        raise PluginError("Installing x failed (is the computer online?)")

    svc.plugins.installer = offline
    make_plugin("needs-net", "def activate(plugin):\n    pass\n", python=["x>=1"])
    p = client.post("/api/plugin-host/needs-net/enable").get_json()["plugin"]
    assert p["status"] == "failed" and "online" in p["problem"]
    assert not (svc.config.plugin_data_dir / "needs-net" / ".python").exists()


# ------------------------------------------------------------------ TypeScript and the scaffold
@pytest.mark.skipif(not HAS_ESBUILD, reason="needs esbuild (npm ci in frontend/)")
def test_a_typescript_frontend_is_bundled(client, svc, make_plugin):
    folder = make_plugin("typed", None)
    (folder / "src").mkdir()
    (folder / "src" / "util.ts").write_text("export const greet = (n: string): string => `hi ${n}`;\n")
    (folder / "src" / "main.ts").write_text(
        "import { greet } from './util';\nexport function activate(p: { notify(m: string): void }) { p.notify(greet('x')); }\n"
    )
    man = json.loads((folder / "manifest.json").read_text())
    man["frontend"] = "src/main.ts"
    (folder / "manifest.json").write_text(json.dumps(man))
    p = client.post("/api/plugin-host/typed/enable").get_json()["plugin"]
    assert p["running"] and p["frontend"] == "__build__/main.js"
    js = client.get("/api/plugin-files/typed/__build__/main.js")
    assert js.status_code == 200 and b"hi ${n}" in js.data and b"function activate" in js.data  # one bundle
    assert client.get("/api/plugin-files/typed/src/main.ts").status_code == 200  # the source is there too


@pytest.mark.skipif(not HAS_ESBUILD, reason="needs esbuild (npm ci in frontend/)")
def test_a_typescript_error_keeps_the_plugin_off(client, svc, make_plugin):
    folder = make_plugin("broken-ts", None)
    (folder / "main.ts").write_text("export function activate( {\n")
    man = json.loads((folder / "manifest.json").read_text())
    man["frontend"] = "main.ts"
    (folder / "manifest.json").write_text(json.dumps(man))
    p = client.post("/api/plugin-host/broken-ts/enable").get_json()["plugin"]
    assert p["status"] == "failed" and "Building its frontend failed" in p["problem"]


def test_the_scaffold_makes_a_plugin_that_starts(client, svc):
    folder = create("my-plugin", svc.config.plugins_dir)
    assert (folder / "frontend" / "api.ts").read_text() == (APP_ROOT / "frontend/src/plugins/api.ts").read_text()
    man = json.loads((folder / "manifest.json").read_text())
    assert man["name"] == "My Plugin" and man["frontend"] == "frontend/main.ts"
    with pytest.raises(PluginError, match="exists already"):
        create("my-plugin", svc.config.plugins_dir)
    with pytest.raises(PluginError):
        create("Bad Id", svc.config.plugins_dir)
    if not HAS_ESBUILD:
        pytest.skip("needs esbuild to start (its frontend is TypeScript)")
    p = client.post("/api/plugin-host/my-plugin/enable").get_json()["plugin"]
    assert p["running"], p["problem"]
    assert client.get("/api/plugins/my-plugin/hello").get_json()["message"] == "Hello from My Plugin"
    assert p["tools"] == ["my-plugin__added_count"] and p["schedules"][0]["rule"] == "0 9 * * *"


def test_dev_plugins_restart_when_a_file_changes(tmp_path):
    from backend import create_app
    from backend.config import Config
    from tests.plugins.conftest import write_plugin

    dev = tmp_path / "dev"
    write_plugin(dev, "live", "def activate(plugin):\n    plugin.route('/v')(lambda: {'v': 1})\n")
    cfg = Config.from_env(tmp_path / "data")
    cfg.dev_plugins_dir = dev
    app = create_app(cfg, start_indexer=False)
    svc = app.extensions["synapse"]
    c = app.test_client()
    assert svc.plugins.info("live")["source"] == "dev"
    c.post("/api/plugin-host/live/enable")
    assert c.get("/api/plugins/live/v").get_json() == {"v": 1}
    assert svc.plugins.check_changes() == []
    code = dev / "live" / "plugin.py"
    code.write_text("def activate(plugin):\n    plugin.route('/v')(lambda: {'v': 222})\n")
    later = time.time() + 5
    os.utime(code, (later, later))
    assert svc.plugins.check_changes() == ["live"]
    assert c.get("/api/plugins/live/v").get_json() == {"v": 222}


def test_stopping_and_restarting_dev_host_stops_its_watcher(tmp_path):
    from backend.config import Config
    from backend.plugins.host import PluginHost
    from tests.plugins.conftest import write_plugin

    cfg = Config.from_env(tmp_path / "data")
    cfg.dev_plugins_dir = tmp_path / "dev"
    write_plugin(cfg.dev_plugins_dir, "live")
    host = PluginHost(cfg)
    try:
        host.start()
        watcher = host._watcher
        assert watcher and watcher.is_alive()
        host.stop()
        assert not watcher.is_alive()
        host.start()
        assert host._watcher is not watcher and host._watcher.is_alive()
    finally:
        host.stop()


def test_public_markdown_sdk_keeps_host_module_identity(client, svc, make_plugin):
    from backend.config import APP_ROOT

    folder = make_plugin("sdk-reader", frontend="frontend.js")
    # Relative source imports are rewritten to the public host module in the served bundle.
    source = APP_ROOT / "frontend" / "src" / "plugins" / "markdown.js"
    import os

    relative = os.path.relpath(source, folder).replace(os.sep, "/")
    (folder / "frontend.ts").write_text(
        f'import {{ renderTaskBlock }} from "{relative}"; export function activate() {{ return renderTaskBlock([], {{view:"list", group:"none", completed:"hide"}}); }}'
    )
    import json

    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["frontend"] = "frontend.ts"
    (folder / "manifest.json").write_text(json.dumps(manifest))
    svc.plugins.enable("sdk-reader")
    js = client.get("/api/plugin-files/sdk-reader/__build__/main.js")
    assert js.status_code == 200
    assert b'"/js/plugins/markdown.js"' in js.data
    assert b"renderDefaults" not in js.data and b"function createRenderer" not in js.data


def test_mcp_host_does_not_require_frontend_bundling(svc, make_plugin, monkeypatch):
    from backend.plugins.host import PluginHost
    from backend.plugins.library import LibraryReader

    folder = make_plugin(
        "mcp-ui",
        """
        def activate(p):
            @p.mcp_tool("Read", name="read")
            def read(): return {"ok": True}
    """,
        frontend="frontend.ts",
        permissions={"mcp": True},
    )
    (folder / "frontend.ts").write_text("export function activate() {}")
    svc.plugins._update_state("mcp-ui", enabled=True)
    monkeypatch.setattr("backend.plugins.build.bundle", lambda *a, **k: pytest.fail("MCP tried to build a frontend"))
    host = PluginHost(svc.config, library=LibraryReader(svc), services=svc)
    try:
        host.start("mcp")
        assert host.info("mcp-ui")["running"]
        assert next(t for t in host.mcp_tools() if t.name == "mcp-ui__read").fn() == {"ok": True}
    finally:
        host.stop()
