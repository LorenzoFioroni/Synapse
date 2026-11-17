"""The companion's settings, native messaging host, browser registration, window, autostart and
self-update (synapse_companion/), against a live test server. Nothing here touches the user's own
folders, browsers or network: the environment points at a temporary directory."""

import base64
import http.client
import io
import json
import os
import re
import stat
import struct
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from backend import connector
from backend.companion import bundle
from backend.online.http import LookupFailed
from synapse_companion import autostart, cli, common, extension, native, providers, remote, runner, service, tray, ui
from synapse_companion.service import Service
from tests.conftest import make_pdf

KEY = "bm90LWEtcmVhbC1rZXk="  # a fixed public key: the extension's id is derived from it
KEY_ID = "pkhhnhlnlckogeplnciknoepehfofmhh"
CODEX_ON = {"providers": {"codex": {"enabled": True}}}
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="uses the Linux desktop's folders")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Never the real user's folders: companion files, browsers, menus and autostart entries."""
    user = tmp_path / "user"
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(user / "data"))
    monkeypatch.setenv("SYNAPSE_COMPANION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SYNAPSE_COMPANION_NO_WINDOW", "1")
    monkeypatch.setattr(
        extension,
        "BROWSER_DIRS",
        {"Chrome": user / "config" / "google-chrome", "Chromium": user / "config" / "chromium"},
    )


@pytest.fixture
def live(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(5)


@pytest.fixture
def token(svc):
    return svc.tokens.create("test", scope="companion")[1]


def connect(live, token, name="Test PC", profile="default", settings=None):
    """A connected profile, without starting the companion."""
    svc = Service(profile)
    svc._start_companion = lambda profile: None  # type: ignore[method-assign]
    svc.connect(live, token, name)
    if settings is not None:
        svc.set_settings(settings)
    return svc


# ---------------------------------------------------------------------- settings


def test_settings_default_to_claude_on_codex_off():
    settings = common.normal_settings(None)
    assert settings == common.DEFAULT_SETTINGS
    assert settings["providers"]["claude"]["enabled"] and not settings["providers"]["codex"]["enabled"]
    assert settings["max_running"] == 2 and settings["extension_updates"] is True
    settings["providers"]["claude"]["enabled"] = False  # a copy: the defaults are never changed
    assert common.DEFAULT_SETTINGS["providers"]["claude"]["enabled"] is True


@pytest.mark.parametrize("junk", [[], "x", 5, {"providers": []}, {"providers": {"claude": "no"}}])
def test_settings_of_the_wrong_shape_fall_back_to_the_defaults(junk):
    assert common.normal_settings(junk) == common.DEFAULT_SETTINGS


def test_settings_keep_what_is_valid_and_drop_the_rest():
    got = common.normal_settings(
        {
            "junk": 1,
            "providers": {
                "claude": {"enabled": "yes", "models": "opus", "efforts": [1, 2]},  # wrong types: defaults
                "codex": {"enabled": True, "models": ["a", "a", "b"], "efforts": [], "extra": 1},
                "evil": {"enabled": True},
            },
            "max_running": 1,
            "extension_updates": False,
        }
    )
    assert got["providers"]["claude"] == {"enabled": True, "models": None, "efforts": None}
    assert got["providers"]["codex"] == {"enabled": True, "models": ["a", "b"], "efforts": []}
    assert set(got) == {"providers", "max_running", "extension_updates"} and set(got["providers"]) == {
        "claude",
        "codex",
    }
    assert got["max_running"] == 1 and got["extension_updates"] is False


@pytest.mark.parametrize("value", [0, 3, -1, True, 1.5, "2", None])
def test_max_running_stays_within_the_programs_own_limit(value):
    assert common.normal_settings({"max_running": value})["max_running"] == 2
    assert common.MAX_RUNNING == 2


def test_a_long_choice_list_is_cut():
    models = common.normal_settings({"providers": {"claude": {"models": [str(i) for i in range(100)]}}})
    assert len(models["providers"]["claude"]["models"]) == 32


def test_the_profile_keeps_its_settings_across_connecting_again(live, token, tools):
    service_ = connect(live, token)
    service_.set_settings({**common.DEFAULT_SETTINGS, "extension_updates": False})
    connect(live, token, name="Renamed")
    profile = common.load_profile("default")
    assert profile["name"] == "Renamed" and profile["settings"]["extension_updates"] is False
    path = common.profile_path("default")
    path.write_text(json.dumps({k: v for k, v in json.loads(path.read_text()).items() if k != "settings"}))
    assert common.load_profile("default")["settings"] == common.DEFAULT_SETTINGS  # an older profile


def test_disconnect_removes_the_token(live, token, tools):
    service_ = connect(live, token)
    service_.disconnect()
    assert not common.profile_path("default").exists() and not service_.state()["connected"]


# ---------------------------------------------------------------------- what the companion offers

FAKE_TOOL = "#!/bin/sh\nexit 0\n"


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """Fake `claude` and `codex` on PATH; they are never run here."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        (bin_dir / name).write_text(FAKE_TOOL)
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def advert_ids(companion):
    return {p["id"]: p for p in companion._poll_body()["providers"]}


def test_the_poll_advertises_only_enabled_tools_and_the_chosen_models_and_efforts(live, token, tools):
    connect(live, token)
    companion = runner.Companion(common.load_profile("default"), "PC")
    assert list(advert_ids(companion)) == ["claude"]  # Codex is on PATH but off
    companion.update_settings(CODEX_ON)
    assert list(advert_ids(companion)) == ["claude", "codex"]
    companion.update_settings(
        {"providers": {"claude": {"models": ["opus", "nope"], "efforts": ["low"]}, "codex": {"enabled": True}}}
    )
    adverts = advert_ids(companion)
    assert [m["id"] for m in adverts["claude"]["models"]] == ["opus"] and adverts["claude"]["efforts"] == ["low"]
    assert adverts["codex"]["efforts"] == ["low", "medium", "high", "xhigh"]
    companion.update_settings({"providers": {"claude": {"enabled": False}}})
    assert advert_ids(companion) == {}
    body = companion._poll_body()
    assert body["client"] == companion.client and body["name"] == "PC" and body["running"] == []


def test_a_tool_that_is_not_installed_is_not_advertised_even_when_enabled(live, token, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    connect(live, token, settings=CODEX_ON)
    companion = runner.Companion(common.load_profile("default"), "PC")
    assert advert_ids(companion) == {}


def test_a_question_can_choose_only_among_what_the_user_allowed():
    claude = providers.Claude("claude")
    setting = {"enabled": True, "models": ["opus"], "efforts": ["low", "high"]}
    assert claude.choice({"model": "opus"}, "model", setting) == "opus"
    assert claude.choice({"model": "haiku"}, "model", setting) is None  # offered by the tool, not by the user
    assert claude.choice({"effort": "high"}, "effort", setting) == "high"
    assert claude.choice({"effort": "max"}, "effort", setting) is None
    nothing = {"models": [], "efforts": []}
    assert claude.choice({"model": "opus"}, "model", nothing) is None and claude.offered(nothing, "efforts") == []
    assert claude.choice({"model": "haiku"}, "model", None) == "haiku"


def test_codex_is_off_by_default_and_on_through_the_settings(live, token, tools):
    service_ = connect(live, token)
    state = {p["id"]: p for p in service_.state()["providers"]}
    assert state["claude"]["enabled"] and state["claude"]["found"] and not state["codex"]["enabled"]
    assert state["codex"]["found"]  # present on this computer, but not used
    service_.set_settings(CODEX_ON)
    assert {p["id"]: p["enabled"] for p in service_.state()["providers"]} == {"claude": True, "codex": True}
    assert common.load_profile("default")["settings"]["providers"]["codex"]["enabled"] is True


def test_settings_reach_a_running_companion(live, token, tools):
    service_ = connect(live, token)
    companion = runner.Companion(common.load_profile("default"), "PC")
    service_.companion = companion
    service_.set_settings(CODEX_ON)
    assert list(advert_ids(companion)) == ["claude", "codex"]
    service_.companion = None


# ---------------------------------------------------------------------- native messaging host


def frames(raw: bytes) -> list[dict]:
    out, i = [], 0
    while i < len(raw):
        (size,) = struct.unpack("=I", raw[i : i + 4])
        out.append(json.loads(raw[i + 4 : i + 4 + size]))
        i += 4 + size
    return out


def framed(*messages) -> bytes:
    return b"".join(struct.pack("=I", len(d := json.dumps(m).encode())) + d for m in messages)


def ask_host(profile: str, message: dict) -> list[dict]:
    """The replies of a host to one request (the host is driven directly, as the reader loop would)."""
    out = io.BytesIO()
    native.Host(profile, io.BytesIO(), out).handle("1", message)
    return frames(out.getvalue())


def one(profile: str, message: dict) -> dict:
    (reply,) = ask_host(profile, message)
    assert reply["id"] == "1"
    return reply


def pdf_form(tmp_path, meta):
    pdf = make_pdf(tmp_path / "p.pdf", "hello")
    return [
        ["meta", json.dumps(meta)],
        [
            "file",
            {"filename": "paper.pdf", "type": "application/pdf", "data": base64.b64encode(pdf.read_bytes()).decode()},
        ],
    ]


def _offline():
    raise LookupFailed("offline")


@pytest.fixture
def host_server(live, token, monkeypatch):
    monkeypatch.setattr(connector, "lookup", lambda **k: _offline())
    connect(live, token)


def test_the_host_says_hello_and_records_that_the_extension_was_seen(host_server, live):
    reply = one("default", {"id": "1", "type": "hello", "version": "1.2.3", "build": "abcd"})
    assert reply == {"id": "1", "type": "hello", "server": live, "name": "Test PC"}
    seen = extension.last_seen("default")
    assert seen["version"] == "1.2.3" and seen["build"] == "abcd" and time.time() - seen["time"] < 10


def test_the_host_passes_connector_requests_with_the_companions_token(host_server, tmp_path):
    ping = one("default", {"type": "request", "method": "GET", "path": "/api/connector/ping"})
    assert ping["status"] == 200 and ping["more"] is False and json.loads(ping["body"])["app"] == "synapse"
    saved = one("default", {"type": "request", "method": "POST", "path": "/api/connector/save", "json": {"title": "T"}})
    assert saved["status"] == 201 and json.loads(saved["body"])["item"]["title"] == "T"
    duplicate = one(
        "default", {"type": "request", "method": "POST", "path": "/api/connector/save", "json": {"title": "T"}}
    )
    assert duplicate["status"] == 409 and "duplicate_of" in json.loads(duplicate["body"])  # the server's own answer
    form = pdf_form(tmp_path, {"title": "With PDF", "page_url": "https://x.org/a"})
    sent = one("default", {"type": "request", "method": "POST", "path": "/api/connector/save", "form": form})
    body = json.loads(sent["body"])
    assert sent["status"] == 201 and body["pdf"] == "attached" and len(body["item"]["attachments"]) == 1
    query = one("default", {"type": "request", "method": "GET", "path": "/api/connector/missing-pdfs?limit=5"})
    assert query["status"] in (200, 400)


@pytest.mark.parametrize(
    "path",
    [
        "/api/companion/poll",
        "/api/companion/hello",
        "/mcp",
        "/api/items",
        "/api/connector",
        "/api/connector/../items",
        "/api/connector/a/../../items",
        "/api/connector/%2e%2e/items",
        "/api/connector/ping#x",
        "/api/connector/ping?x=<script>",
        "/api/connector/ping\n",
        "//api/connector/ping",
        "http://evil.example/api/connector/ping",
        "api/connector/ping",
        "",
        None,
        5,
    ],
)
def test_the_host_refuses_everything_but_connector_paths(host_server, monkeypatch, path):
    forwarded = []
    monkeypatch.setattr(remote.Server, "forward", lambda self, *a, **k: forwarded.append(a) or (200, b"{}"))
    reply = one("default", {"type": "request", "method": "GET", "path": path})
    assert reply["type"] == "error" and "library requests only" in reply["message"] and forwarded == []


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "get", "", None, ["GET"]])
def test_the_host_passes_only_get_and_post(host_server, monkeypatch, method):
    forwarded = []
    monkeypatch.setattr(remote.Server, "forward", lambda self, *a, **k: forwarded.append(a) or (200, b"{}"))
    reply = one("default", {"type": "request", "method": method, "path": "/api/connector/ping"})
    assert reply["type"] == "error" and forwarded == []


def test_the_host_refuses_unknown_messages_and_bad_forms(host_server):
    assert one("default", {"type": "shell"})["message"] == "Unknown request."
    post = {"type": "request", "method": "POST", "path": "/api/connector/save"}
    for form in ("x", [["a"]], [[1, "x"]], [["a", 5]], [["a", {"data": "!!not base64!!"}]], [["a", "x"]] * 51):
        assert one("default", {**post, "form": form})["type"] == "error"


def test_form_values_cannot_inject_headers():
    data, content_type = native.multipart(
        [['na"me\r\nX-Evil: 1', "v"], ["file", {"filename": 'a"\r\nX-Evil: 2', "type": "bad type\r\nX: y", "data": ""}]]
    )
    assert b"\r\nX-Evil" not in data and b"\r\nX: y" not in data and b"application/octet-stream" in data
    boundary = content_type.split("boundary=")[1]
    assert data.endswith(f"--{boundary}--\r\n".encode())


def test_a_long_answer_comes_in_parts(host_server, monkeypatch):
    monkeypatch.setattr(native, "CHUNK", 60)
    replies = ask_host("default", {"type": "request", "method": "GET", "path": "/api/connector/ping"})
    assert len(replies) > 2 and [r["more"] for r in replies] == [True] * (len(replies) - 1) + [False]
    assert all(r["id"] == "1" and r["status"] == 200 and len(r["body"]) <= 60 for r in replies)
    assert json.loads("".join(r["body"] for r in replies))["app"] == "synapse"


def test_an_empty_answer_is_one_part(host_server, monkeypatch):
    monkeypatch.setattr(remote.Server, "forward", lambda self, *a, **k: (204, b""))
    assert one("default", {"type": "request", "method": "GET", "path": "/api/connector/ping"}) == {
        "id": "1",
        "type": "response",
        "status": 204,
        "body": "",
        "more": False,
    }


def test_the_host_says_when_it_is_not_connected():
    for message in ({"type": "hello"}, {"type": "request", "method": "GET", "path": "/api/connector/ping"}):
        reply = one("default", message)
        assert reply["type"] == "error" and reply["message"] == native.NOT_CONNECTED


def test_the_host_says_when_the_library_cannot_be_reached(tmp_path):
    common.save_profile("default", {"server": "http://127.0.0.1:9", "token": "t", "client": "c", "name": "PC"})
    reply = one("default", {"type": "request", "method": "GET", "path": "/api/connector/ping"})
    assert reply["type"] == "error" and "cannot reach the library" in reply["message"]


def test_the_host_reads_framed_messages_until_the_browser_closes(host_server, live):
    stdin = io.BytesIO(
        framed(
            {"id": "a", "type": "hello"},
            {"id": 5, "type": "hello"},  # no usable id: ignored
            {"id": "b", "type": "request", "method": "GET", "path": "/api/connector/ping"},
        )
        + b"\x01"  # a cut-off frame header ends the connection
    )
    out = io.BytesIO()
    native.Host("default", stdin, out).serve()
    deadline = time.time() + 10
    while time.time() < deadline and len(frames(out.getvalue())) < 2:
        time.sleep(0.05)
    by_id = {r["id"]: r for r in frames(out.getvalue())}
    assert set(by_id) == {"a", "b"} and by_id["a"]["type"] == "hello" and by_id["b"]["status"] == 200


def test_the_host_does_not_read_oversized_or_broken_messages():
    with pytest.raises(common.Fail, match="too large"):
        native.Host("d", io.BytesIO(struct.pack("=I", native.MAX_IN + 1)), io.BytesIO()).read()
    assert native.Host("d", io.BytesIO(struct.pack("=I", 10) + b"abc"), io.BytesIO()).read() is None
    assert native.Host("d", io.BytesIO(framed("text")), io.BytesIO()).read() == {}
    assert native.Host("d", io.BytesIO(struct.pack("=I", 3) + b"{{{"), io.BytesIO()).read() == {}


def test_the_host_is_started_with_the_profile_from_the_launcher(tools):
    from synapse_companion import cli

    seen = []
    original = native.main
    native.main = lambda profile: seen.append(profile) or 0  # type: ignore[assignment]
    try:
        assert cli.main(["native-host", "--profile", "work", "chrome-extension://abc/"]) == 0
        assert cli.main(["native-host", "chrome-extension://abc/"]) == 0
    finally:
        native.main = original
    assert seen == ["work", "default"]


# ---------------------------------------------------------------------- browser registration


def test_extension_id_is_derived_from_the_manifest_key(tmp_path):
    folder = tmp_path / "ext"
    folder.mkdir()
    assert extension.extension_id(folder) is None
    (folder / "manifest.json").write_text(json.dumps({"key": KEY}))
    assert extension.extension_id(folder) == KEY_ID
    for bad in ({}, {"key": 5}, {"key": "***not base64***"}):
        (folder / "manifest.json").write_text(json.dumps(bad))
        assert extension.extension_id(folder) is None
    (folder / "manifest.json").write_text("not json")
    assert extension.extension_id(folder) is None


def test_the_shipped_extension_has_a_valid_id():
    assert re.fullmatch(r"[a-p]{32}", extension.extension_id(bundle.EXTENSION_DIR) or "")


@linux_only
def test_register_host_writes_a_launcher_and_a_manifest_for_installed_browsers(tmp_path):
    chrome = tmp_path / "user" / "config" / "google-chrome"
    chrome.mkdir(parents=True)  # Chromium is not installed
    assert extension.registered_browsers(KEY_ID) == []
    assert extension.register_host("work", KEY_ID) == ["Chrome"]
    manifest = chrome / "NativeMessagingHosts" / "org.synapse.companion.json"
    data = json.loads(manifest.read_text())
    launcher = common.home() / "native-host"
    assert data == {
        "name": "org.synapse.companion",
        "description": data["description"],
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{KEY_ID}/"],
    }
    assert not (tmp_path / "user" / "config" / "chromium").exists()
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o700
    text = launcher.read_text()
    assert (
        text.startswith("#!/bin/sh\nexec ") and "'native-host' '--profile' 'work'" in text and text.endswith(' "$@"\n')
    )
    assert extension.registered_browsers(KEY_ID) == ["Chrome"]
    assert extension.registered_browsers("a" * 32) == [] and extension.registered_browsers(None) == []
    (tmp_path / "user" / "config" / "chromium").mkdir()
    assert extension.register_host("work", KEY_ID) == ["Chrome", "Chromium"]  # a browser installed later


@linux_only
def test_the_launcher_quotes_its_paths_and_passes_the_origin_on(tmp_path, monkeypatch):
    tricky = tmp_path / "it's a dir"
    tricky.mkdir()
    fake_python = tricky / "python"
    fake_python.write_text('#!/bin/sh\nfor a in "$@"; do echo "[$a]"; done\n')
    fake_python.chmod(0o755)
    monkeypatch.setattr(
        extension, "command", lambda *args, **kw: [str(fake_python), str(tricky / "my companion.pyz"), *args]
    )
    extension.register_host("work", KEY_ID)
    out = subprocess.run(
        [str(common.home() / "native-host"), f"chrome-extension://{KEY_ID}/"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    assert out[:4] == [f"[{tricky / 'my companion.pyz'}]", "[native-host]", "[--profile]", "[work]"]
    assert out[4] == f"[chrome-extension://{KEY_ID}/]"


def test_the_manifest_of_a_host_names_only_the_extension(tmp_path):
    folder = tmp_path / "hosts"
    path = extension.write_host_manifest(folder, KEY_ID, tmp_path / "launcher")
    assert json.loads(path.read_text())["allowed_origins"] == [f"chrome-extension://{KEY_ID}/"]
    assert path.name == "org.synapse.companion.json"


# ---------------------------------------------------------------------- the window


class Browser:
    """A client of the window's HTTP server that sets its own Host, Origin and Content-Type."""

    def __init__(self, window):
        self.window = window

    def call(self, method, path, body=None, *, key=None, host=None, origin=None, ctype="application/json", raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.window.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.window.port}"}
        if ctype:
            headers["Content-Type"] = ctype
        if key:
            headers["X-Companion-Key"] = key
        if origin:
            headers["Origin"] = origin
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(payload), resp
        except ValueError:
            return resp.status, payload, resp


@pytest.fixture
def window(live, token, tools):
    svc = Service("default")
    win = ui.Window(svc)
    win.start()
    yield win
    win.stop()
    svc.quit()
    win.httpd.server_close()


@pytest.fixture
def api(window):
    return Browser(window)


def session(api):
    ticket = api.window.ticket()
    status, body, _ = api.call("POST", "/api/session", {"ticket": ticket})
    assert status == 200
    return body["key"]


def test_the_window_file_holds_the_port_and_a_private_key(window):
    path = ui.window_file("default")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    info = json.loads(path.read_text())
    assert info["port"] == window.port and info["key"] == window.key and len(window.key) >= 40
    assert window.origin == f"http://127.0.0.1:{window.port}"


def test_a_ticket_is_exchanged_for_the_key_once(api):
    ticket = api.window.ticket()
    assert api.call("POST", "/api/session", {"ticket": ticket})[:2] == (200, {"key": api.window.key})
    status, body, _ = api.call("POST", "/api/session", {"ticket": ticket})
    assert status == 403 and "expired" in body["error"]
    for bad in ("", "nope", None, 5, ["x"]):
        assert api.call("POST", "/api/session", {"ticket": bad})[0] == 403
    assert api.call("POST", "/api/session", {})[0] == 403


def test_a_ticket_expires(api, monkeypatch):
    monkeypatch.setattr(ui, "TICKET_SECONDS", 0)
    ticket = api.window.ticket()
    time.sleep(0.01)
    assert api.call("POST", "/api/session", {"ticket": ticket})[0] == 403


def test_old_tickets_are_swept(api, monkeypatch):
    monkeypatch.setattr(ui, "TICKET_SECONDS", 0)
    for _ in range(5):
        api.window.ticket()
    monkeypatch.setattr(ui, "TICKET_SECONDS", 60)
    api.window.ticket()
    assert len(api.window.tickets) == 1


def test_the_api_needs_the_key(api):
    key = session(api)
    assert api.call("GET", "/api/state")[0] == 403
    assert api.call("GET", "/api/state", key="wrong")[0] == 403
    assert api.call("POST", "/api/disconnect", {})[0] == 403
    assert api.call("POST", "/api/settings", {"settings": {}}, key="wrong")[0] == 403
    assert api.call("GET", "/api/state", key=key)[0] == 200


def test_requests_for_another_host_are_refused(api):
    key = session(api)
    for host in ("evil.example", f"localhost:{api.window.port}", "127.0.0.1", f"127.0.0.1:{api.window.port + 1}"):
        assert api.call("GET", "/api/state", key=key, host=host)[0] == 403
        assert api.call("GET", "/", host=host)[0] == 403
        assert api.call("POST", "/api/session", {"ticket": api.window.ticket()}, host=host)[0] == 403


def test_posts_from_other_origins_or_without_json_are_refused(api):
    key = session(api)
    off = {"on": False}  # a harmless change
    assert api.call("POST", "/api/autostart", off, key=key, origin="http://evil.example")[0] == 403
    assert api.call("POST", "/api/autostart", off, key=key, origin="null")[0] == 403
    assert api.call("POST", "/api/autostart", off, key=key, ctype="text/plain")[0] == 403
    assert api.call("POST", "/api/autostart", off, key=key, ctype="application/x-www-form-urlencoded")[0] == 403
    assert api.call("POST", "/api/autostart", off, key=key, ctype=None)[0] == 403
    assert api.call("POST", "/api/session", {"ticket": api.window.ticket()}, origin="http://evil.example")[0] == 403
    assert api.call("POST", "/api/autostart", off, key=key, origin=api.window.origin)[0] == 200  # its own page
    assert api.call("POST", "/api/autostart", off, key=key)[0] == 200  # no Origin


def test_bad_bodies_are_refused(api):
    key = session(api)
    assert api.call("POST", "/api/settings", key=key, raw=b"[]")[0] == 400
    assert api.call("POST", "/api/settings", key=key, raw=b"not json")[0] == 400
    assert api.call("POST", "/api/settings", key=key, raw=b"x" * (ui.MAX_BODY + 1))[0] == 413
    assert api.call("POST", "/api/nothing", {}, key=key)[0] == 400  # unknown actions are refused, not run
    assert api.call("GET", "/api/nothing")[0] == 404


def test_the_page_is_served_with_a_restrictive_policy(api):
    status, body, resp = api.call("GET", "/")
    assert status == 200 and b"<html" in body.lower()
    assert "default-src 'self'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("Cache-Control") == "no-store" and resp.getheader("X-Content-Type-Options") == "nosniff"
    for path in ("/app.js", "/app.css", "/icon.svg"):
        assert api.call("GET", path)[0] == 200
    assert api.call("GET", "/../ui.py")[0] == 404 and api.call("GET", "/index.html")[0] == 404


def test_connecting_through_the_window(api, live, token, svc):
    key = session(api)
    state = api.call("GET", "/api/state", key=key)[1]
    assert state["connected"] is False and state["connection"]["state"] == "none"
    for payload, message in (
        ({"server": "http://synapse.example.com", "token": "x", "name": "n"}, "https"),
        ({"server": live, "token": "wrong", "name": "n"}, "companion token"),
        ({"server": live, "token": "", "name": "n"}, "token"),
        ({"server": 5, "token": 5}, "address"),
    ):
        status, body, _ = api.call("POST", "/api/connect", payload, key=key)
        assert status == 400 and message in body["error"]
    assert not common.profile_path("default").exists()
    assert api.call("POST", "/api/connect", {"server": live, "token": token, "name": "Window PC"}, key=key)[:2] == (
        200,
        {"ok": True},
    )
    state = api.call("GET", "/api/state", key=key)[1]
    assert state["connected"] and state["server"] == live and state["name"] == "Window PC"
    for _ in range(100):  # the companion polls the server
        if svc.companions.status():
            break
        time.sleep(0.05)
    assert svc.companions.status()[0]["name"] == "Window PC"
    assert api.call("POST", "/api/disconnect", {}, key=key)[0] == 200
    assert api.call("GET", "/api/state", key=key)[1]["connected"] is False


def test_settings_make_a_round_trip_through_the_window(api, live, token):
    key = session(api)
    connect(live, token)
    settings = {
        "providers": {
            "claude": {"enabled": True, "models": ["opus"], "efforts": None},
            "codex": {"enabled": True, "efforts": ["low", "bogus"]},
        },
        "max_running": 1,
        "extension_updates": False,
        "unknown": True,
    }
    assert api.call("POST", "/api/settings", {"settings": settings}, key=key)[0] == 200
    state = api.call("GET", "/api/state", key=key)[1]
    assert state["settings"] == {
        "providers": {
            "claude": {"enabled": True, "models": ["opus"], "efforts": None},
            "codex": {"enabled": True, "models": None, "efforts": ["low", "bogus"]},
        },
        "max_running": 1,
        "extension_updates": False,
    }
    by_id = {p["id"]: p for p in state["providers"]}
    assert [m["id"] for m in by_id["claude"]["models"] if m["on"]] == ["opus"]
    assert [e["id"] for e in by_id["codex"]["efforts"] if e["on"]] == ["low"]  # only what the tool offers
    assert common.load_profile("default")["settings"] == state["settings"]
    assert api.call("POST", "/api/settings", {"settings": "junk"}, key=key)[0] == 200  # back to the defaults
    assert api.call("GET", "/api/state", key=key)[1]["settings"] == common.DEFAULT_SETTINGS


def test_settings_need_a_connection(api):
    key = session(api)
    status, body, _ = api.call("POST", "/api/settings", {"settings": {}}, key=key)
    assert status == 400 and "Connect" in body["error"]


def test_the_state_has_what_the_window_shows(api, live, token):
    key = session(api)
    connect(live, token)
    state = api.call("GET", "/api/state", key=key)[1]
    assert set(state) == {
        "profile",
        "connected",
        "server",
        "name",
        "connection",
        "settings",
        "max_running",
        "providers",
        "running",
        "autostart",
        "autostart_where",
        "program",
        "update",
        "log",
        "extension",
        "tray",
    }
    assert state["tray"]["status"] == "off" and "PySide6" in state["tray"]["install"]
    assert state["profile"] == "default" and state["max_running"] == 2 and state["running"] == []
    assert set(state["connection"]) == {"state", "message"} and isinstance(state["log"], list)
    claude = next(p for p in state["providers"] if p["id"] == "claude")
    assert set(claude) == {"id", "label", "found", "enabled", "models", "efforts"}
    assert claude["models"][0] == {"id": "fable", "label": "Fable", "on": True}
    assert claude["efforts"][0] == {"id": "low", "on": True}
    assert state["extension"]["installed"] is False and state["extension"]["browsers"] == []
    assert state["update"] is False and state["autostart"] is False
    assert api.call("POST", "/api/autostart", {"on": "yes"}, key=key)[0] == 200  # only a real true turns it on
    assert api.call("GET", "/api/state", key=key)[1]["autostart"] is False


def test_asking_a_running_companion_for_a_ticket(api, window):
    answer = ui.running_ticket("default")
    assert answer["port"] == window.port and window.redeem(answer["ticket"])
    window.stop()
    assert ui.running_ticket("default") is None  # the file is gone with the window
    assert ui.running_ticket("other") is None


def test_a_ticket_needs_proof_of_the_key_never_the_key(api, window):
    key = session(api)
    assert api.call("POST", "/api/ticket", {}, key=key)[0] == 403  # the key alone is not how
    nonce = "n" * 32
    assert api.call("POST", "/api/ticket", {"nonce": nonce, "proof": "0" * 64})[0] == 403
    status, body, _ = api.call("POST", "/api/ticket", {"nonce": nonce, "proof": ui.proof(key, "ask", nonce)})
    assert status == 200 and body["proof"] == ui.proof(key, "answer", nonce, body["ticket"])


def test_a_stale_window_file_never_trusts_or_feeds_another_program(tmp_path, monkeypatch):
    """After a crash another program may hold the old port: it never sees the key, and a made-up
    answer does not count as a running companion."""
    import http.server

    seen = []

    class Impostor(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"ticket": "t", "proof": "x"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Impostor)
    threading.Thread(target=httpd.handle_request, daemon=True).start()
    key = "secret-window-key-0123456789"
    common.write_private(ui.window_file("default"), json.dumps({"port": httpd.server_address[1], "key": key}))
    assert ui.running_ticket("default") is None
    headers, body = seen[0]
    assert key not in json.dumps(headers) and key.encode() not in body
    httpd.server_close()


def test_the_window_opens_through_a_private_file(tmp_path, monkeypatch):
    launched = []
    monkeypatch.delenv("SYNAPSE_COMPANION_NO_WINDOW", raising=False)
    monkeypatch.setattr(ui, "_chromium", lambda: "/usr/bin/fake-browser")
    monkeypatch.setattr(ui.subprocess, "Popen", lambda args, **kw: launched.append(args))
    ui.open_window("http://127.0.0.1:1234/?ticket=SECRET", "default")
    assert launched and not any("SECRET" in a for a in launched[0])
    redirect = ui.home() / "default.open.html"
    assert launched[0][1] == f"--app={redirect.as_uri()}" and "SECRET" in redirect.read_text()
    assert redirect.stat().st_mode & 0o077 == 0 and ui.home().stat().st_mode & 0o077 == 0


# ---------------------------------------------------------------------- the tray icon


def test_the_tray_line_sums_up_the_state():
    assert tray.summary({"connection": {"state": "none"}, "server": "", "running": []}) == "Not connected"
    line = tray.summary(
        {"connection": {"state": "connected"}, "server": "https://synapse.example.com", "running": [{}, {}]}
    )
    assert line == "Connected · synapse.example.com · answering 2"


def test_no_tray_without_a_desktop_or_pyside(monkeypatch):
    proc = tray.TrayProcess(1, "k")
    proc.start()  # SYNAPSE_COMPANION_NO_WINDOW: tests and headless use
    assert proc.status == "off" and proc.proc is None
    monkeypatch.delenv("SYNAPSE_COMPANION_NO_WINDOW")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(tray.importlib.util, "find_spec", lambda name: None)
    proc.start()
    assert proc.status == "missing" and proc.proc is None
    assert any("PySide6-Essentials" in line for line in common.recent_log())


def test_the_install_hint_fits_the_python(monkeypatch):
    monkeypatch.setattr(tray.sys, "prefix", "/venv")
    monkeypatch.setattr(tray.sys, "base_prefix", "/usr")
    assert tray.install_hint().endswith(" -m pip install PySide6-Essentials")  # --user fails in a venv
    monkeypatch.setattr(tray.sys, "base_prefix", "/venv")
    assert tray.install_hint().endswith(" -m pip install --user PySide6-Essentials")


def _fake_icon(monkeypatch, tmp_path, code: str) -> Path:
    """The icon's process replaced by ``code``, which gets what the real one would on stdin."""
    monkeypatch.delenv("SYNAPSE_COMPANION_NO_WINDOW")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(tray.importlib.util, "find_spec", lambda name: object())
    out = tmp_path / "icon-stdin.json"
    script = f"import sys, os; open({str(out)!r}, 'w').write(sys.stdin.read()); {code}"
    monkeypatch.setattr(tray, "command", lambda *args, **kw: [sys.executable, "-c", script, *args])
    return out


def _wait(predicate) -> None:
    for _ in range(100):
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_the_icon_gets_the_key_on_stdin_never_in_its_command_line(monkeypatch, tmp_path):
    out = _fake_icon(monkeypatch, tmp_path, f"sys.exit({tray.NO_TRAY})")
    proc = tray.TrayProcess(4321, "the-window-key")
    proc.start()
    assert "the-window-key" not in json.dumps(proc.proc.args)
    _wait(lambda: proc.status != "starting")
    assert json.loads(out.read_text()) == {"port": 4321, "key": "the-window-key"}
    assert proc.status == "no-tray" and any("no system tray" in line for line in common.recent_log())


def test_a_crashing_icon_leaves_the_companion_running(monkeypatch, tmp_path):
    _fake_icon(monkeypatch, tmp_path, "sys.stderr.write('Could not load the Qt platform plugin\\n'); os.abort()")
    proc = tray.TrayProcess(1, "k")
    proc.start()
    _wait(lambda: proc.status != "starting")
    assert proc.status == "failed"
    assert any("tray icon stopped" in line and "Qt platform plugin" in line for line in common.recent_log())


def test_stopping_the_companion_ends_its_icon(monkeypatch, tmp_path):
    _fake_icon(monkeypatch, tmp_path, "import time; time.sleep(30)")
    common._recent.clear()
    proc = tray.TrayProcess(1, "k")
    proc.start()
    child = proc.proc
    proc.stop()
    assert child.wait(5) != 0 and proc.status == "off"
    time.sleep(0.2)
    assert not any("tray icon stopped" in line for line in common.recent_log())  # not a failure


def test_the_icon_reports_itself_and_opens_the_window(api):
    key = session(api)
    assert api.call("POST", "/api/tray", {"shown": True}, key="wrong")[0] == 403
    assert api.call("POST", "/api/tray", {"shown": True}, key=key)[0] == 200
    assert api.call("GET", "/api/state", key=key)[1]["tray"]["status"] == "shown"
    assert api.call("POST", "/api/open", {}, key=key)[0] == 200
    assert common.recent_log()[-1].endswith(f"Window: {api.window.origin}/")


def test_the_icon_process_needs_its_window_key(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert cli.main(["tray"]) == 2


def test_an_unknown_extension_action_is_refused(api):
    key = session(api)
    assert api.call("POST", "/api/extension", {"action": "format"}, key=key)[0] == 400


# ---------------------------------------------------------------------- start at login


@linux_only
def test_autostart_writes_and_removes_the_desktop_entries(tmp_path):
    icon = tmp_path / "icon.svg"
    assert not autostart.enabled("default")
    autostart.set_enabled("default", True, icon)
    start = tmp_path / "user" / "config" / "autostart" / "synapse-companion.desktop"
    menu = tmp_path / "user" / "data" / "applications" / "synapse-companion.desktop"
    assert autostart.enabled("default") and start.is_file() and menu.is_file()
    run = next(line for line in start.read_text().splitlines() if line.startswith("Exec="))
    assert '"run" "--profile" "default"' in run and f'"{sys.executable}"' in run
    assert "X-GNOME-Autostart-enabled=true" in start.read_text() and "NoDisplay=true" in start.read_text()
    shown = menu.read_text()
    assert '"open" "--profile" "default"' in shown and "NoDisplay" not in shown and f"Icon={icon}" in shown
    assert autostart.describe("default") == str(start)
    autostart.set_enabled("default", False)
    assert not autostart.enabled("default") and not start.exists()
    assert menu.is_file()  # the menu entry stays: it only opens the window
    autostart.set_enabled("default", False)  # nothing to remove is fine


@linux_only
def test_autostart_is_per_profile_and_quotes_special_characters(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "command", lambda *a, **k: ['/p "a" $x/python', "/q`z\\.pyz", *a])
    autostart.set_enabled("work", True)
    entry = tmp_path / "user" / "config" / "autostart" / "synapse-companion-work.desktop"
    exec_line = next(line for line in entry.read_text().splitlines() if line.startswith("Exec="))
    assert exec_line == 'Exec="/p \\"a\\" \\$x/python" "/q\\`z\\\\.pyz" "run" "--profile" "work"'
    assert autostart.enabled("work") and not autostart.enabled("default")


@linux_only
def test_the_service_turns_autostart_on_and_off(live, token, tools, tmp_path):
    service_ = connect(live, token)
    service_.set_autostart(True)
    state = service_.state()
    assert state["autostart"] is True and state["autostart_where"].endswith("synapse-companion.desktop")
    assert (
        "Icon=" + str(common.home() / "icon.svg")
        in (tmp_path / "user" / "data" / "applications" / "synapse-companion.desktop").read_text()
    )
    assert b"<svg" in (common.home() / "icon.svg").read_bytes()
    service_.set_autostart(False)
    assert service_.state()["autostart"] is False


# ---------------------------------------------------------------------- the program itself


@pytest.fixture
def pyz(tmp_path):
    build, data = bundle.script()
    path = tmp_path / "pack" / "synapse_companion.pyz"
    path.parent.mkdir()
    path.write_bytes(data)
    path.chmod(0o755)
    return build, path


def test_the_served_program_is_a_runnable_pyz(pyz, tmp_path):
    _, path = pyz
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert "__main__.py" in names and "synapse_companion/cli.py" in names and "synapse_companion/static/app.js" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    run = subprocess.run(
        [sys.executable, str(path), "--help"], capture_output=True, text=True, cwd=tmp_path, timeout=30
    )
    assert run.returncode == 0 and "connect" in run.stdout and "run" in run.stdout
    # from outside the checkout it runs from the zip alone, and knows its own path
    status = subprocess.run(
        [sys.executable, str(path), "status"], capture_output=True, text=True, cwd=tmp_path, timeout=30
    )
    assert status.returncode == 0 and f"python {path}" in status.stdout


def test_the_build_of_the_program_matches_the_servers(pyz, monkeypatch):
    build, path = pyz
    monkeypatch.setattr(service, "program_path", lambda: path)
    assert service.own_build() == build == bundle.script()[0]
    path.write_bytes(path.read_bytes() + b"x")
    assert service.own_build() != build
    monkeypatch.setattr(service, "program_path", lambda: Path(service.__file__).parent)
    assert service.own_build() == ""  # a checkout is never out of date


def test_the_state_says_when_the_program_is_out_of_date(live, token, pyz, monkeypatch):
    build, path = pyz
    monkeypatch.setattr(common, "program_path", lambda: path)
    monkeypatch.setattr(service, "program_path", lambda: path)
    service_ = connect(live, token)
    service_.check()
    assert service_.hello["script"] == build and service_.state()["update"] is False
    service_.hello = {"script": "0" * 16}
    assert service_.state()["update"] is True and service_.state()["program"] == str(path)


def test_update_is_refused_outside_a_packaged_program(live, token):
    service_ = connect(live, token)
    with pytest.raises(common.Fail, match="checkout"):
        service_.update_program()
    assert not service_.restart


def test_update_replaces_the_pyz_and_asks_to_restart(live, token, pyz, tmp_path, monkeypatch):
    build, path = pyz
    old = io.BytesIO()
    with zipfile.ZipFile(old, "w") as z:
        z.writestr("__main__.py", "print('old')")
    path.write_bytes(old.getvalue())
    monkeypatch.setattr(common, "program_path", lambda: path)
    monkeypatch.setattr(service, "program_path", lambda: path)
    service_ = connect(live, token)
    service_.update_program()
    assert path.read_bytes() == bundle.script()[1] and os.access(path, os.X_OK)
    assert service_.restart is True and service_.stopped.is_set()
    assert service.own_build() == build and [p.name for p in path.parent.iterdir()] == [path.name]


def test_update_refuses_a_download_that_is_not_the_companion(live, token, pyz, monkeypatch):
    _, path = pyz
    before = path.read_bytes()
    monkeypatch.setattr(common, "program_path", lambda: path)
    monkeypatch.setattr(service, "program_path", lambda: path)
    junk = io.BytesIO()
    with zipfile.ZipFile(junk, "w") as z:
        z.writestr("__main__.py", "x")
    service_ = connect(live, token)
    for data in (b"not a zip", junk.getvalue()):
        monkeypatch.setattr(remote.Server, "call", lambda self, *a, data=data, **k: data)
        with pytest.raises(common.Fail, match="not the Synapse companion"):
            service_.update_program()
        assert path.read_bytes() == before and not service_.restart

    def unreachable(self, *a, **k):
        raise remote.NetworkError("down")

    monkeypatch.setattr(remote.Server, "call", unreachable)
    with pytest.raises(common.Fail, match="Could not download"):
        service_.update_program()
    assert path.read_bytes() == before


def test_the_program_can_start_again_by_its_own_command(pyz, monkeypatch):
    _, path = pyz
    monkeypatch.setattr(common, "program_path", lambda: path)
    assert common.is_packaged() and common.command("run", "x")[1:] == [str(path), "run", "x"]
    assert common.python_exe() == sys.executable
    monkeypatch.undo()
    assert not common.is_packaged() and common.program_path().name == "synapse_companion"
