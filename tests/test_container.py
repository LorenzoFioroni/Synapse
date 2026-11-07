"""Synapse in a container behind a tunnel (``server.py --container``, docs/docker.md), and the
services a deployment runs next to it (``--local-service``).

In Docker, cloudflared runs in a container of its own: its requests come from a private address
of the Docker network (here 172.18.0.3), never from loopback. The host reaches the container
through the network's gateway (172.18.0.1).
"""

import os
import sys
from types import SimpleNamespace

import pytest

from backend import create_app
from backend.config import Config, LocalServiceError, parse_local_service, parse_local_services
from tests.test_public_url import PUBLIC, PUBLIC_URL, VISITOR

TUNNEL_CONTAINER = "172.18.0.3"
DOCKER_GATEWAY = "172.18.0.1"
CONTAINER_IP = "172.18.0.2"


@pytest.fixture
def app(tmp_path):
    cfg = Config.from_env(tmp_path / "data")
    cfg.public_url = PUBLIC_URL
    cfg.container = True
    app = create_app(cfg, start_indexer=False)
    app.config["TESTING"] = True
    return app


def request(c, method, path, *, host, addr, **kw):
    return c.open(path, method=method, base_url=f"http://{host}", environ_base={"REMOTE_ADDR": addr}, **kw)


def through_tunnel(c, method, path, **kw):
    headers = {"Origin": PUBLIC_URL, "Cf-Connecting-Ip": VISITOR, "X-Forwarded-For": VISITOR, **kw.pop("headers", {})}
    return request(c, method, path, host=PUBLIC, addr=TUNNEL_CONTAINER, headers=headers, **kw)


def pairing_code(c):
    """A code made inside the container, as ``docker compose exec … --pairing-link`` makes one."""
    r = request(c, "POST", "/api/devices/pairing", host="127.0.0.1:8000", addr="127.0.0.1")
    return r.get_json()["links"][0]["url"].split("code=")[1]


# ------------------------------------------------------------------ the guard in a container


def test_everything_through_the_tunnel_signs_in(app):
    c = app.test_client()
    for path in ("/", "/api/items", "/api/settings", "/mcp"):
        assert through_tunnel(c, "GET", path).status_code == 401, path
    assert through_tunnel(c, "POST", "/api/devices/pairing").status_code == 401
    assert (
        through_tunnel(
            c, "POST", "/api/items", json={"title": "x"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert through_tunnel(c, "GET", "/manifest.webmanifest").status_code == 200


def test_only_loopback_and_the_public_name_are_accepted(app):
    c = app.test_client()
    # the service name and the container's address: no LAN names in a container
    assert request(c, "GET", "/api/items", host="synapse:8000", addr=TUNNEL_CONTAINER).status_code == 403
    assert request(c, "GET", "/api/items", host=f"{CONTAINER_IP}:8000", addr=DOCKER_GATEWAY).status_code == 403
    # the host computer is another device, even when it names loopback
    assert request(c, "GET", "/api/items", host="127.0.0.1:8000", addr=DOCKER_GATEWAY).status_code == 401


def test_the_health_check_and_pairing_link_work_inside_the_container(app):
    c = app.test_client()
    assert request(c, "GET", "/manifest.webmanifest", host="127.0.0.1:8000", addr="127.0.0.1").status_code == 200
    r = request(c, "POST", "/api/devices/pairing", host="127.0.0.1:8000", addr="127.0.0.1")
    assert r.status_code == 201
    assert r.get_json()["links"][0]["url"].startswith(f"{PUBLIC_URL}/pair#code=")
    assert len(r.get_json()["links"]) == 1  # no LAN addresses


def test_the_visitor_address_comes_from_the_tunnel_container(app):
    c = app.test_client()
    code = pairing_code(c)
    r = through_tunnel(c, "POST", "/api/devices/pair", json={"code": code, "name": "iPhone"})
    assert r.status_code == 201
    assert r.get_json()["device"]["last_addr"] == VISITOR


def test_outside_a_container_a_private_peer_reports_no_visitor(tmp_path):
    cfg = Config.from_env(tmp_path / "data")
    cfg.public_url = PUBLIC_URL
    c = create_app(cfg, start_indexer=False).test_client()
    code = pairing_code(c)
    r = through_tunnel(c, "POST", "/api/devices/pair", json={"code": code, "name": "iPhone"})
    assert r.get_json()["device"]["last_addr"] == TUNNEL_CONTAINER


@pytest.mark.parametrize("peer", ["192.0.2.10", "198.51.100.4", "8.8.8.8"])
def test_public_and_documentation_addresses_are_not_trusted_proxies(peer):
    from backend.access.proxy import _client

    environ = {"REMOTE_ADDR": peer, "HTTP_CF_CONNECTING_IP": VISITOR}
    assert _client(environ, private_peers=True) == peer
    assert _client({**environ, "REMOTE_ADDR": "10.1.2.3"}, private_peers=True) == VISITOR
    assert _client({**environ, "REMOTE_ADDR": "fd00::5"}, private_peers=True) == VISITOR


def test_factory_refuses_a_container_without_a_tunnel(tmp_path):
    cfg = Config.from_env(tmp_path / "data")
    cfg.container = True
    with pytest.raises(ValueError, match="container"):
        create_app(cfg, start_indexer=False)


# ------------------------------------------------------------------ server.py --container


@pytest.fixture
def started(monkeypatch):
    """Run ``server.main()`` with these arguments; returns (config, run's keyword arguments, umask)."""
    import server

    seen: dict = {}
    monkeypatch.setattr(server, "ensure_built", lambda _: None)
    monkeypatch.setattr(os, "umask", lambda mask: seen.setdefault("umask", mask))

    class App:
        extensions = {"synapse": SimpleNamespace(backups=SimpleNamespace(stop=lambda: seen.update(stopped=True)))}

        def run(self, **kwargs):
            seen["run"] = kwargs

    def create(config):
        seen["config"] = config
        return App()

    monkeypatch.setattr(server, "create_app", create)

    def start(*args):
        monkeypatch.setattr(sys, "argv", ["server.py", *args])
        server.main()
        assert seen["stopped"]
        return seen["config"], seen["run"], seen.get("umask")

    return start


def test_container_listens_on_its_network_without_lan_mode(started):
    config, run, umask = started("--container", "--public-url", PUBLIC_URL)
    assert run["host"] == "0.0.0.0" and run["port"] == 8000 and run["threaded"] and not run["debug"]
    assert config.container and not config.lan and config.public_url == PUBLIC_URL
    assert config.allowed_hosts == set()
    assert umask == 0o077


def test_container_needs_a_public_url(monkeypatch):
    import server

    monkeypatch.setattr(sys, "argv", ["server.py", "--container"])
    with pytest.raises(SystemExit, match="needs --public-url"):
        server.main()


@pytest.mark.parametrize(
    "args",
    [
        ["--lan"],
        ["--cert", "c.pem", "--key", "k.pem"],
        ["--window"],
        ["--debug"],
        ["--allow-host", "server.local"],
        ["--make-shortcut"],
        ["--quit-when-closed"],
        ["--host", "0.0.0.0"],
    ],
)
def test_container_refuses_options_around_the_tunnel(monkeypatch, args):
    import server

    monkeypatch.setattr(os, "umask", lambda mask: pytest.fail("refused options must not reach umask"))
    monkeypatch.setattr(sys, "argv", ["server.py", "--container", "--public-url", PUBLIC_URL, *args])
    with pytest.raises(SystemExit, match="--container cannot be combined with"):
        server.main()


# ------------------------------------------------------------------ local services


def test_local_services_from_flags_and_environment(started, monkeypatch):
    monkeypatch.setenv("SYNAPSE_LOCAL_SERVICES", "ollama=http://ollama:11434, other=https://Other.example")
    config, _, _ = started("--container", "--public-url", PUBLIC_URL, "--local-service", "ollama=http://models:11434")
    assert config.local_services == {"ollama": "http://models:11434", "other": "https://other.example"}


def test_a_bad_local_service_stops_the_server(started):
    with pytest.raises(SystemExit, match="NAME=URL"):
        started("--local-service", "http://ollama:11434")


@pytest.mark.parametrize(
    "given,parsed",
    [
        ("ollama=http://ollama:11434", ("ollama", "http://ollama:11434")),
        ("Ollama=HTTP://Ollama:11434/", ("ollama", "http://ollama:11434")),
        ("models=https://[fd00::2]:8443", ("models", "https://[fd00::2]:8443")),
        ("x=http://x", ("x", "http://x")),
    ],
)
def test_local_service_is_parsed(given, parsed):
    assert parse_local_service(given) == parsed


@pytest.mark.parametrize(
    "bad",
    [
        "http://ollama:11434",
        "=http://ollama:11434",
        "1st=http://ollama",
        "ollama=ollama:11434",
        "ollama=ftp://ollama",
        "ollama=http://ollama:11434/api",
        "ollama=http://user:pw@ollama:11434",
        "ollama=http://ollama:port",
        "ollama=http://",
    ],
)
def test_bad_local_services_are_refused(bad):
    with pytest.raises(LocalServiceError):
        parse_local_service(bad)


def test_local_services_list():
    assert parse_local_services(None) == {}
    assert parse_local_services(" a=http://a:1,b=http://b:2  c=http://c ") == {
        "a": "http://a:1",
        "b": "http://b:2",
        "c": "http://c",
    }
