"""Remote backup configuration, delivery, recovery and browser authorization contracts."""

import json
import shutil
import stat
import tarfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.backup import BackupError
from backend.backup.delivery import Delivery
from backend.backup.remote import RemoteStorage
from backend.online.backup_oauth import CALLBACK, AuthorizationError, BackupOAuth


def s3(folder="bucket/backups"):
    return {
        "provider": "s3",
        "folder": folder,
        "options": {
            "provider": "Other",
            "access_key_id": "private-access-id",
            "secret_access_key": "private-secret",
            "endpoint": "https://storage.example",
        },
    }


def drive():
    return {
        "provider": "drive",
        "folder": "Backups",
        "options": {"client_id": "application-id", "client_secret": "application-secret"},
    }


def ftp(security="explicit", port="47406"):
    return {
        "provider": "ftp",
        "folder": "Backups",
        "options": {
            "host": "backup.example",
            "port": port,
            "user": "backup-user",
            "pass": "test-password",
            "security": security,
        },
    }


@pytest.mark.parametrize(
    "security,tls,explicit_tls,default_port",
    [("explicit", "false", "true", "21"), ("implicit", "true", "false", "990"), ("plain", "false", "false", "21")],
)
@pytest.mark.parametrize("custom_port", [True, False])
def test_ftp_security_and_ports_round_trip(
    client, svc, monkeypatch, security, tls, explicit_tls, default_port, custom_port
):
    calls = []

    def obscure(*args, **kwargs):
        calls.append((args, kwargs))
        return "obscured-password\n"

    monkeypatch.setattr(svc.backups.remote.rclone, "run", obscure)
    data = ftp(security, "47406" if custom_port else "")
    response = client.put("/api/backups/remote", json=data)
    assert response.status_code == 200, response.text
    assert calls == [(("obscure", "-"), {"input": "test-password"})]
    options = svc.backups.remote._options()
    assert options == {
        "type": "ftp",
        "host": "backup.example",
        "port": "47406" if custom_port else default_port,
        "user": "backup-user",
        "pass": "obscured-password",
        "tls": tls,
        "explicit_tls": explicit_tls,
    }
    saved = RemoteStorage(svc.backups.remote.root, BackupOAuth()).configuration()
    assert saved["options"]["security"] == security
    assert saved["stored_secrets"] == ["pass"]
    assert "test-password" not in response.text and "obscured-password" not in response.text
    data["options"]["pass"] = ""
    assert client.put("/api/backups/remote", json=data).get_json()["configuration"]["revision"] == saved["revision"]
    assert len(calls) == 1


def test_ftp_defaults_to_explicit_tls(client, svc, monkeypatch):
    monkeypatch.setattr(svc.backups.remote.rclone, "run", lambda *a, **kw: "obscured")
    data = ftp()
    del data["options"]["security"]
    assert client.put("/api/backups/remote", json=data).status_code == 200
    assert svc.backups.remote._options()["explicit_tls"] == "true"
    assert svc.backups.remote._options()["tls"] == "false"


@pytest.mark.parametrize("provider", ["ftp", "webdav", "sftp"])
def test_existing_obscured_password_is_preserved_without_running_rclone(client, svc, monkeypatch, provider):
    # Rclone's unpadded URL-safe base64 format includes a 16-byte IV.
    obscured = "QW4tZXhhbXBsZS1JVjEyMy1jaXBoZXJ0ZXh0"
    data = ftp()
    data.update(provider=provider, password_obscured=True)
    data["options"] = {
        "ftp": data["options"],
        "webdav": {"url": "https://backup.example/dav", "user": "backup-user"},
        "sftp": {"host": "backup.example", "user": "backup-user", "known_hosts": "backup.example ssh-ed25519 AAAA"},
    }[provider]
    data["options"]["pass"] = obscured

    def unexpected(*a, **kw):
        pytest.fail("An imported password must not be obscured again")

    monkeypatch.setattr(svc.backups.remote.rclone, "run", unexpected)
    response = client.put("/api/backups/remote", json=data)
    assert response.status_code == 200, response.text
    assert svc.backups.remote._options()["pass"] == obscured
    assert obscured not in response.text
    assert "password_obscured" not in svc.backups.remote._options()
    revision = response.get_json()["configuration"]["revision"]
    data["options"]["pass"] = ""
    for flag in (False, True):
        data["password_obscured"] = flag
        assert client.put("/api/backups/remote", json=data).get_json()["configuration"]["revision"] == revision
        assert svc.backups.remote._options()["pass"] == obscured


@pytest.mark.parametrize(
    "change",
    [
        {"security": "invalid"},
        {"security": ""},
        {"port": "65536"},
        {"port": "0"},
        {"port": "not-a-port"},
        {"port": "9" * 5000},
        {"port": "２１"},
        {"host": "ftp://backup.example/path"},
        {"tls": "true"},
        {"explicit_tls": "true"},
        {"pass": "bad\npassword"},
    ],
)
def test_invalid_ftp_settings_preserve_saved_connection(client, svc, monkeypatch, change):
    client.put("/api/backups/remote", json=s3())
    before = svc.backups.remote.rclone.config.read_bytes()
    monkeypatch.setattr(svc.backups.remote.rclone, "run", lambda *a, **kw: "obscured")
    data = ftp()
    data["options"].update(change)
    assert client.put("/api/backups/remote", json=data).status_code == 400
    assert svc.backups.remote.rclone.config.read_bytes() == before


@pytest.mark.parametrize("password", ["short", "not base64!", "a" * 25, "YWJj", "A" * 22 + "="])
def test_malformed_obscured_password_is_rejected(client, svc, password):
    client.put("/api/backups/remote", json=s3())
    before = svc.backups.remote.rclone.config.read_bytes()
    data = ftp()
    data["options"]["pass"] = password
    data["password_obscured"] = True
    response = client.put("/api/backups/remote", json=data)
    assert response.status_code == 400
    assert password not in response.text
    assert svc.backups.remote.rclone.config.read_bytes() == before


@pytest.mark.parametrize("flag", ["true", 1, None, []])
def test_password_format_requires_a_boolean(client, flag):
    assert client.put("/api/backups/remote", json={**ftp(), "password_obscured": flag}).status_code == 400


@pytest.mark.skipif(shutil.which("rclone") is None, reason="optional real-rclone integration test")
def test_real_rclone_password_can_be_imported_or_obscured(svc):
    remote = svc.backups.remote
    data = ftp()
    password = " test-password-with-spaces "
    data["options"]["pass"] = password
    remote.save(data)
    obscured = remote._options()["pass"]
    assert obscured != password
    data.update(password_obscured=True)
    data["options"]["pass"] = obscured
    revision = remote.metadata()["revision"]
    remote.save(data)
    assert remote._options()["pass"] == obscured
    assert remote.metadata()["revision"] == revision


@pytest.fixture
def storage(svc, tmp_path, monkeypatch):
    """An in-memory rclone transport with real local files and the public Settings configuration."""
    remote = svc.backups.remote
    remote.save(s3())
    root = tmp_path / "offsite"
    root.mkdir()
    calls = []

    def path(value):
        return root / value.removeprefix("remote:") if value.startswith("remote:") else Path(value)

    def run(*args, **kw):
        calls.append(args)
        operation = args[0]
        if operation in ("copyto", "moveto"):
            src, dest = path(args[1]), path(args[2])
            dest.parent.mkdir(parents=True, exist_ok=True)
            if "--immutable" in args and dest.exists() and dest.read_bytes() != src.read_bytes():
                raise BackupError("Immutable backup differs")
            shutil.copyfile(src, dest)
            if operation == "moveto":
                src.unlink()
            return ""
        if operation == "deletefile":
            path(args[1]).unlink()
            return ""
        if operation == "lsjson":
            folder = path(args[1])
            if not folder.exists():
                raise BackupError("Remote folder is missing")
            return json.dumps(
                [
                    {"Name": p.name, "Size": p.stat().st_size, "IsDir": p.is_dir(), "ModTime": "2026-11-07T12:00:00Z"}
                    for p in folder.iterdir()
                    if (p.is_dir() if "--dirs-only" in args else p.is_file())
                ]
            )
        raise AssertionError(args)

    monkeypatch.setattr(remote.rclone, "run", run)
    svc.settings.update({"backup_destination": "remote"})
    return root, calls


def test_remote_settings_are_masked_persistent_and_not_archived(client, svc):
    response = client.put("/api/backups/remote", json=s3())
    assert response.status_code == 200
    public = response.get_json()["configuration"]
    assert public["stored_secrets"] == ["access_key_id", "secret_access_key"]
    assert "private-secret" not in response.text and "private-access-id" not in response.text
    remote = RemoteStorage(svc.backups.remote.root, BackupOAuth())
    assert remote.configuration()["namespace"] == public["namespace"]
    assert remote._options()["secret_access_key"] == "private-secret"
    assert stat.S_IMODE(remote.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(remote.rclone.config.stat().st_mode) == 0o600
    entry = svc.backups.create()
    with tarfile.open(svc.backups.dir / entry["name"]) as archive:
        assert not any(n.startswith("backup-remote") for n in archive.getnames())
        assert b"private-secret" not in archive.extractfile("settings.json").read()
    saved = s3()
    saved["options"]["access_key_id"] = saved["options"]["secret_access_key"] = ""
    assert client.put("/api/backups/remote", json=saved).get_json()["configuration"]["revision"] == public["revision"]


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "local"},
        {"folder": "../other"},
        {"folder": "other:bucket"},
        {
            "options": {
                "provider": "Other",
                "secret_access_key": "x",
                "access_key_id": "x",
                "endpoint": "http://storage.example",
            }
        },
        {"options": {"sftp_command": "sh -c 'something'"}},
    ],
)
def test_invalid_connections_do_not_change_existing_configuration(client, svc, change):
    client.put("/api/backups/remote", json=s3())
    before = svc.backups.remote.rclone.config.read_bytes()
    response = client.put("/api/backups/remote", json={**s3(), **change})
    assert response.status_code == 400
    assert svc.backups.remote.rclone.config.read_bytes() == before


def test_remote_upload_is_separate_from_local_creation_and_publishes_only_complete_archives(svc, storage):
    svc.papers.create({"title": "Backed up"})
    entry = svc.backups.create()
    assert svc.backups.status()["delivery"]["pending"] == 1
    assert (svc.backups.dir / entry["name"]).exists()
    svc.backups.delivery.process()
    status = svc.backups.status()
    assert status["delivery"]["pending"] == 0 and status["backups"][0]["uploaded"]
    assert [b["name"] for b in svc.backups.remote_list()] == [entry["name"]]
    assert [call[0] for call in storage[1]][:2] == ["copyto", "moveto"]
    assert storage[1][0][2].endswith(".part")


def test_failed_upload_survives_restart_and_retries_without_waiting_for_next_backup(svc, storage, monkeypatch):
    entry = svc.backups.create()
    upload = svc.backups.remote.upload

    def offline(_path):
        raise BackupError("Remote unavailable")

    monkeypatch.setattr(svc.backups.remote, "upload", offline)
    svc.backups.delivery.process()
    assert svc.backups.status()["delivery"]["error"] == "Remote unavailable"
    assert not svc.backups.due()
    svc.backups.delivery = Delivery(svc.backups, svc.backups.remote)
    monkeypatch.setattr(svc.backups.remote, "upload", upload)
    svc.backups.delivery.process()
    assert svc.backups.status()["delivery"]["pending"] == 0
    assert svc.backups.status()["delivery"]["error"] is None
    assert svc.backups.remote_list()[0]["name"] == entry["name"]


def test_crash_after_archive_publication_is_reconciled(svc, storage):
    entry = svc.backups.create()
    (svc.backups.remote.root / "delivery.json").unlink()
    svc.backups.delivery = Delivery(svc.backups, svc.backups.remote)
    svc.backups.delivery.process()
    assert svc.backups.remote_list()[0]["name"] == entry["name"]


def test_remote_retention_only_deletes_own_archives_and_local_queue_is_bounded(svc, storage):
    svc.settings.update({"backup_keep": 2})
    for _ in range(4):
        svc.backups.create()
    assert svc.backups.status()["delivery"]["pending"] == 2
    assert svc.backups.status()["delivery"]["warning"]
    svc.backups.delivery.process()
    scope = storage[0] / svc.backups.remote.destination().removeprefix("remote:")
    (scope / "notes-20200101.tar.gz").write_bytes(b"leave alone")
    (scope / "other.txt").write_bytes(b"leave alone")
    (scope / "library-20200101-000000.tar.gz.part").write_bytes(b"unfinished")
    for _ in range(3):
        svc.backups.create()
        svc.backups.delivery.process()
    assert len(svc.backups.remote_list()) == 2
    assert (scope / "other.txt").exists() and (scope / "notes-20200101.tar.gz").exists()
    assert (scope / "library-20200101-000000.tar.gz.part").exists()


def test_remote_restore_downloads_validates_and_keeps_current_connection(client, svc, storage):
    svc.papers.create({"title": "Original"})
    entry = svc.backups.create()
    svc.backups.delivery.process()
    svc.papers.create({"title": "Later"})
    old = svc.backups.remote.rclone.config.read_bytes()
    response = client.post("/api/backups/restore", json={"name": entry["name"], "source": "remote"})
    assert response.status_code == 200, response.text
    assert [p["title"] for p in svc.papers.list()] == ["Original"]
    assert svc.backups.remote.rclone.config.read_bytes() == old
    assert svc.settings.get("backup_destination") == "remote"
    assert response.get_json()["safety_backup"] in [b["name"] for b in svc.backups.list()]
    assert client.post("/api/backups/restore", json={"name": "../anything", "source": "remote"}).status_code == 400


def test_recovery_finds_existing_backup_sets(client, svc, storage):
    entry = svc.backups.create()
    svc.backups.delivery.process()
    old_set = svc.backups.remote.metadata()["namespace"]
    fresh = s3()
    fresh["namespace"] = "synapse-" + "1" * 32
    client.put("/api/backups/remote", json=fresh)
    found = client.get("/api/backups/remote/sets").get_json()["sets"]
    assert old_set in found
    fresh["namespace"] = old_set
    client.put("/api/backups/remote", json=fresh)
    assert client.get("/api/backups/remote/archives").get_json()["backups"][0]["name"] == entry["name"]


def test_test_connection_checks_read_write_and_delete(client, svc, storage):
    assert client.post("/api/backups/remote/test").status_code == 200
    assert not list(storage[0].rglob(".synapse-test-*"))


def test_backup_destination_setting_and_remote_changes_need_admin_on_another_device(app):
    from backend import create_app
    from backend.config import Config
    from tests.test_container import pairing_code, through_tunnel
    from tests.test_public_url import PUBLIC_URL

    cfg = Config.from_env(app.extensions["synapse"].config.data_dir / "container")
    cfg.public_url = PUBLIC_URL
    cfg.container = True
    server = create_app(cfg, start_indexer=False)
    client = server.test_client()
    code = pairing_code(client)
    assert (
        through_tunnel(client, "POST", "/api/devices/pair", json={"code": code, "name": "Browser"}).status_code == 201
    )
    for method, url, data in [
        ("PUT", "/api/backups/remote", s3()),
        ("POST", "/api/backups/remote/test", {}),
        ("POST", "/api/backups/remote/authorize", {}),
        ("PUT", "/api/settings", {"backup_destination": "remote"}),
    ]:
        assert through_tunnel(client, method, url, json=data).status_code == 403


@pytest.mark.parametrize("provider", ["drive", "onedrive", "dropbox"])
def test_server_oauth_callback_uses_public_https_and_persists_tokens(client, svc, monkeypatch, provider):
    cfg = {**drive(), "provider": provider}
    assert client.put("/api/backups/remote", json=cfg).status_code == 200
    svc.config.public_url = "https://library.example"
    response = client.post("/api/backups/remote/authorize").get_json()
    params = parse_qs(urlsplit(response["url"]).query)
    assert params["redirect_uri"] == ["https://library.example" + CALLBACK]
    assert params["code_challenge_method"] == ["S256"]

    def exchange(method, url, **kw):
        if method == "POST":
            assert kw["data"]["redirect_uri"] == "https://library.example" + CALLBACK
            assert kw["data"]["code_verifier"]
            return {"access_token": "sensitive-access", "refresh_token": "sensitive-refresh", "expires_in": 3600}
        return {"id": "default-drive", "driveType": "personal"}

    monkeypatch.setattr(svc.backups.remote.oauth, "_json", exchange)
    response = client.get(CALLBACK, query_string={"state": params["state"][0], "code": "authorization-code"})
    assert response.status_code == 200 and "Account connected" in response.text
    assert "sensitive" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    options = svc.backups.remote._options()
    assert json.loads(options["token"])["refresh_token"] == "sensitive-refresh"
    if provider == "onedrive":
        assert options["drive_id"] == "default-drive"
    assert "sensitive" not in client.get("/api/backups").text
    assert client.get(CALLBACK, query_string={"state": params["state"][0], "code": "replayed"}).status_code == 400


def test_oauth_rejects_other_device_wrong_state_expiry_and_changed_connection():
    oauth = BackupOAuth()
    options = {"type": "drive", **drive()["options"]}
    oauth.begin("device-a", "https://library.example" + CALLBACK, options, "version")
    state = oauth._pending["state"]
    for owner, args, revision in [
        ("device-b", {"state": state, "code": "x"}, "version"),
        ("device-a", {"state": "wrong", "code": "x"}, "version"),
        ("device-a", {"state": state, "code": "x"}, "changed"),
    ]:
        with pytest.raises(AuthorizationError):
            oauth.finish(owner, args, revision)
    oauth._pending["expires"] = 0
    with pytest.raises(AuthorizationError):
        oauth.finish("device-a", {"state": state, "code": "x"}, "version")
    with pytest.raises(AuthorizationError):
        oauth.begin("device-a", "http://library.example" + CALLBACK, options, "version")


@pytest.mark.skipif(shutil.which("rclone") is None, reason="optional real-rclone integration test")
def test_real_rclone_archive_delivery_and_restore(svc, tmp_path):
    remote = svc.backups.remote
    remote.save(s3(str(tmp_path / "offsite")))
    # A real local rclone backend exercises the process/configuration and transfer protocol without
    # requiring a cloud account or network in the test suite. The public UI never accepts this type.
    remote._write_options({"type": "local"})
    svc.settings.update({"backup_destination": "remote"})
    svc.papers.create({"title": "Real transfer"})
    entry = svc.backups.create()
    svc.backups.delivery.process()
    assert svc.backups.status()["delivery"]["error"] is None
    assert svc.backups.remote_list()[0]["name"] == entry["name"]
    remote.test()
    svc.papers.create({"title": "Later"})
    svc.backups.restore_remote(entry["name"])
    assert [p["title"] for p in svc.papers.list()] == ["Real transfer"]


def test_private_key_can_be_stored_without_leaking_to_status(client, svc, monkeypatch):
    monkeypatch.setattr(svc.backups.remote.rclone, "run", lambda *a, **kw: "obscured")
    response = client.put(
        "/api/backups/remote",
        json={
            "provider": "sftp",
            "folder": "/backups",
            "options": {
                "host": "backup.example",
                "user": "owner",
                "key_pem": "PRIVATE\nKEY",
                "known_hosts": "backup.example ssh-ed25519 AAAA",
            },
        },
    )
    assert response.status_code == 200, response.text
    assert "PRIVATE" not in response.text
    assert svc.backups.remote._options()["key_pem"] == "PRIVATE\\nKEY"


def test_missing_pending_file_does_not_block_newer_uploads(svc, storage):
    lost = svc.backups.create()
    (svc.backups.dir / lost["name"]).unlink()
    retained = svc.backups.create()
    svc.backups.delivery.process()
    assert [b["name"] for b in svc.backups.remote_list()] == [retained["name"]]
    assert svc.backups.status()["delivery"]["pending"] == 0
    assert "missing" in svc.backups.status()["delivery"]["warning"]


def test_inflight_archive_is_protected_without_holding_the_archive_lock(svc, storage, monkeypatch):
    import threading

    svc.settings.update({"backup_keep": 1})
    first = svc.backups.create()
    uploading = threading.Event()
    release = threading.Event()
    upload = svc.backups.remote.upload

    def paused(path):
        uploading.set()
        assert release.wait(5)
        upload(path)

    monkeypatch.setattr(svc.backups.remote, "upload", paused)
    worker = threading.Thread(target=svc.backups.delivery.process)
    worker.start()
    try:
        assert uploading.wait(5)
        # A normal backup still completes while the network is busy.
        second = svc.backups.create()
        assert (svc.backups.dir / first["name"]).exists()
        assert (svc.backups.dir / second["name"]).exists()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert [b["name"] for b in svc.backups.list()] == [second["name"]]


def test_local_mode_keeps_offsite_backups_and_pauses_delivery(svc, storage):
    entry = svc.backups.create()
    svc.backups.delivery.process()
    svc.settings.update({"backup_destination": "local"})
    svc.backups.create()
    before = list(storage[1])
    svc.backups.delivery.process()
    assert storage[1] == before
    assert svc.backups.remote_list()[0]["name"] == entry["name"]


def test_rclone_cannot_inherit_remotes_and_does_not_report_provider_secrets(tmp_path, monkeypatch):
    import subprocess

    from backend.backup.rclone import Rclone

    captured = {}

    class Process:
        returncode = 1

        def __init__(self, command, **kwargs):
            captured.update(command=command, **kwargs)

        def communicate(self, **kwargs):
            return "sensitive-provider-body", None

    monkeypatch.setenv("RCLONE_CONFIG_REMOTE_TYPE", "local")
    monkeypatch.setenv("RCLONE_CONFIG_PASS_COMMAND", "untrusted-command")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rclone")
    monkeypatch.setattr(subprocess, "Popen", Process)
    with pytest.raises(BackupError) as failure:
        Rclone(tmp_path / "private.conf").run("lsjson", "remote:backups")
    assert "sensitive" not in str(failure.value)
    assert not any(key.startswith("RCLONE_") for key in captured["env"])
    assert captured["stderr"] == subprocess.DEVNULL
    assert captured["command"][1:3] == ["--config", str(tmp_path / "private.conf")]


def test_cloud_callback_can_finish_from_the_initiating_remote_device_after_confirmation_expires(app, monkeypatch):
    from backend import create_app
    from backend.config import Config
    from tests.test_container import pairing_code, through_tunnel
    from tests.test_public_url import PUBLIC_URL

    cfg = Config.from_env(app.extensions["synapse"].config.data_dir / "oauth-container")
    cfg.public_url, cfg.container = PUBLIC_URL, True
    server = create_app(cfg, start_indexer=False)
    services = server.extensions["synapse"]
    client = server.test_client()
    response = through_tunnel(
        client, "POST", "/api/devices/pair", json={"code": pairing_code(client), "name": "Browser"}
    )
    device_id = response.get_json()["device"]["id"]
    services.confirmations.confirm(device_id)
    assert through_tunnel(client, "PUT", "/api/backups/remote", json=drive()).status_code == 200
    response = through_tunnel(client, "POST", "/api/backups/remote/authorize")
    assert response.status_code == 200
    state = parse_qs(urlsplit(response.get_json()["url"]).query)["state"][0]
    services.confirmations.forget(device_id)
    monkeypatch.setattr(
        services.backups.remote.oauth, "_json", lambda *a, **kw: {"access_token": "access", "refresh_token": "refresh"}
    )
    response = through_tunnel(client, "GET", CALLBACK, query_string={"state": state, "code": "code"})
    assert response.status_code == 200
    assert services.backups.remote.configuration()["authorized"]
    assert through_tunnel(client, "PUT", "/api/settings", json={"backup_destination": "remote"}).status_code == 403
    services.settings.update({"backup_destination": "remote"})
    assert through_tunnel(client, "POST", "/api/settings/reset").status_code == 403
