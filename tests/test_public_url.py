"""Synapse behind a tunnel (``--public-url``). See backend/access/proxy.py.

The tunnel (Cloudflare Tunnel's ``cloudflared``) runs on this computer and forwards the internet's
requests to ``http://127.0.0.1:<port>`` with the visitor's ``Host``: here that is a test client
connecting from 127.0.0.1 over plain HTTP, naming the public host.
"""

import socket
import sys

import pytest

from backend import create_app
from backend.access.pairing_link import PairingLinkError, describe, request_pairing
from backend.access.proxy import PublicUrlError, parse_public_url
from backend.config import Config
from tests.test_passkeys import Authenticator

PUBLIC = "synapse.example.com"
PUBLIC_URL = f"https://{PUBLIC}"
VISITOR = "203.0.113.7"  # TEST-NET-3


@pytest.fixture
def app(tmp_path):
    cfg = Config.from_env(tmp_path / "data")
    cfg.public_url = PUBLIC_URL
    app = create_app(cfg, start_indexer=False)
    app.config["TESTING"] = True
    return app


def tunnel(c, method, path, **kw):
    """A request as cloudflared passes it on: from loopback, plain HTTP, the public Host, Cloudflare's headers."""
    headers = {
        "Origin": PUBLIC_URL,
        "Cf-Connecting-Ip": VISITOR,
        "X-Forwarded-For": VISITOR,
        "X-Forwarded-Proto": "https",
        **kw.pop("headers", {}),
    }
    headers = {k: v for k, v in headers.items() if v is not None}  # None: leave the header out
    return c.open(
        path,
        method=method,
        base_url=f"http://{PUBLIC}",
        headers=headers,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        **kw,
    )


def local(c, method, path, **kw):
    return c.open(
        path, method=method, base_url="http://127.0.0.1:8000", environ_base={"REMOTE_ADDR": "127.0.0.1"}, **kw
    )


def test_requests_through_the_tunnel_need_a_signed_in_device(app):
    c = app.test_client()
    assert tunnel(c, "GET", "/api/items").status_code == 401
    page = tunnel(c, "GET", "/")
    assert page.status_code == 401 and b"Passkey" in page.data
    # the administrative actions too
    assert tunnel(c, "POST", "/api/devices/pairing").status_code == 401
    assert tunnel(c, "POST", "/api/mcp/tokens", json={"name": "x"}).status_code == 401
    # what an unpaired device may still open
    assert tunnel(c, "GET", "/manifest.webmanifest").status_code == 200
    # the same server, opened on this computer itself, needs nothing
    assert local(c, "GET", "/api/items").status_code == 200


def test_even_without_cloudflare_headers(app):
    c = app.test_client()
    r = c.open("/api/items", base_url=f"http://{PUBLIC}", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 401


def test_proxy_headers_on_loopback_are_never_this_computer(tmp_path):
    cfg = Config.from_env(tmp_path / "data")  # no --public-url: e.g. a proxy set up by hand
    app = create_app(cfg, start_indexer=False)
    c = app.test_client()
    assert local(c, "GET", "/api/items").status_code == 200
    for header in ("X-Forwarded-For", "Cf-Connecting-Ip", "X-Real-Ip", "Forwarded"):
        r = local(c, "GET", "/api/items", headers={header: "for=198.51.100.1" if header == "Forwarded" else VISITOR})
        assert r.status_code == 401, header


def test_other_hosts_are_still_refused(app):
    c = app.test_client()
    r = c.open("/api/items", base_url="http://evil.example", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 403


def test_pair_add_a_passkey_sign_in_and_confirm_through_the_tunnel(app):
    mac = app.test_client()  # e.g. --pairing-link on the server
    links = local(mac, "POST", "/api/devices/pairing").get_json()["links"]
    assert links[0]["url"].startswith(f"{PUBLIC_URL}/pair#code=")
    code = links[0]["url"].split("code=")[1]

    phone = app.test_client()
    r = tunnel(phone, "POST", "/api/devices/pair", json={"code": code, "name": "iPhone"})
    assert r.status_code == 201
    cookie = r.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie  # the visitor's connection is HTTPS
    assert r.get_json()["device"]["last_addr"] == VISITOR
    assert tunnel(phone, "GET", "/api/items").status_code == 200

    # passkeys work at the public name
    st = tunnel(phone, "GET", "/api/passkeys").get_json()
    assert st["rp_id"] == PUBLIC and st["unavailable"] is None
    auth = Authenticator()
    o = tunnel(phone, "POST", "/api/passkeys/register/options", json={"name": "Synapse"}).get_json()
    reg = tunnel(phone, "POST", "/api/passkeys/register", json={"credential": auth.create(o, origin=PUBLIC_URL)})
    assert reg.status_code == 201, reg.get_json()

    ipad = app.test_client()
    o = tunnel(ipad, "POST", "/api/passkeys/login/options").get_json()
    r = tunnel(ipad, "POST", "/api/passkeys/login", json={"credential": auth.get(o, origin=PUBLIC_URL), "name": "iPad"})
    assert r.status_code == 201

    # an access token for an AI app, after confirming with the passkey
    assert tunnel(ipad, "POST", "/api/mcp/tokens", json={"name": "Laptop"}).get_json()["confirm"] is True
    o = tunnel(ipad, "POST", "/api/passkeys/confirm/options").get_json()
    assert (
        tunnel(ipad, "POST", "/api/passkeys/confirm", json={"credential": auth.get(o, origin=PUBLIC_URL)}).status_code
        == 200
    )
    made = tunnel(ipad, "POST", "/api/mcp/tokens", json={"name": "Laptop"})
    assert made.status_code == 201
    ep = tunnel(ipad, "GET", "/api/mcp/tokens").get_json()["endpoints"]
    assert ep[0]["url"] == f"{PUBLIC_URL}/mcp"

    # the AI app reaches /mcp through the tunnel with it, and not without
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    no = tunnel(app.test_client(), "POST", "/mcp", json=body, headers={"Origin": None})
    assert no.status_code == 401
    yes = tunnel(
        app.test_client(),
        "POST",
        "/mcp",
        json=body,
        headers={"Origin": None, "Authorization": f"Bearer {made.get_json()['token']}"},
    )
    assert yes.status_code == 200


def test_changes_need_the_public_origin(app):
    c = app.test_client()
    code = local(c, "POST", "/api/devices/pairing").get_json()["links"][0]["url"].split("code=")[1]
    assert tunnel(c, "POST", "/api/devices/pair", json={"code": code}).status_code == 201
    r = tunnel(c, "POST", "/api/tags", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "url",
    [
        "http://synapse.example.com",
        "https://",
        "https://synapse.example.com/app",
        "https://synapse.example.com:8443",
        "https://192.0.2.1",
        "synapse.example.com",
    ],
)
def test_bad_public_urls(url):
    with pytest.raises(PublicUrlError):
        parse_public_url(url)


def test_public_url_is_normalized():
    assert parse_public_url(" https://Synapse.Example.com/ ") == "https://synapse.example.com"
    assert parse_public_url("https://synapse.example.com:443") == "https://synapse.example.com"


def test_pairing_link_text():
    text = describe(
        {
            "expires": 1000 + 600,
            "links": [{"url": f"{PUBLIC_URL}/pair#code=abc"}, {"url": "http://a.local:8000/pair#code=abc"}],
        },
        now=1000,
    )
    assert f"{PUBLIC_URL}/pair#code=abc" in text and "10 minutes" in text and "a.local" in text


def test_pairing_link_needs_a_running_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(PairingLinkError, match="not running"):
        request_pairing(port, timeout=2)


def test_debug_is_refused_with_a_public_url(monkeypatch):
    import server

    monkeypatch.setattr(sys, "argv", ["server.py", "--public-url", PUBLIC_URL, "--debug"])
    with pytest.raises(SystemExit, match="debug"):
        server.main()
    monkeypatch.setattr(sys, "argv", ["server.py", "--public-url", "http://x.example"])
    with pytest.raises(SystemExit, match="https"):
        server.main()


@pytest.mark.parametrize(
    "args",
    [
        ["--lan"],
        ["--lan", "--host", "::1"],
        ["--host", "0.0.0.0"],
        ["--host", "::"],
        ["--host", "192.168.1.5"],
        ["--host", "server.local"],
    ],
)
def test_public_url_refuses_network_listening(monkeypatch, args):
    import server

    monkeypatch.setattr(sys, "argv", ["server.py", "--public-url", PUBLIC_URL, *args])
    with pytest.raises(SystemExit, match="--public-url cannot be combined"):
        server.main()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_public_url_accepts_loopback_listening(monkeypatch, host):
    import server

    monkeypatch.setattr(sys, "argv", ["server.py", "--public-url", PUBLIC_URL, "--host", host, "--no-browser"])
    monkeypatch.setattr(server, "ensure_built", lambda _: None)

    class App:
        def run(self, **kwargs):
            assert kwargs["host"] == host

    def create(config):
        assert config.public_url == PUBLIC_URL
        assert not config.lan
        return App()

    monkeypatch.setattr(server, "create_app", create)
    server.main()


def test_factory_refuses_public_url_with_lan(tmp_path):
    cfg = Config.from_env(tmp_path / "data")
    cfg.public_url = PUBLIC_URL
    cfg.lan = True
    with pytest.raises(ValueError, match="loopback-only"):
        create_app(cfg, start_indexer=False)
    assert not cfg.data_dir.exists()
