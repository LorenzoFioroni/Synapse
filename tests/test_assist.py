"""Autocompletion in notes (backend/assist): settings, the status checks that guard against a
missing Ollama or model, the request sent to Ollama, and cleaning the model's answer."""

import threading
import time

import pytest

from backend.assist import prompt
from backend.assist.ollama import Ollama, Unavailable, same_model
from backend.settings import SettingsError, local_url
from tests.fake_ollama import FakeOllama, free_port


def turn_on(svc, url, **more):
    svc.settings.update({"notes_ai_complete": True, "notes_ai_url": url, **more})


# ------------------------------------------------------------------ settings


def test_off_by_default(client):
    s = client.get("/api/settings").get_json()["settings"]
    assert s["notes_ai_complete"] is False
    assert s["notes_ai_url"] == "http://127.0.0.1:11434"
    assert s["notes_ai_length"] == "line"


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
def test_ollama_address_on_this_computer(given, stored):
    assert local_url(given) == stored


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
def test_other_hosts_are_refused(bad):
    with pytest.raises(SettingsError):
        local_url(bad)


def test_settings_api_checks_address_model_and_length(client):
    assert client.put("/api/settings", json={"notes_ai_url": "http://10.0.0.2:11434"}).status_code == 400
    assert client.put("/api/settings", json={"notes_ai_model": "two words"}).status_code == 400
    assert client.put("/api/settings", json={"notes_ai_length": "novel"}).status_code == 400
    r = client.put("/api/settings", json={"notes_ai_url": "localhost:9", "notes_ai_length": "paragraph"})
    assert r.get_json()["settings"]["notes_ai_url"] == "http://localhost:9"


# ------------------------------------------------------------------ status: the safeguards


def test_status_when_ollama_is_not_running(client, svc):
    turn_on(svc, f"http://127.0.0.1:{free_port()}")
    st = client.get("/api/assist/status?fresh=1").get_json()
    assert st["enabled"] is True and st["ok"] is False
    assert st["reason"] == "unreachable"
    assert "not running" in st["message"]


def test_status_when_the_model_is_missing(client, svc):
    with FakeOllama(models=("llama3.2:3b",)) as fake:
        turn_on(svc, fake.url, notes_ai_model="qwen2.5-coder:1.5b")
        st = client.get("/api/assist/status?fresh=1").get_json()
    assert st["ok"] is False and st["reason"] == "no_model"
    assert "ollama pull qwen2.5-coder:1.5b" in st["message"]
    assert st["models"] == ["llama3.2:3b"]  # Settings offers what is installed


def test_status_ready_with_a_fill_in_the_middle_model(client, svc):
    with FakeOllama() as fake:
        turn_on(svc, fake.url)
        st = client.get("/api/assist/status?fresh=1").get_json()
    assert st["ok"] is True and st["fim"] is True and st["version"] == "0.12.0"


def test_status_of_a_model_without_tag_and_an_embedding_model(client, svc):
    with FakeOllama(models=("mistral:latest",), capabilities=("completion",)) as fake:
        turn_on(svc, fake.url, notes_ai_model="mistral")
        st = client.get("/api/assist/status?fresh=1").get_json()
        assert st["ok"] is True and st["fim"] is False and "only continues" in st["message"]
        fake.capabilities = ["embedding"]
        st = client.get("/api/assist/status?fresh=1").get_json()
        assert st["ok"] is False and st["reason"] == "unsupported"


def test_something_else_on_the_port_is_not_ollama():
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
    with pytest.raises(Unavailable) as e:
        Ollama(f"http://127.0.0.1:{srv.server_address[1]}").models()
    assert e.value.reason == "unreachable"
    srv.server_close()


def test_status_is_remembered_but_follows_settings(svc):
    with FakeOllama() as fake:
        turn_on(svc, fake.url)
        svc.assist.status()
        n = len(fake.requests)
        svc.assist.status()
        assert len(fake.requests) == n  # remembered
        svc.settings.update({"notes_ai_model": "other:1b"})
        assert svc.assist.status()["reason"] == "no_model"  # a new model is checked at once


def test_warm_loads_the_model_once(svc):
    with FakeOllama() as fake:
        turn_on(svc, fake.url)
        svc.assist.status(warm=True)
        svc.assist.status(warm=True)
        for _ in range(50):
            if any(p == "/api/generate" for p, _ in fake.requests):
                break
            time.sleep(0.02)
        loads = [d for p, d in fake.requests if p == "/api/generate" and "prompt" not in d]
        assert len(loads) == 1 and loads[0]["keep_alive"]


# ------------------------------------------------------------------ complete


def test_nothing_is_sent_while_off(client, svc):
    with FakeOllama() as fake:
        svc.settings.update({"notes_ai_url": fake.url})
        r = client.post("/api/assist/complete", json={"prefix": "Hello", "suffix": ""}).get_json()
        assert r == {"text": "", "reason": "off"}
        assert fake.requests == []


def test_no_model_means_no_generate_request(client, svc):
    with FakeOllama(models=()) as fake:
        turn_on(svc, fake.url)
        r = client.post("/api/assist/complete", json={"prefix": "Hello", "suffix": ""}).get_json()
        assert r["text"] == "" and r["reason"] == "no_model"
        assert fake.generated() == []


def test_unreachable_completion_says_why(client, svc):
    turn_on(svc, f"http://127.0.0.1:{free_port()}")
    r = client.post("/api/assist/complete", json={"prefix": "Hello", "suffix": ""})
    assert r.status_code == 200
    assert r.get_json()["reason"] == "unreachable"


def test_completion_fills_in_the_middle(client, svc):
    with FakeOllama(reply=lambda p: " the ground state energy.\nMore text") as fake:
        turn_on(svc, fake.url)
        r = client.post(
            "/api/assist/complete", json={"prefix": "# Note\n\nWe compute", "suffix": "\n\nNext paragraph."}
        ).get_json()
        assert r == {"text": " the ground state energy.", "reason": ""}
        sent = fake.generated()[0]
        assert sent["prompt"] == "# Note\n\nWe compute" and sent["suffix"] == "\n\nNext paragraph."
        assert sent["stream"] is False and sent["options"]["stop"] == ["\n"]
        assert "raw" not in sent


def test_completion_without_fill_in_the_middle_is_raw(client, svc):
    with FakeOllama(capabilities=("completion", "thinking"), reply=lambda p: " world$ and more") as fake:
        turn_on(svc, fake.url)
        r = client.post("/api/assist/complete", json={"prefix": "Say $hello", "suffix": "$ now"}).get_json()
        assert r["text"] == " world"  # what follows the cursor is not repeated
        sent = fake.generated()[0]
        assert sent["raw"] is True and "suffix" not in sent and "think" not in sent


def test_a_newer_request_supersedes_a_waiting_one(svc):
    with FakeOllama(reply=lambda p: " x", delay=0.3) as fake:
        turn_on(svc, fake.url)
        svc.assist.status()
        out = {}

        def ask(name):
            out[name] = svc.assist.complete(f"{name} ", "")

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


def test_failed_generation_rechecks_status(svc):
    with FakeOllama() as fake:
        turn_on(svc, fake.url)
        svc.assist.status()
        fake.models = []  # the model was removed meanwhile
        r = svc.assist.complete("Hello ", "")
        assert r["reason"] == "no_model"
        assert svc.assist.status()["reason"] == "no_model"


# ------------------------------------------------------------------ the prompt and the answer


def test_build_sends_whole_lines_near_the_cursor():
    prefix = "".join(f"line {i}\n" for i in range(2000)) + "end of"
    p = prompt.build("m", prefix, "", fim=True, thinking=True, length="paragraph")
    assert len(p["prompt"]) <= prompt.PREFIX_CHARS
    assert p["prompt"].startswith("line ") and p["prompt"].endswith("end of")
    assert p["suffix"] == "\n"  # never empty: keeps Ollama filling in
    assert p["think"] is False and p["options"]["stop"] == ["\n\n"]
    assert p["options"]["num_predict"] == prompt.MAX_TOKENS["paragraph"]
    empty_line = prompt.build("m", "Text\n", "", fim=True, thinking=False, length="line")
    assert empty_line["options"]["stop"] == []


def test_same_model():
    assert same_model("qwen2.5-coder:latest", "qwen2.5-coder")
    assert same_model("Qwen2.5-Coder:1.5b", "qwen2.5-coder:1.5b")
    assert not same_model("qwen2.5-coder:7b", "qwen2.5-coder:1.5b")


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
def test_clean(raw, prefix, suffix, length, fim, want):
    assert prompt.clean(raw, prefix, suffix, length, fim=fim) == want
