"""Requests from other websites are refused (issue #17)."""

import io

import pytest

from backend.access import guard
from tests.conftest import make_pdf

EVIL = {"Origin": "https://evil.example"}

# Every state-changing route a web page could reach without a CORS preflight
# (no body, or a form/multipart body), plus JSON and DELETE routes for good measure.
CROSS_SITE_CALLS = [
    ("POST", "/api/trash/empty", {}),
    ("POST", "/api/settings/reset", {}),
    ("POST", "/api/fulltext/rebuild", {}),
    ("POST", "/api/fulltext/sync", {}),
    ("POST", "/api/notes/rescan", {}),
    ("POST", "/api/notes/attachments/clean", {}),
    ("POST", "/api/connector/save", {"data": {"meta": '{"title": "Injected"}'}}),
    ("POST", "/api/items", {"json": {"title": "Injected"}}),
    ("POST", "/api/items/trash", {"json": {"ids": ["x"]}}),
    ("DELETE", "/api/tags/1", {}),
]


@pytest.mark.parametrize("method,path,kw", CROSS_SITE_CALLS)
def test_foreign_origin_is_refused(client, method, path, kw):
    r = client.open(path, method=method, headers=EVIL, **kw)
    assert r.status_code == 403
    assert "app itself" in r.get_json()["error"]


@pytest.mark.parametrize("method,path,kw", CROSS_SITE_CALLS)
def test_rebinding_host_is_refused(client, method, path, kw):
    """A DNS-rebinding page sends its own host name — same origin as far as the browser knows."""
    headers = {"Host": "evil.example:8000", "Origin": "http://evil.example:8000"}
    assert client.open(path, method=method, headers=headers, **kw).status_code == 403


def test_rebinding_host_cannot_read(client):
    for path in ("/api/items", "/api/notes/overview", "/", "/api/settings"):
        r = client.get(path, headers={"Host": "evil.example:8000"})
        assert r.status_code == 403, path


def test_trash_survives_a_cross_site_empty(client, svc):
    item = client.post("/api/items", json={"title": "Keep me"}).get_json()
    client.post("/api/items/trash", json={"ids": [item["id"]]})
    assert client.post("/api/trash/empty", headers=EVIL).status_code == 403
    assert client.post("/api/trash/empty", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert svc.papers.trashed_ids() == [item["id"]]


def test_cross_site_upload_adds_nothing(client, svc, tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf", "Injected paper")
    r = client.post(
        "/api/items/from_pdf",
        headers=EVIL,
        data={"file": (io.BytesIO(pdf.read_bytes()), "x.pdf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 403
    assert svc.papers.list() == []


def test_same_origin_and_plain_clients_work(client):
    # the app's own pages
    r = client.post("/api/items", json={"title": "Mine"}, headers={"Origin": "http://localhost"})
    assert r.status_code in (200, 201)
    r = client.post(
        "/api/items",
        json={"title": "Mine 2"},
        headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code in (200, 201)
    # curl / scripts send no Origin
    assert client.post("/api/items", json={"title": "Script"}).status_code in (200, 201)
    # a bookmark or typed URL: Sec-Fetch-Site none
    assert client.post("/api/fulltext/sync", headers={"Sec-Fetch-Site": "none"}).status_code == 200
    # IPv6 loopback
    assert client.get("/api/settings", headers={"Host": "[::1]:8000"}).status_code == 200


def test_another_local_port_is_another_site(client):
    """A different app on localhost:3000 is not this app."""
    r = client.post("/api/trash/empty", headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:3000"})
    assert r.status_code == 403


def test_browser_extension_can_save(client, svc):
    r = client.post(
        "/api/connector/save",
        headers={"Origin": "chrome-extension://abcdef"},
        json={"title": "Saved from the button", "doi": "10.1/x", "options": {"fetch_metadata": False}},
    )
    assert r.status_code in (200, 201), r.get_json()
    assert client.get("/api/connector/ping", headers={"Origin": "moz-extension://xyz"}).status_code == 200


def test_allowed_hosts(tmp_path):
    from backend import create_app
    from backend.config import Config

    cfg = Config.from_env(tmp_path / "d")
    cfg.allowed_hosts = {"192.168.1.20"}
    c = create_app(cfg, start_indexer=False).test_client()
    assert c.get("/api/settings", headers={"Host": "192.168.1.20:8000"}).status_code == 200
    r = c.post(
        "/api/items", json={"title": "LAN"}, headers={"Host": "192.168.1.20:8000", "Origin": "http://192.168.1.20:8000"}
    )
    assert r.status_code in (200, 201)
    assert c.get("/api/settings", headers={"Host": "192.168.1.21:8000"}).status_code == 403


@pytest.mark.parametrize(
    "host,name",
    [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("LOCALHOST", "localhost"),
        ("[::1]:8000", "::1"),
        ("::1", "::1"),
        ("evil.example:80", "evil.example"),
    ],
)
def test_host_name(host, name):
    assert guard.host_name(host) == name
