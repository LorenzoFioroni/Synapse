"""The companion program (synapse_companion.py) against a live test server and fake AI tools."""

import http.server
import io
import json
import os
import stat
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path

import pytest
from werkzeug.serving import make_server

import synapse_companion as sc
from backend.companion import bundle, hub

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
    out({{"type": "result", "is_error": False, "result": "Hello"}})
"""

FAKE_CODEX = r"""#!{python}
import json, os, sys
log = json.load(open(os.environ["FAKE_LOG"])) if os.path.exists(os.environ["FAKE_LOG"]) else []
log.append({{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "env_token": os.environ.get("SYNAPSE_MCP_TOKEN")}})
json.dump(log, open(os.environ["FAKE_LOG"], "w"))
for o in (
    {{"type": "thread.started", "thread_id": "thread-1"}},
    {{"type": "item.started", "item": {{"type": "mcp_tool_call", "tool": "get_note"}}}},
    {{"type": "item.completed", "item": {{"type": "agent_message", "text": "From Codex"}}}},
    {{"type": "turn.completed"}},
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
    monkeypatch.setenv("SYNAPSE_COMPANION_HOME", str(tmp_path / "home"))
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


def connect(live, token, *extra):
    assert sc.main(["connect", live, "--token", token, "--name", "Test PC", *extra]) == 0


class Running:
    """A companion running in a thread."""

    def __init__(self, live, token, allow=()):
        connect(live, token)
        self.companion = sc.Companion(sc.load_profile("default"), "Test PC", allow)
        self.thread = threading.Thread(target=self.companion.run, daemon=True)
        self.thread.start()

    def close(self):
        self.companion.stop()
        self.thread.join(10)
        assert not self.thread.is_alive()


@pytest.fixture
def running(live, token, tools):
    started = []

    def start(allow=()):
        started.append(Running(live, token, allow))
        return started[-1]

    yield start
    for r in started:
        r.close()


def ask(client, svc, prompt="Question?", **extra):
    for _ in range(100):
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


def test_connect_stores_a_private_profile(live, token, tools, capsys):
    connect(live, token)
    path = sc.profile_path("default")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    profile = json.loads(path.read_text())
    assert profile["server"] == live and profile["token"] == token and profile["name"] == "Test PC"
    client_id = profile["client"]
    connect(live, token)  # connecting again keeps this computer's identity
    assert json.loads(path.read_text())["client"] == client_id
    connect(live, token, "--profile", "other")
    assert sc.profile_path("other").exists()
    assert sc.main(["status"]) == 0
    assert "reachable" in capsys.readouterr().out


def test_connect_refuses_unsafe_or_wrong_input(live, svc, tools, capsys):
    assert sc.main(["connect", "http://synapse.example.com", "--token", "x"]) == 1
    assert "https" in capsys.readouterr().err
    assert sc.main(["connect", "ftp://x", "--token", "x"]) == 1
    assert sc.main(["connect", live, "--token", "wrong"]) == 1
    assert "companion token" in capsys.readouterr().err
    assert sc.main(["connect", live, "--token", svc.tokens.create("m", scope="mcp")[1]]) == 1
    assert not sc.profile_path("default").exists()
    assert sc.main(["run"]) == 1  # not connected
    assert sc.main(["connect", "https://synapse.example.com", "--token", "x", "--profile", "../bad"]) == 1


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
        with pytest.raises(sc.ApiError, match="redirect"):
            sc.Server(f"http://127.0.0.1:{redirecting.server_port}", token).call("GET", "/x")
    finally:
        target.shutdown()
        redirecting.shutdown()
    assert seen == []


# ---------------------------------------------------------------------- questions


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
    # the token is in the private config file only: not in argv, not in the environment
    assert token not in " ".join(argv) and call["env_token"] == []
    assert call["config_mode"] == "0o600" and token in call["config"]
    assert json.loads(call["config"])["mcpServers"]["synapse"]["url"].endswith("/mcp")
    assert not Path(call["config_path"]).exists()
    # run from an empty private directory
    assert Path(call["cwd"]).name == "work-default" and not any(Path(call["cwd"]).iterdir())


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


def test_codex_is_offered_only_when_allowed(client, svc, running, tools):
    running()
    for _ in range(100):
        if svc.companions.status():
            break
        time.sleep(0.02)
    assert [p["id"] for p in svc.companions.status()[0]["providers"]] == ["claude"]
    assert client.post("/api/ai/ask", json={"prompt": "q", "provider": "codex"}).status_code == 409


def test_codex_runs_read_only_with_the_token_in_the_environment(client, svc, token, running, tools):
    running(allow={"codex"})
    first = ask(client, svc, "Hi", provider="codex")
    state, events = collect(client, first["job"])
    assert state == "done" and text_of(events) == "From Codex"
    second = ask(client, svc, "Again", conversation=first["conversation"])
    assert collect(client, second["job"])[0] == "done"
    one, two = calls(tools)
    argv = one["argv"]
    assert argv[:2] == ["exec", "--json"] and 'sandbox_mode="read-only"' in argv and argv[-1] == "-"
    assert token not in " ".join(argv) and one["env_token"] == token
    assert "Synapse" in one["stdin"] and "Hi" in one["stdin"]
    assert two["argv"][:3] == ["exec", "resume", "--json"] and "thread-1" in two["argv"]


# ---------------------------------------------------------------------- extension folder


def test_extension_folder_is_installed_and_kept_current(live, token, tools, tmp_path, monkeypatch, capsys):
    source = tmp_path / "ext_src"
    source.mkdir()
    (source / "manifest.json").write_text("{}")
    original = bundle.extension
    monkeypatch.setattr(bundle, "extension", lambda root=source: original(root))
    connect(live, token)
    dest = tmp_path / "chrome" / "synapse"
    assert sc.main(["extension", "--dir", str(dest)]) == 0
    assert "Load unpacked" in capsys.readouterr().out
    assert (dest / "manifest.json").is_file() and sc.folder_build(dest) == original(source)[0]
    assert sc.main(["extension", "--dir", str(dest)]) == 0
    out = capsys.readouterr().out
    assert "up to date" in out and "Load unpacked" not in out
    (source / "manifest.json").write_text('{"v": 2}')
    (source / "new.js").write_text("x")
    sc.sync_extension(sc.Server(live, token), dest)
    assert json.loads((dest / "manifest.json").read_text()) == {"v": 2} and (dest / "new.js").is_file()
    assert sorted(p.name for p in (tmp_path / "chrome").iterdir()) == ["synapse"]  # no temporary folders left
    # a folder that is not ours is never replaced, even one with a manifest
    for files in ({"notes.txt": "keep"}, {"manifest.json": "{}", "background.js": "mine"}):
        other = tmp_path / f"mine{len(files)}"
        other.mkdir()
        for name, text in files.items():
            (other / name).write_text(text)
        assert sc.main(["extension", "--dir", str(other)]) == 1
        assert sorted(p.name for p in other.iterdir()) == sorted(files)
        assert all((other / n).read_text() == t for n, t in files.items())


def zip_with(*members):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data, attr in members:
            info = zipfile.ZipInfo(name)
            info.external_attr = attr << 16
            z.writestr(info, data)
    return out.getvalue()


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
    with pytest.raises(sc.Fail):
        sc._safe_extract(data, target)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "abs").exists()


def test_zip_without_a_manifest_is_refused(tmp_path):
    with pytest.raises(sc.Fail, match="manifest"):
        sc._safe_extract(zip_with(("a.txt", "x", 0o100644)), tmp_path)
    with pytest.raises(sc.Fail):
        sc._safe_extract(b"not a zip", tmp_path)


# ---------------------------------------------------------------------- update


def test_update_replaces_only_this_script_on_request(live, token, tools, tmp_path, monkeypatch, capsys):
    connect(live, token)
    copy = tmp_path / "synapse_companion.py"
    copy.write_text(Path(sc.__file__).read_text() + "\n# old\n")
    copy.chmod(0o755)
    monkeypatch.setattr(sc, "__file__", str(copy))
    assert sc.main(["update"]) == 0
    assert copy.read_bytes() == Path(bundle.SCRIPT).read_bytes() and os.access(copy, os.X_OK)
    assert sc.main(["update"]) == 0
    assert "up to date" in capsys.readouterr().out


# ---------------------------------------------------------------------- fix round


@pytest.fixture
def ext_source(live, token, tools, tmp_path, monkeypatch):
    """A fake extension folder the server serves, and a connected profile."""
    source = tmp_path / "ext_src"
    source.mkdir()
    (source / "manifest.json").write_text(json.dumps({"name": "x", "permissions": ["storage"]}))
    (source / "background.js").write_text("a")
    original = bundle.extension
    monkeypatch.setattr(bundle, "extension", lambda root=source: original(root))
    connect(live, token)
    return source


def test_extension_update_changing_permissions_needs_review(ext_source, live, token, tmp_path, monkeypatch, capsys):
    server = sc.Server(live, token)
    dest = tmp_path / "ext"
    assert sc.sync_extension(server, dest, force=True)
    (ext_source / "manifest.json").write_text(json.dumps({"name": "x", "permissions": ["storage", "cookies"]}))
    (ext_source / "background.js").write_text("b")
    with pytest.raises(sc.ExtensionReview) as asked:  # what `run` does at start and hourly
        sc.sync_extension(server, dest)
    assert asked.value.keys == ["permissions"]
    assert (dest / "background.js").read_text() == "a" and "cookies" not in (dest / "manifest.json").read_text()
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("ext")) == ["ext", "ext_src"]
    # the command shows the keys and asks; a refusal changes nothing
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    assert sc.main(["extension", "--dir", str(dest)]) == 1
    assert "permissions" in capsys.readouterr().out and (dest / "background.js").read_text() == "a"
    assert sc.main(["extension", "--dir", str(dest), "--yes"]) == 0
    assert (dest / "background.js").read_text() == "b" and "cookies" in (dest / "manifest.json").read_text()
    # other changes go through without a question
    (ext_source / "background.js").write_text("c")
    assert sc.sync_extension(server, dest) and (dest / "background.js").read_text() == "c"


def test_run_logs_one_line_for_an_update_it_will_not_apply(ext_source, live, token, tmp_path, capsys, tools):
    dest = tmp_path / "ext"
    sc.sync_extension(sc.Server(live, token), dest, force=True)
    (ext_source / "manifest.json").write_text(json.dumps({"name": "x", "host_permissions": ["<all_urls>"]}))
    companion = sc.Companion(sc.load_profile("default"), "PC")
    thread = threading.Thread(target=companion.run, kwargs={"sync": lambda: sc.sync_extension(companion.server, dest)})
    thread.start()
    time.sleep(0.5)
    companion.stop()
    thread.join(10)
    out = capsys.readouterr().out
    assert out.count("run `extension` to review") == 1 and "host_permissions" in out
    assert "<all_urls>" not in (dest / "manifest.json").read_text()


def test_local_edits_to_the_installed_extension_are_repaired(ext_source, live, token, tmp_path):
    server = sc.Server(live, token)
    dest = tmp_path / "ext"
    sc.sync_extension(server, dest, force=True)
    assert not sc.sync_extension(server, dest)
    (dest / "background.js").write_text("tampered")
    assert sc.sync_extension(server, dest) and (dest / "background.js").read_text() == "a"
    (dest / "extra.js").write_text("added")
    assert sc.sync_extension(server, dest) and not (dest / "extra.js").exists()


def test_folders_the_program_did_not_create_are_never_removed(ext_source, live, token, tmp_path):
    server = sc.Server(live, token)
    dest = tmp_path / "ext"
    for sibling in ("ext.new", "ext.old"):
        (tmp_path / sibling).mkdir()
        (tmp_path / sibling / "keep.txt").write_text("mine")
    sc.sync_extension(server, dest, force=True)
    sc.sync_extension(server, dest, force=True)
    assert all((tmp_path / n / "keep.txt").read_text() == "mine" for n in ("ext.new", "ext.old"))


def test_corrupt_downloads_are_a_failure_not_a_crash(tmp_path):
    good = zip_with(("manifest.json", "{}" * 2000, 0o100644))
    broken = good[:60] + bytes(b ^ 0xFF for b in good[60:90]) + good[90:]
    for data in (good[:-10], broken, b"PK\x03\x04" + b"\x00" * 40):
        with pytest.raises(sc.Fail):
            sc._safe_extract(data, tmp_path)


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
            with pytest.raises(sc.ApiError):
                sc.Server(f"http://127.0.0.1:{server.server_port}", "t").hello()
        server.index = 3
        hello = sc.Server(f"http://127.0.0.1:{server.server_port}", "t").hello()
        assert hello["extension"] == "" and hello["script"] == "" and "\x1b" not in hello["version"]
    finally:
        server.shutdown()


def test_text_from_the_server_loses_its_control_characters(capsys):
    sc.log("hello \x1b[2J\x07 world\nsecond")
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and out.count("\n") == 1 and "hello" in out and "world" in out


def test_the_companion_never_runs_more_than_its_limit(live, token, tools, monkeypatch):
    connect(live, token)
    companion = sc.Companion(sc.load_profile("default"), "PC")
    started, posted = [], []

    class StubJob:
        def __init__(self, _companion, spec):
            self.spec, self.id = spec, spec["id"]

        def start(self):
            started.append(self.spec["id"])

    monkeypatch.setattr(sc, "Job", StubJob)
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


def test_a_failing_sync_does_not_end_run(live, token, svc, tools):
    connect(live, token)
    companion = sc.Companion(sc.load_profile("default"), "PC")

    def broken():
        raise RuntimeError("boom")

    thread = threading.Thread(target=companion.run, kwargs={"sync": broken}, daemon=True)
    thread.start()
    for _ in range(100):
        if svc.companions.status():
            break
        time.sleep(0.05)
    assert svc.companions.status() and thread.is_alive()
    companion.stop()
    thread.join(10)


def test_sigterm_stops_the_ai_tool_at_once(client, svc, live, token, tools, tmp_path, monkeypatch):
    import signal
    import subprocess

    monkeypatch.setenv("FAKE_MODE", "hang")
    connect(live, token)
    env = {**os.environ, "SYNAPSE_COMPANION_HOME": str(tmp_path / "home")}
    proc = subprocess.Popen(
        [sys.executable, sc.__file__, "run", "--allow", "codex"], env=env, stdout=subprocess.PIPE, text=True
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
        assert "Codex is on" in out and "Stopped." in out
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
