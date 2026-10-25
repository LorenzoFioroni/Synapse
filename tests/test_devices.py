"""Other devices on the network must be paired. See backend/access/devices.py and backend/access/guard.py."""

import re
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from backend import create_app
from backend.access import devices as devmod
from backend.config import Config

ROOT = Path(__file__).resolve().parents[1]

IPAD = "192.0.2.10"  # TEST-NET: never one of this computer's own addresses
LAN_HOST = "192.168.1.5:8000"
REMOTE = {"REMOTE_ADDR": IPAD}
APP = {"Host": LAN_HOST, "Origin": f"http://{LAN_HOST}"}


@pytest.fixture
def lan_app(tmp_path):
    cfg = Config.from_env(tmp_path / "data")
    cfg.lan = True
    app = create_app(cfg, start_indexer=False)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def mac(lan_app):
    """The computer running Synapse (loopback)."""
    return lan_app.test_client()


@pytest.fixture
def ipad(lan_app):
    return lan_app.test_client()


def remote(c, method, path, **kw):
    headers = {**APP, **kw.pop("headers", {})}
    return c.open(path, method=method, headers=headers, environ_base=REMOTE, **kw)


def pair(mac, ipad, name="iPad"):
    res = mac.post("/api/devices/pairing")
    assert res.status_code == 201, res.get_json()
    url = res.get_json()["links"][0]["url"]
    code = url.split("#code=", 1)[1]
    r = remote(ipad, "POST", "/api/devices/pair", json={"code": code, "name": name})
    assert r.status_code == 201, r.get_json()
    return code, r


def test_unpaired_device_gets_nothing(ipad):
    for path in (
        "/api/items",
        "/api/settings",
        "/api/notes/overview",
        "/api/devices",
        "/viewer.html",
        "/js/main.js",
        "/api/attachments/x/file",
    ):
        r = remote(ipad, "GET", path)
        assert r.status_code == 401, path
    r = remote(ipad, "GET", "/")
    assert r.status_code == 401 and b"isn't paired" in r.data
    assert remote(ipad, "GET", "/api/items").get_json()["paired"] is False
    r = remote(ipad, "POST", "/api/items", json={"title": "Injected"})
    assert r.status_code == 401


def test_pairing_page_and_icon_are_open(ipad):
    assert remote(ipad, "GET", "/pair").status_code == 200
    assert remote(ipad, "GET", "/assets/favicon.svg").status_code == 200


def test_home_screen_icon_and_manifest_need_no_cookie(ipad):
    # Browsers fetch the manifest without cookies and iOS the Home Screen icon too: a paired
    # device's request for them looks like an unpaired one.
    for path, kind in (
        ("/manifest.webmanifest", "application/manifest+json"),
        ("/assets/apple-touch-icon.png", "image/png"),
        ("/apple-touch-icon.png", "image/png"),
        ("/apple-touch-icon-precomposed.png", "image/png"),
        ("/assets/icon-192.png", "image/png"),
        ("/assets/icon-512.png", "image/png"),
        ("/assets/favicon-32.png", "image/png"),
    ):
        r = remote(ipad, "GET", path)
        assert r.status_code == 200 and r.mimetype == kind, (path, r.status_code, r.mimetype)
    icons = [i["src"] for i in remote(ipad, "GET", "/manifest.webmanifest").get_json()["icons"]]
    assert all(remote(ipad, "GET", src).status_code == 200 for src in icons)
    # the exact addresses the app's page links to, version tags included
    page = (ROOT / "frontend" / "index.html").read_text()
    links = re.findall(r'<link rel="(?:apple-touch-icon|icon|manifest)"[^>]*href="([^"]+)"', page)
    assert any("apple-touch-icon" in href for href in links)
    for href in links:
        r = remote(ipad, "GET", href)
        assert r.status_code == 200 and r.mimetype != "text/html", (href, r.status_code)
    # nothing else of the app's files
    assert remote(ipad, "GET", "/assets/../index.html").status_code in (401, 404)
    assert remote(ipad, "GET", "/js/main.js").status_code == 401


def test_this_computer_needs_no_pairing(mac):
    assert mac.get("/api/items").status_code == 200
    assert mac.post("/api/items", json={"title": "From the Mac"}).status_code in (200, 201)
    # also through IPv4-mapped loopback
    assert mac.get("/api/items", environ_base={"REMOTE_ADDR": "::ffff:127.0.0.1"}).status_code == 200


def test_own_lan_address_counts_as_this_computer(mac, monkeypatch):
    from backend.access import lan

    monkeypatch.setattr(lan, "own_addresses", lambda: ["192.168.1.5"])
    r = mac.get("/api/items", headers={"Host": LAN_HOST}, environ_base={"REMOTE_ADDR": "192.168.1.5"})
    assert r.status_code == 200


def test_pair_then_use(mac, ipad):
    _, r = pair(mac, ipad)
    cookie = r.headers.get("Set-Cookie")
    assert devmod.COOKIE in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert remote(ipad, "GET", "/api/items").status_code == 200
    assert remote(ipad, "GET", "/").status_code == 200
    r = remote(ipad, "POST", "/api/items", json={"title": "From the iPad"})
    assert r.status_code in (200, 201), r.get_json()
    st = remote(ipad, "GET", "/api/devices").get_json()
    assert st["current"] == st["devices"][0]["id"] and st["devices"][0]["name"] == "iPad"
    assert st["this_computer"] is False
    assert mac.get("/api/devices").get_json()["this_computer"] is True


def test_code_is_single_use_and_expires(lan_app, mac, ipad):
    code, _ = pair(mac, ipad)
    other = lan_app.test_client()
    r = remote(other, "POST", "/api/devices/pair", json={"code": code})
    assert r.status_code == 401
    # expiry
    store = lan_app.extensions["synapse"].devices
    t = [1000.0]
    store.clock = lambda: t[0]
    code2, _ = store.new_pairing_code()
    t[0] += devmod.PAIRING_TTL + 1
    assert remote(other, "POST", "/api/devices/pair", json={"code": code2}).status_code == 401
    assert remote(other, "POST", "/api/devices/pair", json={"code": "guess"}).status_code == 401


def test_failed_attempts_are_limited(lan_app, mac, ipad):
    for _ in range(devmod.FAILED_LIMIT):
        assert remote(ipad, "POST", "/api/devices/pair", json={"code": "wrong"}).status_code == 401
    code = mac.post("/api/devices/pairing").get_json()["links"][0]["url"].split("#code=")[1]
    r = remote(ipad, "POST", "/api/devices/pair", json={"code": code})
    assert r.status_code == 429


def test_only_this_computer_makes_pairing_codes(mac, ipad):
    pair(mac, ipad)
    assert remote(ipad, "POST", "/api/devices/pairing").status_code == 403


def test_pairing_needs_lan_mode(client):
    r = client.post("/api/devices/pairing")
    assert r.status_code == 409 and "--lan" in r.get_json()["error"]
    assert client.get("/api/devices").get_json()["lan"] is False


def test_pairing_links_carry_code_in_fragment_and_qr(mac):
    links = mac.post("/api/devices/pairing", headers={"Host": "127.0.0.1:8000"}).get_json()["links"]
    for link in links:
        assert "/pair#code=" in link["url"] and ":8000/" in link["url"]
        assert link["qr"].startswith("<svg") and "viewBox=" in link["qr"]  # viewBox: scales with the CSS size


def test_pairing_without_segno_still_gives_links(mac, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_segno(name, *a, **k):
        if name == "segno":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_segno)
    res = mac.post("/api/devices/pairing", headers={"Host": "127.0.0.1:8000"}).get_json()
    assert res["links"] and all(l["qr"] is None and "#code=" in l["url"] for l in res["links"])


def test_revoked_device_is_locked_out(mac, ipad):
    pair(mac, ipad)
    dev = mac.get("/api/devices").get_json()["devices"][0]
    assert mac.delete(f"/api/devices/{dev['id']}").status_code == 200
    assert remote(ipad, "GET", "/api/items").status_code == 401


def test_sign_out_this_device(mac, ipad):
    pair(mac, ipad)
    dev = remote(ipad, "GET", "/api/devices").get_json()["current"]
    r = remote(ipad, "DELETE", f"/api/devices/{dev}")
    assert r.status_code == 200 and f"{devmod.COOKIE}=;" in r.headers.get("Set-Cookie")
    assert remote(ipad, "GET", "/api/items").status_code == 401


def test_new_code_replaces_the_old_one(lan_app, mac):
    old = mac.post("/api/devices/pairing").get_json()["links"][0]["url"].split("#code=")[1]
    new = mac.post("/api/devices/pairing").get_json()["links"][0]["url"].split("#code=")[1]
    a, b = lan_app.test_client(), lan_app.test_client()
    assert remote(a, "POST", "/api/devices/pair", json={"code": old}).status_code == 401
    assert remote(b, "POST", "/api/devices/pair", json={"code": new}).status_code == 201


def test_cancel_voids_the_code(lan_app, mac, ipad):
    code = mac.post("/api/devices/pairing").get_json()["links"][0]["url"].split("#code=")[1]
    assert mac.delete("/api/devices/pairing").get_json() == {"ok": True}
    assert remote(ipad, "POST", "/api/devices/pair", json={"code": code}).status_code == 401
    assert mac.delete("/api/devices/pairing").status_code == 200  # nothing to cancel: still fine


def test_only_this_computer_cancels_codes(lan_app, mac, ipad):
    pair(mac, ipad)
    code = mac.post("/api/devices/pairing").get_json()["links"][0]["url"].split("#code=")[1]
    assert remote(ipad, "DELETE", "/api/devices/pairing").status_code == 403
    r = remote(lan_app.test_client(), "POST", "/api/devices/pair", json={"code": code})
    assert r.status_code == 201  # the code still works


def test_failed_attempts_are_forgotten_after_the_window(lan_app):
    store = lan_app.extensions["synapse"].devices
    t = [1000.0]
    store.clock = lambda: t[0]
    for addr in ("192.0.2.20", "192.0.2.21"):
        with pytest.raises(devmod.PairingError):
            store.redeem("wrong", "", "", addr)
    assert set(store._failed) == {"192.0.2.20", "192.0.2.21"}
    t[0] += devmod.FAILED_WINDOW + 1
    with pytest.raises(devmod.PairingError):
        store.redeem("wrong", "", "", "192.0.2.22")
    assert set(store._failed) == {"192.0.2.22"}  # the addresses that stopped trying are gone


def test_paired_device_cannot_manage_other_devices(lan_app, mac, ipad):
    pair(mac, ipad, name="iPad")
    phone = lan_app.test_client()
    pair(mac, phone, name="Phone")
    st = remote(ipad, "GET", "/api/devices").get_json()
    me = st["current"]
    other = next(d["id"] for d in st["devices"] if d["id"] != me)
    r = remote(ipad, "PATCH", f"/api/devices/{other}", json={"name": "Mine now"})
    assert r.status_code == 403 and "computer running Synapse" in r.get_json()["error"]
    assert remote(ipad, "DELETE", f"/api/devices/{other}").status_code == 403
    assert remote(ipad, "DELETE", "/api/devices/no-such-id").status_code == 403  # no hint whether it exists
    names = {d["id"]: d["name"] for d in mac.get("/api/devices").get_json()["devices"]}
    assert names[other] == "Phone"
    assert remote(phone, "GET", "/api/items").status_code == 200  # still paired
    # a device may still rename itself and sign itself out
    r = remote(ipad, "PATCH", f"/api/devices/{me}", json={"name": "Reading iPad"})
    assert r.status_code == 200 and r.get_json()["device"]["name"] == "Reading iPad"
    assert remote(ipad, "DELETE", f"/api/devices/{me}").status_code == 200
    # and the computer running Synapse manages them all
    assert mac.patch(f"/api/devices/{other}", json={"name": "Old phone"}).status_code == 200
    assert mac.delete(f"/api/devices/{other}").status_code == 200
    assert remote(phone, "GET", "/api/items").status_code == 401


@pytest.mark.parametrize("args", [["--lan"], ["--host", "0.0.0.0"], ["--host", "192.168.1.5"]])
def test_debug_is_refused_on_the_network(tmp_path, args):
    """Werkzeug's debugger runs before the guard: it must never listen on the network."""
    root = Path(__file__).resolve().parents[1]
    p = subprocess.run(
        [sys.executable, str(root / "server.py"), *args, "--debug", "--no-browser", "--data-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=root,
    )
    assert p.returncode != 0 and "--debug cannot be combined with --lan" in p.stderr
    assert not any(tmp_path.iterdir())  # stopped before touching the library


def test_rename(mac, ipad):
    pair(mac, ipad, name="")
    dev = mac.get("/api/devices").get_json()["devices"][0]
    assert dev["name"] == "Device"  # the test client sends no user agent
    r = mac.patch(f"/api/devices/{dev['id']}", json={"name": "Reading iPad"})
    assert r.get_json()["device"]["name"] == "Reading iPad"
    assert mac.patch(f"/api/devices/{dev['id']}", json={"name": "  "}).status_code == 400


def test_paired_device_still_needs_the_apps_origin(mac, ipad):
    pair(mac, ipad)
    evil = {"Origin": "https://evil.example"}
    assert remote(ipad, "POST", "/api/items", json={"title": "x"}, headers=evil).status_code == 403
    # an extension on the iPad is not this computer's "Save to library" button
    ext = {"Origin": "chrome-extension://abc"}
    assert remote(ipad, "POST", "/api/connector/save", json={"title": "x"}, headers=ext).status_code == 403
    # no Origin at all from another device: only a same-origin fetch
    bare = ipad.open("/api/items", method="POST", json={"title": "x"}, headers={"Host": LAN_HOST}, environ_base=REMOTE)
    assert bare.status_code == 403
    same = ipad.open(
        "/api/items",
        method="POST",
        json={"title": "x"},
        environ_base=REMOTE,
        headers={"Host": LAN_HOST, "Sec-Fetch-Site": "same-origin"},
    )
    assert same.status_code in (200, 201)


def test_lan_mode_hosts(mac, ipad, monkeypatch):
    from backend.access import lan

    monkeypatch.setattr(lan, "local_hostname", lambda: "lorenzos-mac.local")
    for host in ("10.0.0.7:8000", "lorenzos-mac.local:8000", "[fe80::1]:8000"):
        r = ipad.get("/api/settings", headers={"Host": host}, environ_base=REMOTE)
        assert r.status_code == 401, host  # past the Host check; only the pairing is missing
        assert mac.get("/api/settings", headers={"Host": host}).status_code == 200, host
    # a DNS name that isn't this computer's is still refused (rebinding)
    assert ipad.get("/api/settings", headers={"Host": "evil.example:8000"}, environ_base=REMOTE).status_code == 403
    assert mac.get("/api/settings", headers={"Host": "evil.example:8000"}).status_code == 403


def test_without_lan_mode_remote_needs_pairing_too(tmp_path):
    """--host 0.0.0.0 --allow-host x used to let anyone on the network in; now pairing is required."""
    cfg = Config.from_env(tmp_path / "d")
    cfg.allowed_hosts = {"192.168.1.20"}
    c = create_app(cfg, start_indexer=False).test_client()
    r = c.get("/api/settings", headers={"Host": "192.168.1.20:8000"}, environ_base=REMOTE)
    assert r.status_code == 401


def test_tokens_are_stored_hashed(lan_app, mac, ipad):
    _, r = pair(mac, ipad)
    token = r.headers.get("Set-Cookie").split(";")[0].split("=", 1)[1]
    store = lan_app.extensions["synapse"].devices
    raw = store.path.read_bytes()
    assert token.encode() not in raw
    rows = sqlite3.connect(store.path).execute("SELECT token_hash FROM devices").fetchall()
    assert rows and rows[0][0] != token


def test_api_never_shows_token_hashes(mac, ipad):
    pair(mac, ipad)
    for dev in mac.get("/api/devices").get_json()["devices"]:
        assert "token_hash" not in dev


def test_backups_leave_devices_out(lan_app, mac, ipad):
    pair(mac, ipad)
    svc = lan_app.extensions["synapse"]
    entry = svc.backups.create("manual")
    path = svc.backups.dir / entry["name"]
    with tarfile.open(path) as tar:
        assert not any("auth" in n for n in tar.getnames())


def test_cookie_refreshed_while_in_use(lan_app, mac, ipad):
    pair(mac, ipad)
    store = lan_app.extensions["synapse"].devices
    t = [store.clock() + devmod.TOUCH_EVERY + 5]
    store.clock = lambda: t[0]
    r = remote(ipad, "GET", "/api/items")
    assert r.status_code == 200 and devmod.COOKIE in (r.headers.get("Set-Cookie") or "")
    r = remote(ipad, "GET", "/api/items")  # within a minute: no new cookie
    assert devmod.COOKIE not in (r.headers.get("Set-Cookie") or "")


@pytest.mark.parametrize(
    "ua,name",
    [
        ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)", "iPad"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "iPhone"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "Mac"),
        ("", "Device"),
    ],
)
def test_guess_name(ua, name):
    assert devmod.guess_name(ua) == name
