"""Signing devices in with a passkey. See backend/access/passkeys.py and backend/access/webauthn.py.

A software authenticator stands in for the browser: it makes the key pair, the client data and
the authenticator data the way ``navigator.credentials`` does.
"""

import hashlib
import json
import os
import sqlite3
import struct

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from backend import create_app
from backend.access import devices as devmod
from backend.access import passkeys as pkmod
from backend.access import webauthn
from backend.access.webauthn import WebAuthnError, b64url
from backend.config import Config

SITE = "synapse.test:8443"
BASE = f"https://{SITE}"
ORIGIN = BASE
IPAD = "192.0.2.10"  # TEST-NET: never one of this computer's own addresses


# ----------------------------------------------------------------------------- a fake authenticator
def cbor(v) -> bytes:
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
        raise ValueError(n)

    if v is None:
        return b"\xf6"
    if isinstance(v, bool):
        return bytes([0xF5 if v else 0xF4])
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return head(3, len(b)) + b
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


class Authenticator:
    def __init__(self, alg=webauthn.ES256, count=None):
        self.alg = alg
        self.cred_id = os.urandom(16)
        self.count = count  # None: a synced passkey, whose counter is always 0
        if alg == webauthn.ES256:
            self.key = ec.generate_private_key(ec.SECP256R1())
        elif alg == webauthn.EDDSA:
            self.key = ed25519.Ed25519PrivateKey.generate()
        else:
            self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def cose(self) -> dict:
        pub = self.key.public_key()
        if self.alg == webauthn.ES256:
            n = pub.public_numbers()
            return {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        if self.alg == webauthn.EDDSA:
            from cryptography.hazmat.primitives import serialization

            raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            return {1: 1, 3: -8, -1: 6, -2: raw}
        n = pub.public_numbers()
        return {1: 3, 3: -257, -1: n.n.to_bytes(256, "big"), -2: n.e.to_bytes(3, "big")}

    def _count(self) -> bytes:
        if self.count is None:
            return struct.pack(">I", 0)
        self.count += 1
        return struct.pack(">I", self.count)

    def create(self, options: dict, origin: str = ORIGIN) -> dict:
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": origin}).encode()
        auth = (
            hashlib.sha256(options["rp"]["id"].encode()).digest()
            + bytes([0x45])  # user present, verified, attested data
            + self._count()
            + bytes(16)
            + struct.pack(">H", len(self.cred_id))
            + self.cred_id
            + cbor(self.cose())
        )
        att = cbor({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": b64url(self.cred_id), "clientDataJSON": b64url(client), "attestationObject": b64url(att)}

    def sign(self, data: bytes) -> bytes:
        if self.alg == webauthn.ES256:
            return self.key.sign(data, ec.ECDSA(hashes.SHA256()))
        if self.alg == webauthn.EDDSA:
            return self.key.sign(data)
        return self.key.sign(data, padding.PKCS1v15(), hashes.SHA256())

    def get(self, options: dict, origin: str = ORIGIN, rp_id: str | None = None, flags: int = 0x05) -> dict:
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"], "origin": origin}).encode()
        auth = hashlib.sha256((rp_id or options["rpId"]).encode()).digest() + bytes([flags]) + self._count()
        return {
            "id": b64url(self.cred_id),
            "clientDataJSON": b64url(client),
            "authenticatorData": b64url(auth),
            "signature": b64url(self.sign(auth + hashlib.sha256(client).digest())),
            "userHandle": None,
        }


# ----------------------------------------------------------------------------- the app over HTTPS
@pytest.fixture
def lan_app(tmp_path):
    cfg = Config.from_env(tmp_path / "data")
    cfg.lan = True
    cfg.allowed_hosts = {"synapse.test"}
    app = create_app(cfg, start_indexer=False)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def mac(lan_app):
    return lan_app.test_client()


@pytest.fixture
def ipad(lan_app):
    return lan_app.test_client()


def secure(c, method, path, remote=False, **kw):
    headers = {"Origin": ORIGIN, **kw.pop("headers", {})}
    env = {"REMOTE_ADDR": IPAD} if remote else {}
    return c.open(path, method=method, base_url=BASE, headers=headers, environ_base=env, **kw)


def add_passkey(mac, auth: Authenticator, name="My passkey"):
    o = secure(mac, "POST", "/api/passkeys/register/options", json={"name": name})
    assert o.status_code == 200, o.get_json()
    r = secure(mac, "POST", "/api/passkeys/register", json={"credential": auth.create(o.get_json())})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["passkey"]


def sign_in(ipad, auth: Authenticator, **kw):
    o = secure(ipad, "POST", "/api/passkeys/login/options", remote=True)
    assert o.status_code == 200, o.get_json()
    return secure(
        ipad,
        "POST",
        "/api/passkeys/login",
        remote=True,
        json={"credential": auth.get(o.get_json(), **kw), "name": "Phone"},
    )


def test_sign_in_with_a_passkey(mac, ipad):
    auth = Authenticator()
    pk = add_passkey(mac, auth)
    assert pk["rp_id"] == "synapse.test" and pk["name"] == "My passkey"
    assert secure(ipad, "GET", "/api/items", remote=True).status_code == 401
    page = secure(ipad, "GET", "/", remote=True)
    assert page.status_code == 401 and b"Sign In with a Passkey" in page.data
    r = sign_in(ipad, auth)
    assert r.status_code == 201, r.get_json()
    cookie = r.headers.get("Set-Cookie")
    assert "synapse_device=" in cookie and "Secure" in cookie and "HttpOnly" in cookie
    assert r.get_json()["device"]["name"] == "Phone"
    assert secure(ipad, "GET", "/api/items", remote=True).status_code == 200
    # it shows up as a device, and the passkey as used
    devices = secure(mac, "GET", "/api/devices").get_json()["devices"]
    assert [d["name"] for d in devices] == ["Phone"]
    listed = secure(mac, "GET", "/api/passkeys").get_json()["passkeys"]
    assert listed[0]["last_used"] and listed[0]["last_addr"] == IPAD


@pytest.mark.parametrize("alg", [webauthn.EDDSA, webauthn.RS256])
def test_other_key_types(mac, ipad, alg):
    auth = Authenticator(alg)
    add_passkey(mac, auth)
    assert sign_in(ipad, auth).status_code == 201


def test_a_challenge_works_once(mac, ipad):
    auth = Authenticator()
    add_passkey(mac, auth)
    o = secure(ipad, "POST", "/api/passkeys/login/options", remote=True).get_json()
    cred = auth.get(o)
    assert secure(ipad, "POST", "/api/passkeys/login", remote=True, json={"credential": cred}).status_code == 201
    again = secure(ipad, "POST", "/api/passkeys/login", remote=True, json={"credential": cred})
    assert again.status_code == 400 and "expired" in again.get_json()["error"]


def test_made_up_challenge_is_refused(mac, ipad):
    auth = Authenticator()
    add_passkey(mac, auth)
    r = secure(
        ipad,
        "POST",
        "/api/passkeys/login",
        remote=True,
        json={"credential": auth.get({"challenge": "x" * 43, "rpId": "synapse.test"})},
    )
    assert r.status_code == 400


def test_wrong_key_unknown_passkey_and_other_site(mac, ipad):
    auth = Authenticator()
    add_passkey(mac, auth)
    impostor = Authenticator()
    impostor.cred_id = auth.cred_id  # claims the saved passkey, signs with another key
    r = sign_in(ipad, impostor)
    assert r.status_code == 401 and "signature" in r.get_json()["error"]
    assert sign_in(ipad, Authenticator()).status_code == 401  # never added
    assert sign_in(ipad, auth, origin="https://evil.example").status_code == 400
    assert sign_in(ipad, auth, rp_id="evil.example").status_code == 400
    assert sign_in(ipad, auth, flags=0x00).status_code == 400  # nobody touched the authenticator
    assert secure(ipad, "GET", "/api/items", remote=True).status_code == 401


def test_counter_must_go_up(mac, ipad):
    auth = Authenticator(count=5)
    add_passkey(mac, auth)
    assert sign_in(ipad, auth).status_code == 201
    auth.count = 1  # a copy of the authenticator, behind the original
    r = sign_in(ipad, auth)
    assert r.status_code == 401 and "counter" in r.get_json()["error"]


def confirm(ipad, auth: Authenticator, **kw):
    o = secure(ipad, "POST", "/api/passkeys/confirm/options", remote=True)
    assert o.status_code == 200, o.get_json()
    return secure(ipad, "POST", "/api/passkeys/confirm", remote=True, json={"credential": auth.get(o.get_json(), **kw)})


def test_only_signed_in_devices_add_passkeys(mac, ipad):
    r = secure(ipad, "POST", "/api/passkeys/register/options", remote=True, json={})
    assert r.status_code == 401
    # a device signed in with a passkey may add another one, after confirming with it
    auth = Authenticator()
    add_passkey(mac, auth)
    assert sign_in(ipad, auth).status_code == 201
    r = secure(ipad, "POST", "/api/passkeys/register/options", remote=True, json={"name": "iPad"})
    assert r.status_code == 403 and r.get_json()["confirm"] is True
    assert confirm(ipad, auth).status_code == 200
    o = secure(ipad, "POST", "/api/passkeys/register/options", remote=True, json={"name": "iPad"})
    assert o.status_code == 200
    second = Authenticator()
    r = secure(ipad, "POST", "/api/passkeys/register", remote=True, json={"credential": second.create(o.get_json())})
    assert r.status_code == 201


def test_registration_answer_is_checked(mac):
    auth = Authenticator()
    o = secure(mac, "POST", "/api/passkeys/register/options", json={}).get_json()
    assert o["authenticatorSelection"]["residentKey"] == "required" and o["attestation"] == "none"
    r = secure(
        mac, "POST", "/api/passkeys/register", json={"credential": auth.create(o, origin="https://evil.example")}
    )
    assert r.status_code == 400
    # a login challenge does not register
    lo = secure(mac, "POST", "/api/passkeys/login/options").get_json()
    r = secure(
        mac,
        "POST",
        "/api/passkeys/register",
        json={"credential": auth.create({"challenge": lo["challenge"], "rp": {"id": "synapse.test"}})},
    )
    assert r.status_code == 400
    assert secure(mac, "POST", "/api/passkeys/register", json={}).status_code == 400


def test_same_passkey_twice(mac):
    auth = Authenticator()
    add_passkey(mac, auth)
    o = secure(mac, "POST", "/api/passkeys/register/options", json={}).get_json()
    assert o["excludeCredentials"] == [{"type": "public-key", "id": b64url(auth.cred_id)}]
    r = secure(mac, "POST", "/api/passkeys/register", json={"credential": auth.create(o)})
    assert r.status_code == 409


def test_removing_a_passkey(mac, ipad):
    auth = Authenticator()
    pk = add_passkey(mac, auth)
    assert sign_in(ipad, auth).status_code == 201
    assert secure(ipad, "DELETE", f"/api/passkeys/{pk['id']}", remote=True).status_code == 403
    assert secure(mac, "DELETE", f"/api/passkeys/{pk['id']}").status_code == 200
    assert secure(mac, "DELETE", f"/api/passkeys/{pk['id']}").status_code == 404
    other = mac.application.test_client()
    assert sign_in(other, auth).status_code == 401
    # the device it signed in stays until removed itself
    assert secure(ipad, "GET", "/api/items", remote=True).status_code == 200


def test_passkeys_need_https_and_a_name(mac, client):
    st = client.get("/api/passkeys").get_json()
    assert st["unavailable"] and "HTTPS" in st["unavailable"] and st["rp_id"] is None
    assert client.post("/api/passkeys/register/options", json={}).status_code == 409
    assert client.post("/api/passkeys/login/options").status_code == 409
    # by IP address, even over HTTPS
    r = mac.post(
        "/api/passkeys/login/options", base_url="https://127.0.0.1:8443", headers={"Origin": "https://127.0.0.1:8443"}
    )
    assert r.status_code == 409 and "IP address" in r.get_json()["error"]
    assert secure(mac, "GET", "/api/passkeys").get_json()["unavailable"] is None


def test_relying_party():
    assert pkmod.relying_party("Mac.local:8443", "https") == ("mac.local", "https://mac.local:8443")
    with pytest.raises(WebAuthnError):
        pkmod.relying_party("mac.local:8000", "http")
    with pytest.raises(WebAuthnError):
        pkmod.relying_party("[::1]:8443", "https")


def test_pending_challenges_are_capped(tmp_path):
    from backend.access.devices import Devices

    store = pkmod.Passkeys(tmp_path / "auth.db", Devices(tmp_path / "auth.db"))
    first = store.login_options("synapse.test", ORIGIN)["challenge"]
    for _ in range(pkmod.MAX_CHALLENGES):
        store.login_options("synapse.test", ORIGIN)
    assert len(store._challenges) == pkmod.MAX_CHALLENGES
    with pytest.raises(WebAuthnError):
        store._take(first, "login", "synapse.test", ORIGIN)


def test_challenges_expire(tmp_path):
    from backend.access.devices import Devices

    t = [1000.0]
    store = pkmod.Passkeys(tmp_path / "auth.db", Devices(tmp_path / "auth.db"), clock=lambda: t[0])
    c = store.login_options("synapse.test", ORIGIN)["challenge"]
    t[0] += pkmod.CHALLENGE_TTL + 1
    with pytest.raises(WebAuthnError, match="expired"):
        store._take(c, "login", "synapse.test", ORIGIN)


def test_old_auth_db_gains_the_new_tables(tmp_path):
    path = tmp_path / "data" / "auth.db"
    path.parent.mkdir(parents=True)
    from backend.access.devices import SCHEMA

    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO devices (id, name, token_hash, created) VALUES ('d1', 'Old iPad', 'h', '2026-01-01')")
    db.commit()
    db.close()
    app = create_app(Config.from_env(tmp_path / "data"), start_indexer=False)
    svc = app.extensions["synapse"]
    assert [d["name"] for d in svc.devices.list()] == ["Old iPad"]
    assert svc.passkeys.list() == [] and svc.tokens.list() == []


def test_cbor_decoder():
    value = {"a": [1, -2, b"\x00\x01", True, None], 3: {"x": "y"}, "big": 2**40}
    assert webauthn.cbor_decode(cbor(value))[0] == value
    for bad in (b"", b"\x5f", b"\x58\x05ab", b"\xf9\x00\x00"):  # empty, indefinite, short, half float
        with pytest.raises(WebAuthnError):
            webauthn.cbor_decode(bad)
    deep = b"\x81" * 40 + b"\x00"
    with pytest.raises(WebAuthnError):
        webauthn.cbor_decode(deep)


def test_unsupported_key_type():
    with pytest.raises(WebAuthnError, match="kind of key"):
        webauthn.cose_to_der({1: 2, 3: -35, -1: 2, -2: b"x", -3: b"y"})  # ES384


def test_a_paired_device_adds_the_first_passkey(mac, ipad):
    code_links = secure(mac, "POST", "/api/devices/pairing").get_json()["links"]
    code = code_links[0]["url"].split("code=")[1]
    assert secure(ipad, "POST", "/api/devices/pair", remote=True, json={"code": code}).status_code == 201
    o = secure(ipad, "POST", "/api/passkeys/register/options", remote=True, json={"name": "Phone"})
    assert o.status_code == 200, o.get_json()
    auth = Authenticator()
    r = secure(ipad, "POST", "/api/passkeys/register", remote=True, json={"credential": auth.create(o.get_json())})
    assert r.status_code == 201


def test_a_stale_cookie_cannot_add_the_first_passkey(lan_app, mac, ipad):
    # A device may enrol its first passkey right after pairing, not with a long-lived cookie later:
    # a stolen cookie must not be able to turn itself into a passkey and gain lasting access.
    code = secure(mac, "POST", "/api/devices/pairing").get_json()["links"][0]["url"].split("code=")[1]
    assert secure(ipad, "POST", "/api/devices/pair", remote=True, json={"code": code}).status_code == 201
    devices = lan_app.extensions["synapse"].devices
    paired_at = devices.clock()
    devices.clock = lambda: paired_at + devmod.PASSKEY_ENROLL_GRACE + 1  # the pairing grace has passed
    # No passkey exists yet, so the device cannot confirm either: it is simply refused (no confirm: true).
    r = secure(ipad, "POST", "/api/passkeys/register/options", remote=True, json={"name": "Phone"})
    assert r.status_code == 403 and "confirm" not in r.get_json()
    # register is gated too, before the answer is even looked at
    assert secure(ipad, "POST", "/api/passkeys/register", remote=True, json={}).status_code == 403


def test_plugin_changes_need_confirmation_from_another_device(mac, ipad):
    # Enabling/disabling/reconfiguring a plugin runs or changes code and lasts, so it needs the same
    # passkey confirmation as the other administrative actions, not just a device cookie.
    auth = Authenticator()
    add_passkey(mac, auth)
    assert sign_in(ipad, auth).status_code == 201
    for method, path, payload in (
        ("POST", "/api/plugin-host/graph/enable", None),
        ("POST", "/api/plugin-host/graph/disable", None),
        ("POST", "/api/plugin-host/graph/restart", None),
        ("PUT", "/api/plugin-host/graph/settings", {}),
        ("PUT", "/api/plugin-host/graph/remote-mcp", {"on": True}),
    ):
        r = secure(ipad, method, path, remote=True, json=payload)
        assert r.status_code == 403 and r.get_json().get("confirm") is True, path
    # the computer running Synapse passes the gate (an unknown plugin is then a 400, not a 403)
    assert secure(mac, "POST", "/api/plugin-host/graph/enable").status_code != 403
    assert confirm(ipad, auth).status_code == 200
    # after confirming, the device may change plugins (unknown id → 400 from the host, not the gate)
    assert secure(ipad, "POST", "/api/plugin-host/graph/enable", remote=True).status_code != 403


def test_backup_settings_need_confirmation_from_another_device(mac, ipad, tmp_path):
    # With a stolen device cookie, the backup folder could copy the whole library elsewhere, and
    # "keep 1" would prune the backups right before deleting things.
    auth = Authenticator()
    add_passkey(mac, auth)
    assert sign_in(ipad, auth).status_code == 201
    elsewhere = str(tmp_path / "elsewhere")
    for change in ({"backup_dir": elsewhere}, {"backup_keep": 1}, {"backup_interval": "off"}):
        r = secure(ipad, "PUT", "/api/settings", remote=True, json=change)
        assert r.status_code == 403 and r.get_json().get("confirm") is True, change
    assert not (tmp_path / "elsewhere").exists()
    s = secure(ipad, "GET", "/api/settings", remote=True).get_json()["settings"]
    # other settings, and sending the backup settings unchanged, need no confirmation
    unchanged = {k: s[k] for k in ("backup_dir", "backup_keep", "backup_interval")}
    assert secure(ipad, "PUT", "/api/settings", remote=True, json={**unchanged, "theme": "dark"}).status_code == 200
    # the computer running Synapse, and a confirmed device, may change them
    assert secure(mac, "PUT", "/api/settings", json={"backup_keep": 14}).status_code == 200
    assert confirm(ipad, auth).status_code == 200
    r = secure(ipad, "PUT", "/api/settings", remote=True, json={"backup_keep": 30})
    assert r.status_code == 200 and r.get_json()["settings"]["backup_keep"] == 30


def test_making_and_restoring_backups_need_confirmation_from_another_device(mac, ipad):
    # Each manual backup prunes the oldest (here and offsite): with a stolen cookie alone, a few in a
    # row would push every good backup out. A restore replaces the library and which plugins are on.
    auth = Authenticator()
    add_passkey(mac, auth)
    assert sign_in(ipad, auth).status_code == 201
    made = secure(mac, "POST", "/api/backups")
    assert made.status_code == 201
    name = made.get_json()["backup"]["name"]
    for path, payload in (("/api/backups", None), ("/api/backups/restore", {"name": name})):
        r = secure(ipad, "POST", path, remote=True, json=payload)
        assert r.status_code == 403 and r.get_json().get("confirm") is True, path
    assert [b["name"] for b in secure(ipad, "GET", "/api/backups", remote=True).get_json()["backups"]] == [name]
    assert confirm(ipad, auth).status_code == 200
    assert secure(ipad, "POST", "/api/backups", remote=True).status_code == 201
    assert secure(ipad, "POST", "/api/backups/restore", remote=True, json={"name": name}).status_code == 200


def test_admin_actions_from_a_device_need_a_confirmation(lan_app, mac, ipad):
    auth = Authenticator()
    add_passkey(mac, auth)
    assert sign_in(ipad, auth).status_code == 201
    st = secure(ipad, "GET", "/api/devices", remote=True).get_json()
    assert st["this_computer"] is False and st["can_confirm"] is True and st["confirmed"] is False
    for method, path, body in (
        ("POST", "/api/devices/pairing", None),
        ("POST", "/api/mcp/tokens", {"name": "Laptop"}),
    ):
        r = secure(ipad, method, path, remote=True, json=body)
        assert r.status_code == 403 and r.get_json()["confirm"] is True, path
    # a wrong passkey does not confirm
    assert confirm(ipad, Authenticator()).status_code == 401
    # a login challenge is not a confirmation
    lo = secure(ipad, "POST", "/api/passkeys/login/options", remote=True).get_json()
    bad = secure(ipad, "POST", "/api/passkeys/confirm", remote=True, json={"credential": auth.get(lo)})
    assert bad.status_code == 400
    r = confirm(ipad, auth)
    assert r.status_code == 200 and r.get_json()["until"]
    assert secure(ipad, "GET", "/api/devices", remote=True).get_json()["confirmed"] is True
    assert secure(ipad, "POST", "/api/devices/pairing", remote=True).status_code == 201
    made = secure(ipad, "POST", "/api/mcp/tokens", remote=True, json={"name": "Laptop"})
    assert made.status_code == 201
    tok = made.get_json()["entry"]["id"]
    assert secure(ipad, "DELETE", f"/api/mcp/tokens/{tok}", remote=True).status_code == 200
    # the confirmation runs out
    conf = lan_app.extensions["synapse"].confirmations
    conf.clock = lambda: 10**12
    r = secure(ipad, "POST", "/api/devices/pairing", remote=True)
    assert r.status_code == 403 and r.get_json()["confirm"] is True


def test_another_device_cannot_use_my_confirmation(lan_app, mac, ipad):
    auth = Authenticator()
    add_passkey(mac, auth)
    assert sign_in(ipad, auth).status_code == 201
    assert confirm(ipad, auth).status_code == 200
    other = lan_app.test_client()
    assert sign_in(other, auth).status_code == 201
    r = secure(other, "POST", "/api/devices/pairing", remote=True)
    assert r.status_code == 403
    # an unpaired device can't even ask
    stranger = lan_app.test_client()
    assert secure(stranger, "POST", "/api/passkeys/confirm/options", remote=True).status_code == 401


def test_without_a_passkey_a_device_is_told_where_to_go(mac, ipad):
    code = secure(mac, "POST", "/api/devices/pairing").get_json()["links"][0]["url"].split("code=")[1]
    assert secure(ipad, "POST", "/api/devices/pair", remote=True, json={"code": code}).status_code == 201
    r = secure(ipad, "POST", "/api/mcp/tokens", remote=True, json={"name": "x"})
    assert r.status_code == 403 and "confirm" not in r.get_json()
    assert "computer running Synapse" in r.get_json()["error"]
