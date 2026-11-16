"""Scoped tokens: AI apps and the companion, each only where it belongs."""

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


def test_tokens_keep_working_until_revoked(tmp_path):
    now = [1000.0]
    tokens = AccessTokens(tmp_path / "auth.db", clock=lambda: now[0])
    try:
        entry, secret = tokens.create("Laptop", scope="companion")
        assert tokens.check(secret) is None
        assert tokens.check(secret, scope="companion")["id"] == entry["id"]
        now[0] += 5 * 365 * 24 * 60 * 60
        assert tokens.check(secret, scope="companion") is not None
        assert tokens.revoke(entry["id"])
        assert tokens.check(secret, scope="companion") is None
        assert tokens.list() == []
        for scope in ("all", "connector"):  # browser tokens are no longer made
            with pytest.raises(TokenError):
                tokens.create("Escalation", scope=scope)
    finally:
        tokens.close()


@pytest.mark.parametrize("addr", ["127.0.0.1", "192.0.2.10"])
def test_companion_and_mcp_tokens_are_not_interchangeable(client, svc, addr):
    _, companion = svc.tokens.create("Laptop", scope="companion")
    _, ai = svc.tokens.create("AI")
    remote = {"REMOTE_ADDR": addr}
    assert (
        client.post("/mcp", headers={"Authorization": f"Bearer {companion}"}, json={}, environ_base=remote).status_code
        == 401
    )
    assert (
        client.get("/api/connector/ping", headers={"Authorization": f"Bearer {ai}"}, environ_base=remote).status_code
        == 401
    )
    if addr == "127.0.0.1":
        return
    headers = {"Authorization": f"Bearer {companion}"}
    for path in ["/api/settings", "/api/notes/overview", "/api/mcp/tokens", "/api/devices"]:
        assert client.get(path, headers=headers, environ_base=remote).status_code == 401
    for path in ["/api/items/trash", "/api/notes", "/api/mcp/tokens"]:
        assert client.post(path, headers=headers, json={}, environ_base=remote).status_code == 403


def test_browser_tokens_can_no_longer_be_made_through_the_api(client):
    assert client.post("/api/mcp/tokens", json={"name": "Browser", "scope": "connector"}).status_code == 400
    assert client.post("/api/mcp/tokens", json={"name": "Bad", "scope": "all"}).status_code == 400


def test_remote_token_issuance_still_requires_admin_confirmation(client):
    remote = {"REMOTE_ADDR": "192.0.2.10"}
    response = client.post(
        "/api/mcp/tokens",
        json={"name": "Laptop", "scope": "companion"},
        environ_base=remote,
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 401
