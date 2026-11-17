"""The companion package (synapse_companion/) against a live test server and fake AI tools."""

import http.server
import io
import json
import os
import stat
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from backend.companion import bundle, hub
from synapse_companion import cli, common, extension, providers, remote, runner
from synapse_companion.service import Service

ROOT = Path(__file__).resolve().parent.parent
# A fixed public key, so the extension has an id (Chrome derives it from the manifest's "key").
KEY = "bm90LWEtcmVhbC1rZXk="
CODEX_ON = {"providers": {"codex": {"enabled": True}}}

# A stand-in for `claude`: records how it was started and plays a scripted stream-json answer.
FAKE_CLAUDE = r"""#!{python}
import json, os, sys, time
log = json.load(open(os.environ["FAKE_LOG"])) if os.path.exists(os.environ["FAKE_LOG"]) else []
config = sys.argv[sys.argv.index("--mcp-config") + 1]
system = sys.argv[sys.argv.index("--append-system-prompt-file") + 1]
mode = os.environ.get("FAKE_MODE", "ok")
log.append({{
    "argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd(), "pid": os.getpid(),
    "config": open(config).read(), "config_mode": oct(os.stat(config).st_mode & 0o777), "config_path": config,
    "env_token": [k for k, v in os.environ.items() if "syn_companion_" in v],
    "system_path": system, "system_text": open(system).read(), "system_mode": oct(os.stat(system).st_mode & 0o777),
}})
json.dump(log, open(os.environ["FAKE_LOG"], "w"))
def out(o):
    print(json.dumps(o), flush=True)
tools = ["mcp__synapse__get_note", "mcp__synapse__search_library"]
if mode == "badtools":
    tools.append("Bash")
if mode == "gone":
    out({{"type": "result", "subtype": "error_during_execution", "is_error": True, "errors": ["No conversation found with session ID: x"]}})
    sys.exit(1)
out({{"type": "system", "subtype": "init", "tools": tools}})
if mode == "badtools":
    time.sleep(0.3)
    out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": "LEAK"}}}}}})
    time.sleep(30)
out({{"type": "stream_event", "event": {{"type": "content_block_start", "content_block": {{"type": "tool_use", "name": "mcp__synapse__get_note"}}}}}})
for word in ("Hel", "lo"):
    out({{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": word}}}}}})
if mode == "hang":
    time.sleep(60)
if mode == "fail":
    out({{"type": "result", "is_error": True, "result": "Not logged in"}})
else:
    out({{"type": "result", "is_error": False, "result": "Hello", "duration_ms": 1234, "total_cost_usd": 0.01,
         "usage": {{"input_tokens": 2, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7}}}})
"""

FAKE_CODEX = r"""#!{python}
import json, os, sys
log = json.load(open(os.environ["FAKE_LOG"])) if os.path.exists(os.environ["FAKE_LOG"]) else []
log.append({{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "env_token": os.environ.get("SYNAPSE_MCP_TOKEN"),
    "env_companion": [k for k, v in os.environ.items() if "syn_companion_" in v]}})
json.dump(log, open(os.environ["FAKE_LOG"], "w"))
for o in (
    {{"type": "thread.started", "thread_id": "thread-1"}},
    {{"type": "item.started", "item": {{"type": "mcp_tool_call", "tool": "get_note"}}}},
    {{"type": "item.completed", "item": {{"type": "agent_message", "text": "From Codex"}}}},
    {{"type": "turn.completed", "usage": {{"input_tokens": 11, "output_tokens": 3, "cached_input_tokens": 5}}}},
):
    print(json.dumps(o), flush=True)
"""


@pytest.fixture
def tools(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        path = bin_dir / name
        path.write_text(text.format(python=sys.executable))
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "log.json"))
    # Never the real user's folders: the companion's files, browsers, menus and autostart entries.
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user" / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user" / "data"))
    monkeypatch.setenv("SYNAPSE_COMPANION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SYNAPSE_COMPANION_NO_WINDOW", "1")
    monkeypatch.setattr(extension, "BROWSER_DIRS", {"Chrome": tmp_path / "user" / "config" / "google-chrome"})
    monkeypatch.setattr(hub, "POLL_SECONDS", 0.3)
    return tmp_path / "log.json"


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
    """A connected profile, without starting the companion (tests start what they need)."""
    service = Service(profile)
    service._start_companion = lambda profile: None  # type: ignore[method-assign]
    service.connect(live, token, name)
    if settings is not None:
        service.set_settings(settings)
    return service


class Running:
    """A companion running in a thread."""

    def __init__(self, live, token, settings=None):
        connect(live, token, settings=settings)
        self.companion = runner.Companion(common.load_profile("default"), "Test PC")
        self.thread = threading.Thread(target=self.companion.run, daemon=True)
        self.thread.start()

    def close(self):
        self.companion.stop()
        self.thread.join(10)
        assert not self.thread.is_alive()


@pytest.fixture
def running(live, token, tools):
    started = []

    def start(settings=None):
        started.append(Running(live, token, settings))
        return started[-1]

    yield start
    for r in started:
        r.close()


def ask(client, svc, prompt="Question?", **extra):
    for _ in range(500):  # a companion started as a subprocess can take seconds on a slow runner
        if svc.companions.status():
            break
        time.sleep(0.02)
    r = client.post("/api/ai/ask", json={"prompt": prompt, **extra})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def collect(client, job, until=("done", "error", "cancelled"), timeout=15):
    events, after, deadline = [], 0, time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen = client.get(f"/api/ai/jobs/{job}?after={after}").get_json()
        events += seen["events"]
        after = seen["next"]
        if seen["state"] in until:
            return seen["state"], events
    raise AssertionError(f"job did not finish: {events}")


def text_of(events):
    return "".join(e["text"] for e in events if e["type"] == "text")


def calls(log):
    return json.loads(log.read_text())


# ---------------------------------------------------------------------- connect and status


def test_connect_stores_a_private_profile(live, token, tools, capsys, monkeypatch):
    monkeypatch.setattr(Service, "_start_companion", lambda self, profile: None)
    assert cli.main(["connect", live, "--token", token, "--name", "Test PC"]) == 0
    path = common.profile_path("default")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    profile = json.loads(path.read_text())
    assert profile["server"] == live and profile["token"] == token and profile["name"] == "Test PC"
    client_id = profile["client"]
    assert cli.main(["connect", live, "--token", token]) == 0  # again: this computer's identity and name stay
    again = json.loads(path.read_text())
    assert again["client"] == client_id and again["name"] == "Test PC"
    assert cli.main(["connect", live, "--token", token, "--profile", "other"]) == 0
    assert common.profile_path("other").exists()
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert live in out and "Companion: not running" in out and "Claude Code: on" in out and "Codex: off" in out


def test_connect_reads_the_token_from_the_environment(live, token, tools, monkeypatch):
    monkeypatch.setattr(Service, "_start_companion", lambda self, profile: None)
    monkeypatch.setenv(common.ENV_TOKEN, token)
    assert cli.main(["connect", live]) == 0
    assert common.load_profile("default")["token"] == token


def test_connect_refuses_unsafe_or_wrong_input(live, svc, tools, capsys, monkeypatch):
    monkeypatch.setattr(Service, "_start_companion", lambda self, profile: None)
    assert cli.main(["connect", "http://synapse.example.com", "--token", "x"]) == 1
    assert "https" in capsys.readouterr().err
    assert cli.main(["connect", "ftp://x", "--token", "x"]) == 1
    assert cli.main(["connect", live, "--token", "wrong"]) == 1
    assert "companion token" in capsys.readouterr().err
    assert cli.main(["connect", live, "--token", svc.tokens.create("m", scope="mcp")[1]]) == 1
    assert not common.profile_path("default").exists()
    with pytest.raises(SystemExit):  # argparse refuses a profile name that is a path
        cli.main(["connect", "https://synapse.example.com", "--token", "x", "--profile", "../bad"])
    assert cli.main(["status"]) == 0 and not list(common.home().glob("*.json"))


def test_redirects_never_carry_the_token(live, token, tools):
    seen = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        log_message = lambda *a: None  # noqa: E731

    target = http.server.HTTPServer(("127.0.0.1", 0), Target)

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/")
            self.end_headers()

        log_message = lambda *a: None  # noqa: E731

    redirecting = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    for s in (target, redirecting):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        with pytest.raises(remote.ApiError, match="redirect"):
            remote.Server(f"http://127.0.0.1:{redirecting.server_port}", token).call("GET", "/x")
        with pytest.raises(remote.NetworkError, match="redirect"):  # and not for the extension's requests either
            remote.Server(f"http://127.0.0.1:{redirecting.server_port}", token).forward("GET", "/x", None, None)
    finally:
        target.shutdown()
        redirecting.shutdown()
    assert seen == []


# ---------------------------------------------------------------------- questions


def mcp_token_of(call):
    """The token in the AI tool's MCP config file."""
    header = json.loads(call["config"])["mcpServers"]["synapse"]["headers"]["Authorization"]
    assert header.startswith("Bearer ")
    return header.removeprefix("Bearer ")


def test_question_runs_claude_restricted_to_synapse_and_streams_the_answer(client, svc, running, tools, token, live):
    running()
    reply = ask(client, svc, "What is this?")
    state, events = collect(client, reply["job"])
    assert state == "done" and text_of(events) == "Hello"
    assert {"type": "tool", "name": "get_note"} in events
    (call,) = calls(tools)
    argv = call["argv"]
    pairs = {argv[i]: argv[i + 1] for i in range(len(argv) - 1)}
    assert pairs["--tools"] == "" and pairs["--allowedTools"] == "mcp__synapse"
    assert pairs["--output-format"] == "stream-json" and "--strict-mcp-config" in argv and "--print" in argv
    assert pairs["--setting-sources"] == "" and "--include-partial-messages" in argv
    assert "--session-id" in argv and "--resume" not in argv
    assert "--append-system-prompt" not in argv and "--disable-slash-commands" in argv
    # nothing the server sent is in argv: its system text is in a private file, the session id derived
    assert "Synapse" in call["system_text"] and call["system_mode"] == "0o600"
    assert call["system_text"] not in " ".join(argv) and reply["conversation"] not in " ".join(argv)
    assert not Path(call["system_path"]).exists()
    session = argv[argv.index("--session-id") + 1]
    assert session == str(uuid.uuid5(uuid.NAMESPACE_URL, f"{live}|{reply['conversation']}"))
    assert call["stdin"].startswith("What is this?")
    # The tool gets the question's own token, in the private config file only: never the companion's,
    # not in argv, not in the environment.
    mcp = mcp_token_of(call)
    assert mcp and mcp != token and token not in call["config"] and "syn_companion_" not in call["config"]
    assert mcp not in " ".join(argv) and token not in " ".join(argv) and call["env_token"] == []
    assert call["config_mode"] == "0o600"
    assert json.loads(call["config"])["mcpServers"]["synapse"]["url"].endswith("/mcp")
    assert not Path(call["config_path"]).exists()
    assert svc.companions.job_caller(mcp) is None  # the token died with the question
    # run from an empty private directory
    assert Path(call["cwd"]).name == "work-default" and not any(Path(call["cwd"]).iterdir())


def test_the_question_token_is_valid_while_it_runs_and_redacted(client, svc, running, tools, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    r = running()
    job = ask(client, svc)["job"]
    for _ in range(100):
        if calls(tools) if tools.exists() else None:
            break
        time.sleep(0.05)
    mcp = mcp_token_of(calls(tools)[0])
    assert svc.companions.job_caller(mcp) is not None
    assert mcp in r.companion.secrets and mcp not in r.companion.redact(f"token {mcp} leaked")
    client.post(f"/api/ai/jobs/{job}/cancel")
    for _ in range(100):
        if svc.companions.job_caller(mcp) is None:
            break
        time.sleep(0.05)
    assert svc.companions.job_caller(mcp) is None


def test_a_question_without_its_own_token_is_refused(client, svc, running, tools, monkeypatch):
    handout = hub.Companions._handout
    monkeypatch.setattr(hub.Companions, "_handout", lambda self, job: {**handout(self, job), "mcp_token": None})
    running()
    state, events = collect(client, ask(client, svc)["job"])
    assert state == "error" and "invalid question" in events[-1]["message"]
    assert not tools.exists()  # the AI tool was never started


def test_follow_up_resumes_the_same_session(client, svc, running, tools):
    running()
    first = ask(client, svc)
    collect(client, first["job"])
    second = ask(client, svc, "More?", conversation=first["conversation"])
    assert collect(client, second["job"])[0] == "done"
    one, two = calls(tools)
    session = one["argv"][one["argv"].index("--session-id") + 1]
    assert two["argv"][two["argv"].index("--resume") + 1] == session and "--session-id" not in two["argv"]


def test_unexpected_tool_aborts_the_job(client, svc, running, tools, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "badtools")
    running()
    state, events = collect(client, ask(client, svc)["job"])
    assert state == "error" and "other than Synapse's" in events[-1]["message"]
    assert "LEAK" not in text_of(events)
    pid = calls(tools)[0]["pid"]
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the AI tool was not stopped")


def test_tool_failure_is_reported(client, svc, running, tools, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    running()
    state, events = collect(client, ask(client, svc)["job"])
    assert state == "error" and events[-1] == {"type": "error", "message": "Not logged in"}


def test_cancel_stops_the_process(client, svc, running, tools, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    running()
    job = ask(client, svc)["job"]
    for _ in range(100):  # wait until it streams
        if svc.companions.watch(job, 0, timeout=0)["events"]:
            break
        time.sleep(0.05)
    assert client.post(f"/api/ai/jobs/{job}/cancel").get_json() == {"state": "cancelled"}
    pid = calls(tools)[0]["pid"]
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError("the AI tool kept running after cancel")


def test_stopping_the_companion_ends_open_questions(client, svc, running, tools, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    r = running()
    job = ask(client, svc)["job"]
    for _ in range(100):
        if svc.companions.watch(job, 0, timeout=0)["events"]:
            break
        time.sleep(0.05)
    r.close()
    state, events = collect(client, job)
    assert state == "error" and "stopped" in events[-1]["message"]


def test_missing_tool_is_an_error_for_the_question(client, svc, running, tools, monkeypatch):
    running()
    monkeypatch.setenv("PATH", "/nonexistent")
    for _ in range(100):  # the companion notices at its next poll that claude is gone
        if not svc.companions.status() or not svc.companions.status()[0]["providers"]:
            break
        time.sleep(0.05)
    assert client.post("/api/ai/ask", json={"prompt": "q"}).status_code == 409


def test_codex_is_offered_only_when_turned_on(client, svc, running, tools):
    running()
    for _ in range(500):  # a companion started as a subprocess can take seconds on a slow runner
        if svc.companions.status():
            break
        time.sleep(0.02)
    assert [p["id"] for p in svc.companions.status()[0]["providers"]] == ["claude"]
    assert client.post("/api/ai/ask", json={"prompt": "q", "provider": "codex"}).status_code == 409


def test_codex_runs_read_only_with_the_question_token_in_the_environment(client, svc, token, running, tools):
    running(CODEX_ON)
    first = ask(client, svc, "Hi", provider="codex")
    state, events = collect(client, first["job"])
    assert state == "done" and text_of(events) == "From Codex"
    second = ask(client, svc, "Again", conversation=first["conversation"])
    assert collect(client, second["job"])[0] == "done"
    one, two = calls(tools)
    argv = one["argv"]
    assert argv[:2] == ["exec", "--json"] and 'sandbox_mode="read-only"' in argv and argv[-1] == "-"
    assert one["env_token"] and one["env_token"] != token and one["env_companion"] == []
    assert token not in " ".join(argv) and one["env_token"] not in " ".join(argv)
    assert "Synapse" in one["stdin"] and "Hi" in one["stdin"]
    assert two["argv"][:3] == ["exec", "resume", "--json"] and "thread-1" in two["argv"]
    assert two["env_token"] != one["env_token"]  # each question has its own


# ---------------------------------------------------------------------- extension folder


def zip_with(*members):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data, attr in members:
            info = zipfile.ZipInfo(name)
            info.external_attr = attr << 16
            z.writestr(info, data)
    return out.getvalue()


@pytest.fixture
def ext_source(live, token, tools, tmp_path, monkeypatch):
    """A fake extension folder the server serves, and a connected profile."""
    source = tmp_path / "ext_src"
    source.mkdir()
    (source / "manifest.json").write_text(json.dumps({"name": "x", "key": KEY, "permissions": ["storage"]}))
    (source / "background.js").write_text("a")
    original = bundle.extension
    monkeypatch.setattr(bundle, "extension", lambda root=source: original(root))
    (tmp_path / "user" / "config" / "google-chrome").mkdir(parents=True)  # a browser is installed
    return source


def test_extension_folder_is_installed_registered_and_kept_current(ext_source, live, token, tmp_path):
    service = connect(live, token)
    service.install_extension()
    profile = common.load_profile("default")
    dest = Path(profile["extension"]["dir"])
    assert dest == common.home() / "default-extension" and (dest / "manifest.json").is_file()
    assert extension.folder_build(dest) == bundle.extension(ext_source)[0]
    state = service.state()["extension"]
    assert state["installed"] and state["id"] == extension.extension_id(dest) and state["browsers"] == ["Chrome"]
    service.install_extension()  # again: nothing changes, nothing breaks
    (ext_source / "background.js").write_text("b")
    (ext_source / "new.js").write_text("x")
    assert extension.sync_extension(remote.Server(live, token), dest)
    assert (dest / "background.js").read_text() == "b" and (dest / "new.js").is_file()
    assert sorted(p.name for p in common.home().iterdir() if p.name.startswith("default-extension")) == [
        "default-extension"
    ]  # no temporary folders left
    # a folder that is not ours is never replaced, even one with a manifest
    for files in ({"notes.txt": "keep"}, {"manifest.json": "{}", "background.js": "mine"}):
        other = tmp_path / f"mine{len(files)}"
        other.mkdir()
        for name, text in files.items():
            (other / name).write_text(text)
        common.save_profile("default", {**common.load_profile("default"), "extension": {"dir": str(other)}})
        with pytest.raises(common.Fail, match="build.json"):
            service.install_extension()
        assert sorted(p.name for p in other.iterdir()) == sorted(files)
        assert all((other / n).read_text() == t for n, t in files.items())


def test_extension_update_changing_permissions_needs_review(ext_source, live, token, capsys):
    service = connect(live, token)
    service.install_extension()
    dest = service.extension_dir(common.load_profile("default"))
    (ext_source / "manifest.json").write_text(
        json.dumps({"name": "x", "key": KEY, "permissions": ["storage", "cookies"]})
    )
    (ext_source / "background.js").write_text("b")
    capsys.readouterr()
    service.check()  # what the companion does at start and hourly: not applied, the user is asked
    service.check()
    out = capsys.readouterr().out
    assert service.review == ["permissions"] and out.count("approve it in the window") == 1
    assert service.state()["extension"]["review"] == ["permissions"]
    assert (dest / "background.js").read_text() == "a" and "cookies" not in (dest / "manifest.json").read_text()
    with pytest.raises(common.Fail, match="permissions"):  # a plain refresh does not approve it either
        service.install_extension()
    assert (dest / "background.js").read_text() == "a"
    service.install_extension(approve=True)
    assert (dest / "background.js").read_text() == "b" and "cookies" in (dest / "manifest.json").read_text()
    assert service.review is None
    # other changes go through at the next check without a question
    (ext_source / "background.js").write_text("c")
    service.check()
    assert (dest / "background.js").read_text() == "c"


def test_extension_updates_can_be_turned_off(ext_source, live, token):
    service = connect(live, token)
    service.install_extension()
    dest = service.extension_dir(common.load_profile("default"))
    service.set_settings({"extension_updates": False})
    (ext_source / "background.js").write_text("b")
    service.check()
    assert (dest / "background.js").read_text() == "a" and service.hello["extension"]
    assert service.state()["extension"]["up_to_date"] is False
    service.set_settings({"extension_updates": True})
    service.check()
    assert (dest / "background.js").read_text() == "b" and service.state()["extension"]["up_to_date"] is True


@pytest.mark.parametrize(
    "name,attr",
    [
        ("../evil.txt", 0o100644),
        ("sub/../../evil.txt", 0o100644),
        ("/abs/evil.txt", 0o100644),
        ("..\\evil.txt", 0o100644),
        ("C:/evil.txt", 0o100644),
        ("link", 0o120777),
        ("dev", 0o060644),
    ],
)
def test_unsafe_zip_members_are_refused(tmp_path, name, attr):
    target = tmp_path / "t"
    target.mkdir()
    data = zip_with(("manifest.json", "{}", 0o100644), (name, "x", attr))
    with pytest.raises(common.Fail):
        extension._safe_extract(data, target)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "abs").exists()


def test_zip_without_a_manifest_is_refused(tmp_path):
    with pytest.raises(common.Fail, match="manifest"):
        extension._safe_extract(zip_with(("a.txt", "x", 0o100644)), tmp_path)
    with pytest.raises(common.Fail):
        extension._safe_extract(b"not a zip", tmp_path)


def test_local_edits_to_the_installed_extension_are_repaired(ext_source, live, token, tmp_path):
    server = remote.Server(live, token)
    dest = tmp_path / "ext"
    extension.sync_extension(server, dest, force=True)
    assert not extension.sync_extension(server, dest)
    (dest / "background.js").write_text("tampered")
    assert extension.sync_extension(server, dest) and (dest / "background.js").read_text() == "a"
    (dest / "extra.js").write_text("added")
    assert extension.sync_extension(server, dest) and not (dest / "extra.js").exists()


def test_folders_the_program_did_not_create_are_never_removed(ext_source, live, token, tmp_path):
    server = remote.Server(live, token)
    dest = tmp_path / "ext"
    for sibling in ("ext.new", "ext.old"):
        (tmp_path / sibling).mkdir()
        (tmp_path / sibling / "keep.txt").write_text("mine")
    extension.sync_extension(server, dest, force=True)
    extension.sync_extension(server, dest, force=True)
    assert all((tmp_path / n / "keep.txt").read_text() == "mine" for n in ("ext.new", "ext.old"))


def test_corrupt_downloads_are_a_failure_not_a_crash(tmp_path):
    good = zip_with(("manifest.json", "{}" * 2000, 0o100644))
    broken = good[:60] + bytes(b ^ 0xFF for b in good[60:90]) + good[90:]
    for data in (good[:-10], broken, b"PK\x03\x04" + b"\x00" * 40):
        with pytest.raises(common.Fail):
            extension._safe_extract(data, tmp_path)


def test_unexpected_server_answers_do_not_crash(tmp_path, tools):
    answers = [
        b"[]",
        b'"text"',
        b"not json",
        b'{"app": "synapse", "extension": "x", "script": 5, "version": "\\u001b[2Jv"}',
    ]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(answers[self.server.index])

        log_message = lambda *a: None  # noqa: E731

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for i in range(3):
            server.index = i
            with pytest.raises(remote.ApiError):
                remote.Server(f"http://127.0.0.1:{server.server_port}", "t").hello()
        server.index = 3
        hello = remote.Server(f"http://127.0.0.1:{server.server_port}", "t").hello()
        assert hello["extension"] == "" and hello["script"] == "" and "\x1b" not in hello["version"]
    finally:
        server.shutdown()


def test_text_from_the_server_loses_its_control_characters(capsys):
    common.log("hello \x1b[2J\x07 world\nsecond")
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and out.count("\n") == 1 and "hello" in out and "world" in out


def test_the_companion_never_runs_more_than_its_limit(live, token, tools, monkeypatch):
    connect(live, token)
    companion = runner.Companion(common.load_profile("default"), "PC")
    started, posted = [], []

    class StubJob:
        def __init__(self, _companion, spec):
            self.spec, self.id = spec, spec["id"]

        def start(self):
            started.append(self.spec["id"])

    monkeypatch.setattr(runner, "Job", StubJob)
    monkeypatch.setattr(
        companion.server, "call", lambda method, path, payload=None, **kw: posted.append((path, payload))
    )
    for job_id in ("a", "b", "a", "c", "d"):  # the server repeats one id and hands out more than two
        companion._accept({"id": job_id})
    assert started == ["a", "b"] and sorted(companion.jobs) == ["a", "b"]
    assert [p for p, _ in posted] == ["/api/companion/jobs/c/events", "/api/companion/jobs/d/events"]
    assert posted[0][1]["events"] == [{"type": "error", "message": "The companion is busy with other questions."}]
    companion.finished(companion.jobs["a"])
    companion._accept({"id": "e"})
    assert started == ["a", "b", "e"]
    companion.update_settings({"max_running": 1})  # the user's own, lower limit
    companion._accept({"id": "f"})
    assert started == ["a", "b", "e"] and [p for p, _ in posted][-1] == "/api/companion/jobs/f/events"


def test_a_failing_update_check_does_not_end_the_companion(live, token, svc, tools, monkeypatch):
    connect(live, token)

    def broken(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(Service, "check", broken)
    service = Service("default")
    service.start()
    try:
        for _ in range(100):
            if svc.companions.status():
                break
            time.sleep(0.05)
        assert svc.companions.status() and service.companion is not None
        assert service.state()["connection"]["state"] in ("connecting", "connected")
    finally:
        service.quit()


def test_sigterm_stops_the_ai_tool_at_once(client, svc, live, token, tools, tmp_path, monkeypatch):
    import signal

    monkeypatch.setenv("FAKE_MODE", "hang")
    connect(live, token)
    proc = subprocess.Popen(
        [sys.executable, "-m", "synapse_companion", "run"],
        cwd=ROOT,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        job = ask(client, svc)["job"]
        for _ in range(200):
            if svc.companions.watch(job, 0, timeout=0)["events"]:
                break
            time.sleep(0.05)
        pid = calls(tools)[0]["pid"]
        proc.send_signal(signal.SIGTERM)
        started = time.monotonic()
        out, _ = proc.communicate(timeout=10)
        assert time.monotonic() - started < 6 and proc.returncode == 0
        assert "Stopped." in out
        state, events = collect(client, job)
        assert state == "error" and "stopped" in events[-1]["message"]
        with pytest.raises(ProcessLookupError):
            for _ in range(30):
                os.kill(pid, 0)
                time.sleep(0.1)
            raise AssertionError("the AI tool kept running")
    finally:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------- models and efforts


def test_the_companion_advertises_its_models_and_efforts(client, svc, running, tools):
    running(CODEX_ON)
    for _ in range(500):  # a companion started as a subprocess can take seconds on a slow runner
        if svc.companions.status():
            break
        time.sleep(0.02)
    advertised = {p["id"]: p for p in svc.companions.status()[0]["providers"]}
    assert [m["id"] for m in advertised["claude"]["models"]] == ["fable", "opus", "sonnet", "haiku"]
    assert advertised["claude"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert advertised["codex"]["models"] == [] and advertised["codex"]["efforts"] == ["low", "medium", "high", "xhigh"]
    assert all(
        providers.CHOICE.fullmatch(i)
        for p in (providers.Claude, providers.Codex)
        for i in [*(m for m, _ in p.models), *p.efforts]
    )


def test_model_and_effort_reach_the_command_line_and_nothing_else_does(client, svc, running, tools):
    running(CODEX_ON)
    chosen = ask(client, svc, model="sonnet", effort="high")
    assert collect(client, chosen["job"])[0] == "done"
    plain = ask(client, svc)
    collect(client, plain["job"])
    with_choice, without = calls(tools)
    argv = with_choice["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "high"
    assert "--model" not in without["argv"] and "--effort" not in without["argv"]
    codex = ask(client, svc, provider="codex", effort="low")
    collect(client, codex["job"])
    codex_argv = calls(tools)[-1]["argv"]
    assert 'model_reasoning_effort="low"' in codex_argv and "-m" not in codex_argv


def test_a_value_the_companion_does_not_offer_is_ignored():
    provider = providers.Claude("claude")
    for bad in ("--dangerous", "opus; rm -rf", "x" * 70, "OPUS", "", None, 5, ["opus"]):
        assert provider.choice({"model": bad}, "model") is None and provider.choice({"effort": bad}, "effort") is None
    assert provider.choice({"model": "opus", "effort": "max"}, "model") == "opus"
    assert provider.choice({"model": "opus", "effort": "max"}, "effort") == "max"
    assert providers.Codex("codex").choice({"model": "gpt-5"}, "model") is None  # Codex offers no model list


def test_a_hostile_job_cannot_put_its_model_on_the_command_line(client, svc, live, token, tools):
    connect(live, token)
    profile = common.load_profile("default")
    entry = svc.tokens.check(token, scope="companion")
    claude = providers.Claude("claude")
    svc.companions.poll(profile["client"], "PC", [claude.advert()], [], token=entry["id"], timeout=0)
    asked = client.post("/api/ai/ask", json={"prompt": "q", "model": "opus"}).get_json()
    svc.companions._jobs[asked["job"]].model = "--dangerously-skip-permissions"  # a server gone wrong
    svc.companions._jobs[asked["job"]].effort = "high; echo"
    companion = runner.Companion(profile, "PC")
    thread = threading.Thread(target=companion.run, daemon=True)
    thread.start()
    try:
        assert collect(client, asked["job"])[0] == "done"
    finally:
        companion.stop()
        thread.join(10)
    argv = calls(tools)[0]["argv"]
    assert "--model" not in argv and "--effort" not in argv and not any("dangerous" in a or "echo" in a for a in argv)


def test_started_and_usage_events_come_from_the_tool(client, svc, running, tools):
    running()
    state, events = collect(client, ask(client, svc)["job"])
    kinds = [e["type"] for e in events]
    assert state == "done" and kinds[0] == "started" and kinds[-2:] == ["usage", "done"]
    assert events[-2] == {
        "type": "usage",
        "input_tokens": 17,
        "output_tokens": 7,
        "cost_usd": 0.01,
        "duration_ms": 1234,
    }


def test_a_conversation_whose_session_is_gone_says_so(client, svc, running, tools, monkeypatch):
    running()
    first = ask(client, svc)
    collect(client, first["job"])
    monkeypatch.setenv("FAKE_MODE", "gone")
    state, events = collect(client, ask(client, svc, conversation=first["conversation"])["job"])
    assert state == "error" and events[-1] == {"type": "error", "message": providers.NO_HISTORY}
    stored = client.get(f"/api/ai/conversations/{first['conversation']}").get_json()["messages"]
    assert stored[-1]["error"] == providers.NO_HISTORY and stored[-1]["state"] == "error"


def test_a_restarted_codex_cannot_resume_and_says_so_without_running_anything(client, svc, live, token, tools):
    first_run = Running(live, token, CODEX_ON)
    first = ask(client, svc, provider="codex")
    assert collect(client, first["job"])[0] == "done"
    first_run.close()
    second_run = Running(live, token, CODEX_ON)  # a new process: no threads remembered
    try:
        state, events = collect(client, ask(client, svc, conversation=first["conversation"])["job"])
    finally:
        second_run.close()
    assert state == "error" and events[-1]["message"] == providers.NO_HISTORY
    assert len(calls(tools)) == 1  # the tool was started only for the first question


def test_codex_reports_its_usage(client, svc, running, tools):
    running(CODEX_ON)
    state, events = collect(client, ask(client, svc, provider="codex")["job"])
    assert state == "done" and events[0] == {"type": "started"}
    assert {"type": "usage", "input_tokens": 11, "output_tokens": 3} in events
