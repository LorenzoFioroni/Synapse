"""Scoped tokens preserve old AI credentials and limit browser credentials."""

import sqlite3

import pytest

from backend.access.authdb import hash_secret
from backend.access.tokens import AccessTokens, TokenError


def test_legacy_tokens_migrate_without_gaining_connector_access(tmp_path):
    path = tmp_path / "auth.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE access_tokens (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
            created TEXT NOT NULL, last_used TEXT, last_addr TEXT)""")
        db.execute(
            "INSERT INTO access_tokens VALUES (?, ?, ?, ?, NULL, NULL)",
            ("legacy", "Existing AI app", hash_secret("original-secret"), "2026-01-01"),
        )
    tokens = AccessTokens(path)
    try:
        assert tokens.check("original-secret")["id"] == "legacy"
        assert tokens.check("original-secret", scope="connector") is None
        assert tokens.list()[0]["scope"] == "mcp"
        tokens.close()
        assert tokens.check("original-secret")["id"] == "legacy"
    finally:
        tokens.close()


def test_browser_tokens_keep_working_until_revoked(tmp_path):
    now = [1000.0]
    tokens = AccessTokens(tmp_path / "auth.db", clock=lambda: now[0])
    try:
        entry, secret = tokens.create("Browser", scope="connector")
        assert tokens.check(secret) is None
        assert tokens.check(secret, scope="connector")["id"] == entry["id"]
        now[0] += 5 * 365 * 24 * 60 * 60
        assert tokens.check(secret, scope="connector") is not None
        assert tokens.revoke(entry["id"])
        assert tokens.check(secret, scope="connector") is None
        assert tokens.list() == []
        with pytest.raises(TokenError):
            tokens.create("Escalation", scope="all")
    finally:
        tokens.close()


@pytest.mark.parametrize("addr", ["127.0.0.1", "192.0.2.10"])
def test_connector_and_mcp_tokens_are_not_interchangeable(client, svc, addr):
    _, browser = svc.tokens.create("Browser", scope="connector")
    _, ai = svc.tokens.create("AI")
    remote = {"REMOTE_ADDR": addr}
    assert (
        client.post("/mcp", headers={"Authorization": f"Bearer {browser}"}, json={}, environ_base=remote).status_code
        == 401
    )
    assert (
        client.get("/api/connector/ping", headers={"Authorization": f"Bearer {ai}"}, environ_base=remote).status_code
        == 401
    )
    if addr == "127.0.0.1":
        return
    browser_headers = {"Authorization": f"Bearer {browser}", "Origin": "chrome-extension://abc"}
    for path in ["/api/settings", "/api/notes/overview", "/api/mcp/tokens", "/api/devices"]:
        assert client.get(path, headers=browser_headers, environ_base=remote).status_code == 401
    for path in ["/api/items/trash", "/api/notes", "/api/mcp/tokens"]:
        assert client.post(path, headers=browser_headers, json={}, environ_base=remote).status_code == 403


def test_browser_token_creation_and_revocation_through_api(client, svc):
    response = client.post("/api/mcp/tokens", json={"name": "Browser", "scope": "connector"})
    assert response.status_code == 201
    data = response.get_json()
    assert data["entry"]["scope"] == "connector"
    assert "token_hash" not in data["entry"]
    headers = {"Authorization": f"Bearer {data['token']}", "Origin": "chrome-extension://abc"}
    assert (
        client.get("/api/connector/ping", headers=headers, environ_base={"REMOTE_ADDR": "192.0.2.10"}).status_code
        == 200
    )
    assert client.delete(f"/api/mcp/tokens/{data['entry']['id']}").status_code == 200
    for method, path in [
        ("GET", "/api/connector/ping"),
        ("POST", "/api/connector/save"),
        ("POST", "/api/connector/file"),
    ]:
        denied = client.open(path, method=method, headers=headers, environ_base={"REMOTE_ADDR": "192.0.2.10"})
        assert denied.status_code == 401
        assert denied.headers["Access-Control-Allow-Origin"] == headers["Origin"]
    assert client.post("/api/mcp/tokens", json={"name": "Bad", "scope": "all"}).status_code == 400


def test_remote_browser_token_issuance_still_requires_admin_confirmation(client):
    remote = {"REMOTE_ADDR": "192.0.2.10"}
    response = client.post(
        "/api/mcp/tokens",
        json={"name": "Browser", "scope": "connector"},
        environ_base=remote,
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 401
