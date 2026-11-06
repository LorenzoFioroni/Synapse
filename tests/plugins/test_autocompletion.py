"""Autocompletion in notes, a contributed plugin (plugins/autocompletion): its settings and how they carry
over from Synapse's own, the status checks that guard against a missing Ollama or model, the
request sent to Ollama, and cleaning the model's answer."""

import json
import threading
import time

import pytest

from backend import create_app
from backend.config import APP_ROOT, Config
from backend.plugins.http import PluginHttp
from backend.plugins.loading import load_module
from tests.fake_ollama import FakeOllama, free_port

PID = "autocompletion"
BASE = f"/api/plugins/{PID}"


@pytest.fixture(scope="module")
def mod():
    """The plugin's server package (outside the app, for its pure parts)."""
    module, _ = load_module(PID, APP_ROOT / "plugins" / PID / "server")
    return module


@pytest.fixture
def on(client, svc, bundled):
    """Turn the plugin on; returns a function setting its options."""
    assert client.post(f"/api/plugin-host/{PID}/enable").get_json()["plugin"]["running"]

    def turn_on(url, **more):
        r = client.put(f"/api/plugin-host/{PID}/settings", json={"url": url, **more})
        assert r.status_code == 200, r.get_json()

    return turn_on


def make_ollama(mod, url):
    return mod.ollama.Ollama(url, PluginHttp(PID, ["loopback"], lambda: "test"))


def completer(svc):
    """A completer on the running plugin's settings (as its routes use)."""
    act = svc.plugins._active[PID]
    return act.module.Completer(
        lambda: svc.plugins.values(PID), client=lambda url: act.module.ollama.Ollama(url, act.plugin.http)
    )


# ------------------------------------------------------------------ the plugin and its settings


def test_off_by_default(client, bundled):
    p = next(p for p in client.get("/api/plugin-host").get_json()["plugins"] if p["id"] == PID)
    assert not p["core"] and not p["enabled"] and p["status"] == "off"
    assert p["values"] == {"model": "qwen2.5-coder:1.5b", "length": "line", "url": "http://127.0.0.1:11434"}
    assert client.get(f"{BASE}/status").status_code == 404  # off: no routes
    assert "notes_ai_complete" not in client.get("/api/settings").get_json()["settings"]


def test_settings_carry_over_from_synapse(tmp_path, bundled, svc):
    data = tmp_path / "old"
    data.mkdir()
    (data / "settings.json").write_text(
        json.dumps(
            {
                "notes_ai_complete": True,
                "notes_ai_model": "llama3.2:3b",
                "notes_ai_length": "paragraph",
                "theme": "dark",
            }
        )
    )
    app = create_app(Config.from_env(data), start_indexer=False)
    c = app.test_client()
    p = next(p for p in c.get("/api/plugin-host").get_json()["plugins"] if p["id"] == PID)
    assert p["enabled"] and p["running"]  # it was on
    assert p["values"]["model"] == "llama3.2:3b" and p["values"]["length"] == "paragraph"
    assert c.get("/api/settings").get_json()["settings"]["theme"] == "dark"
    c.put("/api/settings", json={"theme": "light"})
    assert json.loads((data / "settings.json").read_text())["notes_ai_model"] == "llama3.2:3b"  # kept
    c.post(f"/api/plugin-host/{PID}/disable")
    app2 = create_app(Config.from_env(data), start_indexer=False)  # turned off stays off
    assert not next(p for p in app2.test_client().get("/api/plugin-host").get_json()["plugins"] if p["id"] == PID)[
        "enabled"
    ]


def test_settings_are_checked(client, on):
    on("http://127.0.0.1:1")
    assert client.put(f"/api/plugin-host/{PID}/settings", json={"length": "novel"}).status_code == 400
    st = client.get(f"{BASE}/status?fresh=1").get_json()
    assert st["reason"] == "unreachable"
    on("http://10.0.0.2:11434")
    st = client.get(f"{BASE}/status?fresh=1").get_json()
    assert st["ok"] is False and st["reason"] == "bad_url" and "this computer" in st["message"]
    on("localhost:9", model="two words")
    assert client.get(f"{BASE}/status?fresh=1").get_json()["reason"] == "unsupported"


@pytest.mark.parametrize(
    "given,stored",
    [
        ("", "http://127.0.0.1:11434"),
        ("localhost:11434", "http://localhost:11434"),
        ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
        ("https://[::1]:11434", "https://[::1]:11434"),
        ("http://127.1.2.3:1", "http://127.1.2.3:1"),
    ],
)
def test_ollama_address_on_this_computer(mod, given, stored):
    assert mod.ollama.local_url(given) == stored


@pytest.mark.parametrize(
    "bad",
    [
        "http://192.168.1.10:11434",
        "http://example.com",
        "http://localhost.evil.com:11434",
        "http://127.0.0.1.nip.io",
        "ftp://127.0.0.1",
        "http://user:pw@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:port",
    ],
)
def test_other_hosts_are_refused(mod, bad):
    with pytest.raises(ValueError):
        mod.ollama.local_url(bad)


def test_the_servers_address_is_accepted(mod):
    server = "http://ollama:11434"  # --local-service ollama=http://ollama:11434
    assert mod.ollama.local_url("http://ollama:11434/", server) == server
    assert mod.ollama.local_url("http://127.0.0.1:11434", server) == "http://127.0.0.1:11434"
    for bad in ("http://ollama:22", "https://ollama:11434", "http://other:11434", "http://ollama:11434/api"):
        with pytest.raises(ValueError):
            mod.ollama.local_url(bad, server)
    with pytest.raises(ValueError):
        mod.ollama.local_url(server)  # not without the server's word


def test_the_servers_ollama_address_wins_over_settings(tmp_path, bundled):
    with FakeOllama() as fake:
        cfg = Config.from_env(tmp_path / "data")
        cfg.local_services = {"ollama": fake.url}
        c = create_app(cfg, start_indexer=False).test_client()
        assert c.post(f"/api/plugin-host/{PID}/enable").get_json()["plugin"]["running"]
        r = c.put(f"/api/plugin-host/{PID}/settings", json={"url": f"http://127.0.0.1:{free_port()}"})
        assert r.status_code == 200
        st = c.get(f"{BASE}/status?fresh=1").get_json()
        assert st["ok"] is True and st["url"] == fake.url and st["url_from_server"] is True
        c.post(f"{BASE}/complete", json={"prefix": "The cat sat on the", "suffix": ""})
        assert any(path == "/api/generate" for path, _ in fake.requests)


def test_settings_say_when_the_address_is_their_own(client, on):
    on(f"http://127.0.0.1:{free_port()}")
    assert client.get(f"{BASE}/status?fresh=1").get_json()["url_from_server"] is False


# ------------------------------------------------------------------ status: the safeguards


def test_status_when_ollama_is_not_running(client, svc, on):
    on(f"http://127.0.0.1:{free_port()}")
    st = client.get(f"{BASE}/status?fresh=1").get_json()
    assert st["enabled"] is True and st["ok"] is False
    assert st["reason"] == "unreachable"
    assert "not running" in st["message"]


def test_status_when_the_model_is_missing(client, svc, on):
    with FakeOllama(models=("llama3.2:3b",)) as fake:
        on(fake.url, model="qwen2.5-coder:1.5b")
        st = client.get(f"{BASE}/status?fresh=1").get_json()
    assert st["ok"] is False and st["reason"] == "no_model"
    assert "ollama pull qwen2.5-coder:1.5b" in st["message"]
    assert st["models"] == ["llama3.2:3b"]  # Settings offers what is installed


def test_status_ready_with_a_fill_in_the_middle_model(client, svc, on):
    with FakeOllama() as fake:
        on(fake.url)
        st = client.get(f"{BASE}/status?fresh=1").get_json()
    assert st["ok"] is True and st["fim"] is True and st["version"] == "0.12.0"


def test_status_of_a_model_without_tag_and_an_embedding_model(client, svc, on):
    with FakeOllama(models=("mistral:latest",), capabilities=("completion",)) as fake:
        on(fake.url, model="mistral")
        st = client.get(f"{BASE}/status?fresh=1").get_json()
        assert st["ok"] is True and st["fim"] is False and "only continues" in st["message"]
        fake.capabilities = ["embedding"]
        st = client.get(f"{BASE}/status?fresh=1").get_json()
        assert st["ok"] is False and st["reason"] == "unsupported"


def test_something_else_on_the_port_is_not_ollama(mod):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class NotOllama(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>hello</html>")

    srv = HTTPServer(("127.0.0.1", 0), NotOllama)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    with pytest.raises(mod.ollama.Unavailable) as e:
        make_ollama(mod, f"http://127.0.0.1:{srv.server_address[1]}").models()
    assert e.value.reason == "unreachable"
    srv.server_close()


def test_status_is_remembered_but_follows_settings(client, svc, on):
    with FakeOllama() as fake:
        on(fake.url)
        c = completer(svc)
        c.status()
        n = len(fake.requests)
        c.status()
        assert len(fake.requests) == n  # remembered
        client.put(f"/api/plugin-host/{PID}/settings", json={"model": "other:1b"})
        assert c.status()["reason"] == "no_model"  # a new model is checked at once


def test_warm_loads_the_model_once(svc, on):
    with FakeOllama() as fake:
        on(fake.url)
        c = completer(svc)
        c.status(warm=True)
        c.status(warm=True)
        for _ in range(50):
            if any(p == "/api/generate" for p, _ in fake.requests):
                break
            time.sleep(0.02)
        loads = [d for p, d in fake.requests if p == "/api/generate" and "prompt" not in d]
        assert len(loads) == 1 and loads[0]["keep_alive"]


# ------------------------------------------------------------------ complete


def test_nothing_is_sent_while_off(client, svc, on):
    with FakeOllama() as fake:
        on(fake.url)
        client.post(f"/api/plugin-host/{PID}/disable")
        r = client.post(f"{BASE}/complete", json={"prefix": "Hello", "suffix": ""})
        assert r.status_code == 404  # turned off: the plugin has no routes
        assert fake.requests == []


def test_no_model_means_no_generate_request(client, svc, on):
    with FakeOllama(models=()) as fake:
        on(fake.url)
        r = client.post(f"{BASE}/complete", json={"prefix": "Hello", "suffix": ""}).get_json()
        assert r["text"] == "" and r["reason"] == "no_model"
        assert fake.generated() == []


def test_unreachable_completion_says_why(client, svc, on):
    on(f"http://127.0.0.1:{free_port()}")
    r = client.post(f"{BASE}/complete", json={"prefix": "Hello", "suffix": ""})
    assert r.status_code == 200
    assert r.get_json()["reason"] == "unreachable"


def test_completion_fills_in_the_middle(mod, client, svc, on):
    with FakeOllama(reply=lambda p: " the ground state energy.\nMore text") as fake:
        on(fake.url)
        r = client.post(
            f"{BASE}/complete", json={"prefix": "# Note\n\nWe compute", "suffix": "\n\nNext paragraph."}
        ).get_json()
        assert r == {"text": " the ground state energy.", "reason": ""}
        sent = fake.generated()[0]
        assert sent["prompt"] == "# Note\n\nWe compute" and sent["suffix"] == "\n\nNext paragraph."
        assert sent["stream"] is False and sent["options"]["stop"] == ["\n"]
        assert "raw" not in sent


def test_completion_without_fill_in_the_middle_is_raw(client, svc, on):
    with FakeOllama(capabilities=("completion", "thinking"), reply=lambda p: " world$ and more") as fake:
        on(fake.url)
        r = client.post(f"{BASE}/complete", json={"prefix": "Say $hello", "suffix": "$ now"}).get_json()
        assert r["text"] == " world"  # what follows the cursor is not repeated
        sent = fake.generated()[0]
        assert sent["raw"] is True and "suffix" not in sent and "think" not in sent


def test_completion_accepts_bounded_earlier_note_context(client, on):
    with FakeOllama(reply=lambda p: r"\zeta$. More prose") as fake:
        on(fake.url)
        prefix = "Recent trial states and numerical diagnostics.\n" * 200 + "The reset is $"
        result = client.post(
            f"{BASE}/complete",
            json={
                "prefix": prefix,
                "suffix": "$.",
                "context": {
                    "title": "Cooling",
                    "headings": ["## Results"],
                    "excerpts": [r"The reset is denoted by $\zeta$."],
                },
            },
        ).get_json()
        assert result == {"text": r"\zeta", "reason": ""}
        sent = fake.generated()[-1]
        assert len(sent["prompt"]) <= 4000
        assert "# Cooling" in sent["prompt"] and r"denoted by $\zeta$" in sent["prompt"]
        assert sent["prompt"].endswith("The reset is $") and sent["suffix"] == "$."


def test_a_newer_request_supersedes_a_waiting_one(svc, on):
    with FakeOllama(reply=lambda p: " x", delay=0.3) as fake:
        on(fake.url)
        c = completer(svc)
        c.status()
        out = {}

        def ask(name):
            out[name] = c.complete(f"{name} ", "")

        first = threading.Thread(target=ask, args=("a",))
        first.start()
        time.sleep(0.1)  # a is generating
        second = threading.Thread(target=ask, args=("b",))
        second.start()
        time.sleep(0.05)  # b waits for a
        third = threading.Thread(target=ask, args=("c",))
        third.start()
        for t in (first, second, third):
            t.join()
        assert out["a"]["text"] == "x"
        assert out["b"]["reason"] == "superseded"
        assert out["c"]["text"] == "x"
        assert len(fake.generated()) == 2


def test_failed_generation_rechecks_status(mod, svc, on):
    with FakeOllama() as fake:
        on(fake.url)
        c = completer(svc)
        c.status()
        fake.models = []  # the model was removed meanwhile
        r = c.complete("Hello ", "")
        assert r["reason"] == "no_model"
        assert c.status()["reason"] == "no_model"


# ------------------------------------------------------------------ the prompt and the answer


def test_build_sends_whole_lines_near_the_cursor(mod):
    prefix = "".join(f"line {i}\n" for i in range(2000)) + "end of"
    p = mod.prompt.build("m", prefix, "", fim=True, thinking=True, length="paragraph")
    assert len(p["prompt"]) <= mod.prompt.PREFIX_CHARS
    assert p["prompt"].startswith("line ") and p["prompt"].endswith("end of")
    assert p["suffix"] == "\n"  # never empty: keeps Ollama filling in
    assert p["think"] is False and p["options"]["stop"] == ["\n\n"]
    assert p["options"]["num_predict"] == mod.prompt.MAX_TOKENS["paragraph"]
    empty_line = mod.prompt.build("m", "Text\n", "", fim=True, thinking=False, length="line")
    assert empty_line["options"]["stop"] == []


def test_same_model(mod):
    assert mod.ollama.same_model("qwen2.5-coder:latest", "qwen2.5-coder")
    assert mod.ollama.same_model("Qwen2.5-Coder:1.5b", "qwen2.5-coder:1.5b")
    assert not mod.ollama.same_model("qwen2.5-coder:7b", "qwen2.5-coder:1.5b")


@pytest.mark.parametrize(
    "raw,prefix,suffix,length,fim,want",
    [
        (" fox jumps\nover", "The quick", "", "line", True, " fox jumps"),
        ("\n\nA new line", "Text\n", "", "line", True, "A new line"),
        (" two spaces", "Ends with space ", "", "line", True, "two spaces"),
        ("one\ntwo\n\nthree", "Start:\n", "", "paragraph", True, "one\ntwo"),
        ("one\ntwo", "Mid", " rest of line", "paragraph", True, "one"),
        ("x^2<|endoftext|>junk", "$", "$", "line", True, "x^2"),
        ("x^2$", "$", "$.", "line", False, "x^2"),
        (
            " and the result. Next paragraph starts",
            "We find",
            "\n\nNext paragraph starts here",
            "line",
            True,
            " and the result.",
        ),
        ("   \n  ", "Text ", "", "line", True, ""),
    ],
)
def test_clean(mod, raw, prefix, suffix, length, fim, want):
    assert mod.prompt.clean(raw, prefix, suffix, length, fim=fim) == want


def test_safe_mode_preserves_enabled_contributed_plugin(tmp_path, bundled):
    data = tmp_path / "safe"
    data.mkdir()
    (data / "plugins.json").write_text(json.dumps({"plugins": {PID: {"enabled": True, "settings": {"model": "mine"}}}}))
    cfg = Config.from_env(data)
    cfg.safe_mode = True
    app = create_app(cfg, start_indexer=False)
    try:
        c = app.test_client()
        p = next(p for p in c.get("/api/plugin-host").get_json()["plugins"] if p["id"] == PID)
        assert not p["core"] and p["enabled"] and not p["running"] and p["status"] == "safe-mode"
        assert p["values"]["model"] == "mine"
        assert c.get(f"{BASE}/status").status_code == 404
    finally:
        app.extensions["synapse"].plugins.stop()
