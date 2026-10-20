"""The MCP server over HTTP (``/mcp``) and its access tokens. See backend/mcp/http.py,
backend/api/mcp.py and backend/access/tokens.py."""

import pytest

from backend import create_app
from backend.access.tokens import bearer
from backend.config import Config

OTHER_PC = "192.0.2.20"  # TEST-NET: never one of this computer's own addresses
LAN_HOST = "192.168.1.5:8000"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}


@pytest.fixture
def lan_app(tmp_path):
    cfg = Config.from_env(tmp_path / "data")
    cfg.lan = True
    app = create_app(cfg, start_indexer=False)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def mac(lan_app):
    return lan_app.test_client()


@pytest.fixture
def token(mac):
    r = mac.post("/api/mcp/tokens", json={"name": "Work laptop"})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["token"].startswith("syn_")
    return r.get_json()["token"]


def from_other_pc(c, message, token=None, **headers):
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return c.post("/mcp", json=message, headers={"Host": LAN_HOST, **headers}, environ_base={"REMOTE_ADDR": OTHER_PC})


def test_other_computer_uses_the_tools(mac, token):
    r = from_other_pc(mac, INIT, token)
    assert r.status_code == 200, r.get_json()
    res = r.get_json()["result"]
    assert res["protocolVersion"] == "2025-06-18" and res["serverInfo"]["name"] == "synapse"
    # a notification gets an empty 202
    r = from_other_pc(mac, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token)
    assert r.status_code == 202 and r.data == b""
    tools = from_other_pc(mac, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token).get_json()
    assert "search_library" in [t["name"] for t in tools["result"]["tools"]]
    mac.post("/api/items", json={"title": "Quantum transport in graphene"})
    r = from_other_pc(
        mac,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_library", "arguments": {"query": "graphene"}},
        },
        token,
    )
    assert r.status_code == 200
    assert "Quantum transport" in r.get_json()["result"]["content"][0]["text"]


def test_a_batch(mac, token):
    r = from_other_pc(mac, [INIT, {"jsonrpc": "2.0", "id": 2, "method": "ping"}], token)
    assert r.status_code == 200 and [m["id"] for m in r.get_json()] == [1, 2]


def test_no_token_no_access(mac, token):
    for who in ({"REMOTE_ADDR": OTHER_PC}, {}):  # another computer, and this one too
        r = mac.post("/mcp", json=INIT, headers={"Host": LAN_HOST}, environ_base=who)
        assert r.status_code == 401
        assert r.headers["WWW-Authenticate"].startswith("Bearer")
        assert "access token" in r.get_json()["error"]
    assert from_other_pc(mac, INIT, "syn_wrong").status_code == 401
    assert from_other_pc(mac, INIT, token.upper()).status_code == 401
    r = mac.post("/mcp", json=INIT, headers={"Authorization": f"Basic {token}"})
    assert r.status_code == 401


def test_a_paired_device_cookie_is_not_enough(mac):
    res = mac.post("/api/devices/pairing").get_json()
    code = res["links"][0]["url"].split("#code=", 1)[1]
    ipad = mac.application.test_client()
    env = {"REMOTE_ADDR": "192.0.2.10"}
    r = ipad.post(
        "/api/devices/pair",
        json={"code": code},
        headers={"Host": LAN_HOST, "Origin": f"http://{LAN_HOST}"},
        environ_base=env,
    )
    assert r.status_code == 201
    assert ipad.post("/mcp", json=INIT, headers={"Host": LAN_HOST}, environ_base=env).status_code == 401
    # nor may a paired device make or remove tokens
    app_page = {"Host": LAN_HOST, "Origin": f"http://{LAN_HOST}"}
    r = ipad.post("/api/mcp/tokens", json={"name": "x"}, headers=app_page, environ_base=env)
    assert r.status_code == 403
    assert ipad.get("/api/mcp/tokens", headers=app_page, environ_base=env).status_code == 200


def test_browsers_and_rebinding_are_refused(mac, token):
    assert from_other_pc(mac, INIT, token, Origin="http://evil.example").status_code == 403
    assert from_other_pc(mac, INIT, token, Origin=f"http://{LAN_HOST}").status_code == 200
    r = mac.post("/mcp", json=INIT, headers={"Host": "evil.example", "Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_bad_requests(mac, token):
    auth = {"Authorization": f"Bearer {token}"}
    r = mac.post("/mcp", data="{not json", headers=auth, content_type="application/json")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == -32700
    r = mac.post("/mcp", json=INIT, headers={**auth, "MCP-Protocol-Version": "1999-01-01"})
    assert r.status_code == 400
    assert mac.post("/mcp", json=INIT, headers={**auth, "MCP-Protocol-Version": "2025-06-18"}).status_code == 200
    for method in ("GET", "DELETE"):
        r = mac.open("/mcp", method=method, headers=auth)
        assert r.status_code == 405 and r.headers["Allow"] == "POST"


def test_tokens_are_listed_hashed_and_revocable(mac, token, lan_app):
    st = mac.get("/api/mcp/tokens").get_json()
    assert st["this_computer"] and st["lan"]
    assert [t["name"] for t in st["tokens"]] == ["Work laptop"]
    assert "token_hash" not in st["tokens"][0] and all(e["url"].endswith("/mcp") for e in st["endpoints"])
    from_other_pc(mac, INIT, token)
    used = mac.get("/api/mcp/tokens").get_json()["tokens"][0]
    assert used["last_used"] and used["last_addr"] == OTHER_PC
    raw = lan_app.extensions["synapse"].config.auth_db_path.read_bytes()
    assert token.encode() not in raw
    assert mac.delete(f"/api/mcp/tokens/{used['id']}").status_code == 200
    assert mac.delete(f"/api/mcp/tokens/{used['id']}").status_code == 404
    assert from_other_pc(mac, INIT, token).status_code == 401


def test_a_token_needs_a_name(mac):
    assert mac.post("/api/mcp/tokens", json={"name": "  "}).status_code == 400


def test_endpoint_on_this_computer_only(client):
    st = client.get("/api/mcp/tokens").get_json()
    assert not st["lan"] and st["endpoints"] == [{"label": "localhost", "url": "http://localhost/mcp"}]


def test_a_note_written_over_http_reaches_the_open_windows(mac, token, lan_app):
    svc = lan_app.extensions["synapse"]
    poke = svc.note_service.on_change
    first = mac.get("/api/changes", headers={"X-Synapse-Client": "page"}).get_json()
    r = from_other_pc(
        mac,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "write_note", "arguments": {"title": "From afar", "text": "Hello from the laptop."}},
        },
        token,
    )
    assert r.status_code == 200 and r.get_json()["result"]["isError"] is False, r.get_json()
    assert (svc.config.notes_dir / "ai" / "From afar.md").exists()
    later = mac.get(
        f"/api/changes?since={first['seq']}&epoch={first['epoch']}", headers={"X-Synapse-Client": "page"}
    ).get_json()
    assert "notes" in later["changes"]
    # the app's own hooks are left alone (the stdio server unhooks them in its own process only)
    assert svc.note_service.on_change == poke and poke is not None


def test_a_task_made_over_http_reaches_the_open_windows(mac, token, lan_app):
    svc = lan_app.extensions["synapse"]
    first = mac.get("/api/changes", headers={"X-Synapse-Client": "page"}).get_json()
    r = from_other_pc(
        mac,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "create_task", "arguments": {"title": "Ask the referee"}},
        },
        token,
    )
    assert r.status_code == 200 and r.get_json()["result"]["isError"] is False, r.get_json()
    assert [t["title"] for t in svc.task_store.list()] == ["Ask the referee"]
    later = mac.get(
        f"/api/changes?since={first['seq']}&epoch={first['epoch']}", headers={"X-Synapse-Client": "page"}
    ).get_json()
    assert "tasks" in later["changes"]


@pytest.mark.parametrize(
    "header,token",
    [("Bearer abc", "abc"), ("bearer  abc ", "abc"), ("Basic abc", None), ("Bearer", None), (None, None)],
)
def test_bearer(header, token):
    assert bearer(header) == token
