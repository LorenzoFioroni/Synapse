"""plugin.http: allowed hosts, public addresses, errors, redirects, caching, spacing, size limits."""

import http.server
import threading
import time

import pytest

from backend.plugins import http as H
from backend.plugins.errors import PluginError
from backend.plugins.http import HttpError, Offline, PluginHttp, allowed


class Handler(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.hits.append((self.path, dict(self.headers)))
        if self.path == "/json":
            self._send(200, b'{"a": 1}', {"Content-Type": "application/json"})
        elif self.path == "/etag":
            if self.headers.get("If-None-Match") == '"v1"':
                self.send_response(304)
                self.end_headers()
            else:
                self._send(200, b"cached body", {"ETag": '"v1"'})
        elif self.path == "/missing":
            self._send(404, b"no")
        elif self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.end_headers()
        elif self.path == "/away":
            self.send_response(302)
            self.send_header("Location", "https://example.com/x")
            self.end_headers()
        elif self.path == "/big":
            self._send(200, b"x" * 5000)
        else:
            self._send(200, self.path.encode())

    def _send(self, status, body, headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(H, "DEFAULT_GAP", 0.0)
    Handler.hits.clear()


def client(hosts=("127.0.0.1",)):
    return PluginHttp("demo", list(hosts), lambda: "Synapse/test (plugin demo)", private=True)


def test_hosts_match_the_permission():
    assert allowed("export.arxiv.org", ["export.arxiv.org"])
    assert not allowed("arxiv.org", ["export.arxiv.org"])
    assert allowed("export.arxiv.org", ["*.arxiv.org"]) and allowed("arxiv.org", ["*.arxiv.org"])
    assert not allowed("evilarxiv.org", ["*.arxiv.org"])


def test_get_json_with_params_and_user_agent(server):
    r = client().get(server + "/json")
    assert r.status == 200 and r.json() == {"a": 1} and not r.from_cache
    r = client().get(server + "/echo", params={"q": "a b"})
    assert r.text == "/echo?q=a+b"
    assert Handler.hits[-1][1]["User-Agent"] == "Synapse/test (plugin demo)"


def test_a_host_not_in_the_manifest_is_refused(server):
    with pytest.raises(PluginError, match='not in the plugin\'s "network" permission'):
        client(hosts=["example.org"]).get(server + "/json")
    with pytest.raises(PluginError, match="names no host"):
        client(hosts=[]).get(server + "/json")
    assert not Handler.hits


def test_this_computer_is_refused_unless_allowed(server):
    strict = PluginHttp("demo", ["127.0.0.1"], lambda: "ua")
    with pytest.raises(HttpError, match="this computer or the local network"):
        strict.get(server + "/json")


def test_errors_redirects_and_size(server):
    with pytest.raises(HttpError) as e:
        client().get(server + "/missing")
    assert e.value.status == 404
    assert client().get(server + "/missing", raise_for_status=False).status == 404
    assert client().get(server + "/hop").json() == {"a": 1}
    with pytest.raises(PluginError, match="example.com is not in"):
        client().get(server + "/away")  # a redirect to a host it may not reach
    with pytest.raises(HttpError, match="larger than"):
        client().get(server + "/big", max_bytes=1000)


def test_unreachable_is_offline():
    with pytest.raises(Offline):
        client().get("http://127.0.0.1:9/nothing", timeout=2)


def test_answers_are_cached_by_etag(server):
    c = client()
    first = c.get(server + "/etag")
    again = c.get(server + "/etag")
    assert first.text == again.text == "cached body"
    assert again.from_cache and Handler.hits[-1][1].get("If-None-Match") == '"v1"'


def test_requests_to_one_host_are_spaced(server, monkeypatch):
    monkeypatch.setattr(H, "GAPS_STATE", H._Gaps())
    c = client()
    c.limit("127.0.0.1", 0.3)
    start = time.monotonic()
    for _ in range(3):
        c.get(server + "/x")
    assert time.monotonic() - start >= 0.55
    assert H._gap_for("export.arxiv.org") == 3.0 and H._gap_for("example.org") == H.DEFAULT_GAP
