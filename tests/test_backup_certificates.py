"""Trusted backup certificates: persistence, permissions and actual rclone TLS verification."""

import shutil
import socketserver
import ssl
import stat
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.backup import BackupError
from backend.backup.certificates import MAX_CA_BYTES, parse_ca_certificate
from backend.backup.remote import RemoteStorage
from backend.online.backup_oauth import BackupOAuth
from tests.certificates import make_certificate
from tests.test_remote_backup import drive, ftp, s3


@pytest.fixture
def certificate():
    return make_certificate()[0]


def test_certificate_bundle_is_private_persistent_and_excluded_from_archives(client, svc, certificate):
    bundle = certificate + make_certificate(ca=True)[0]
    response = client.put("/api/backups/remote", json={**s3(), "ca_certificate": bundle})
    assert response.status_code == 200, response.text
    summary = response.get_json()["configuration"]["trusted_certificate"]
    assert summary == parse_ca_certificate(bundle).summary
    assert summary["count"] == 2
    remote = svc.backups.remote
    path = Path(remote._options()["override.ca_cert"])
    assert path.parent == remote.root
    assert path.read_text() == bundle
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "BEGIN CERTIFICATE" not in response.text and str(path) not in response.text
    assert bundle not in (svc.config.data_dir / "settings.json").read_text()
    assert RemoteStorage(remote.root, BackupOAuth()).configuration()["trusted_certificate"] == summary
    entry = svc.backups.create()
    with tarfile.open(svc.backups.dir / entry["name"]) as archive:
        assert not any("backup-remote" in name or path.name in name for name in archive.getnames())


def test_certificate_can_be_kept_replaced_or_removed(client, svc, certificate):
    data = {**s3(), "ca_certificate": certificate}
    saved = client.put("/api/backups/remote", json=data).get_json()["configuration"]
    path = Path(svc.backups.remote._options()["override.ca_cert"])
    for payload in (s3(), {**s3(), "ca_certificate": "# exported bundle\r\n" + certificate.replace("\n", "\r\n")}):
        kept = client.put("/api/backups/remote", json=payload).get_json()["configuration"]
        assert kept["revision"] == saved["revision"]
        assert kept["trusted_certificate"] == saved["trusted_certificate"]
    replacement = make_certificate()[0]
    data["ca_certificate"] = replacement
    changed = client.put("/api/backups/remote", json=data).get_json()["configuration"]
    assert changed["revision"] != saved["revision"]
    assert not path.exists()
    replacement_path = Path(svc.backups.remote._options()["override.ca_cert"])
    assert replacement_path.read_text() == replacement
    removed = client.put("/api/backups/remote", json={**s3(), "ca_certificate": None}).get_json()["configuration"]
    assert removed["trusted_certificate"] is None
    assert removed["revision"] != changed["revision"]
    assert "override.ca_cert" not in svc.backups.remote._options()
    assert not replacement_path.exists()


@pytest.mark.parametrize(
    "value", ["", "not a certificate", "/etc/ssl/certs/ca-certificates.crt", 123, False, {}, "x" * (MAX_CA_BYTES + 1)]
)
def test_bad_upload_does_not_change_connection_or_certificate(client, svc, certificate, value):
    client.put("/api/backups/remote", json={**s3(), "ca_certificate": certificate})
    root = svc.backups.remote.root
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    response = client.put("/api/backups/remote", json={**s3(), "ca_certificate": value})
    assert response.status_code == 400
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_private_keys_and_partially_invalid_bundles_are_rejected(client, svc, certificate):
    _, key = make_certificate()
    for value in (
        key.decode(),
        certificate + key.decode(),
        certificate + "-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----\n",
    ):
        response = client.put("/api/backups/remote", json={**s3(), "ca_certificate": value})
        assert response.status_code == 400
        assert "BEGIN" not in response.text
        assert not svc.backups.remote.root.exists()


@pytest.mark.filterwarnings(
    "ignore:Parsed a serial number which wasn't positive:cryptography.utils.CryptographyDeprecationWarning"
)
def test_system_ca_bundle_is_accepted():
    roots = ssl.create_default_context().get_ca_certs(binary_form=True)
    assert roots
    bundle = "".join(ssl.DER_cert_to_PEM_cert(cert) for cert in roots)
    assert parse_ca_certificate(bundle).count == len(roots)


def test_invalid_connection_does_not_save_uploaded_certificate(client, svc, certificate):
    data = s3()
    data["options"]["endpoint"] = "http://backup.example"
    assert client.put("/api/backups/remote", json={**data, "ca_certificate": certificate}).status_code == 400
    assert not svc.backups.remote.root.exists()


def test_changing_provider_does_not_reuse_trust_bundle(client, svc, certificate):
    client.put("/api/backups/remote", json={**s3(), "ca_certificate": certificate})
    path = Path(svc.backups.remote._options()["override.ca_cert"])
    assert client.put("/api/backups/remote", json={**drive(), "ca_certificate": certificate}).status_code == 400
    result = client.put("/api/backups/remote", json=drive()).get_json()["configuration"]
    assert result["trusted_certificate"] is None
    assert "override.ca_cert" not in svc.backups.remote._options()
    assert not path.exists()


def test_ftp_upload_keeps_tls_verification_enabled(client, svc, monkeypatch, certificate):
    monkeypatch.setattr(svc.backups.remote.rclone, "run", lambda *a, **kw: "obscured")
    response = client.put("/api/backups/remote", json={**ftp(), "ca_certificate": certificate})
    assert response.status_code == 200
    options = svc.backups.remote._options()
    assert options["explicit_tls"] == "true" and options["tls"] == "false"
    assert options["override.ca_cert"].endswith(".pem")
    assert not any("no_check" in key for key in options)


@pytest.fixture
def tls_server(tmp_path):
    servers = []

    def start(*, expired=False):
        pem, key = make_certificate(expired=expired)
        cert_path, key_path = tmp_path / f"cert-{len(servers)}.pem", tmp_path / f"key-{len(servers)}.pem"
        cert_path.write_text(pem)
        key_path.write_bytes(key)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_PROPFIND(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                xml = b"""<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
                  <d:response><d:href>/Backups/</d:href><d:propstat><d:prop>
                  <d:resourcetype><d:collection/></d:resourcetype>
                  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
                  <d:response><d:href>/Backups/proof.txt</d:href><d:propstat><d:prop>
                  <d:resourcetype/><d:getcontentlength>4</d:getcontentlength>
                  <d:getlastmodified>Sat, 07 Nov 2026 12:00:00 GMT</d:getlastmodified>
                  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
                  </d:multistatus>"""
                self.send_response(207)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(xml)))
                self.end_headers()
                self.wfile.write(xml)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return pem, server.server_port

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.skipif(shutil.which("rclone") is None, reason="optional real-rclone integration test")
@pytest.mark.parametrize("root_name", ["standard", "with, comma and spaces"])
def test_real_rclone_accepts_only_the_uploaded_certificate(svc, tls_server, root_name):
    pem, port = tls_server()
    remote = RemoteStorage(svc.backups.remote.root / root_name, BackupOAuth())
    data = {
        "provider": "webdav",
        "folder": "Backups",
        "options": {"url": f"https://localhost:{port}", "user": "test", "pass": "test"},
    }
    remote.save(data)

    def listing():
        return remote.rclone.json("lsjson", "remote:Backups", "--low-level-retries", "1")

    with pytest.raises(BackupError):
        listing()
    data["options"]["pass"] = ""
    remote.save({**data, "ca_certificate": pem})
    assert [entry["Name"] for entry in listing()] == ["proof.txt"]
    assert not any("no_check" in key for key in remote._options())
    remote.save({**data, "ca_certificate": make_certificate()[0]})
    with pytest.raises(BackupError):
        listing()
    remote.save({**data, "ca_certificate": None})
    with pytest.raises(BackupError):
        listing()


@pytest.mark.parametrize("failure", ["hostname", "expired"])
@pytest.mark.skipif(shutil.which("rclone") is None, reason="optional real-rclone integration test")
def test_upload_does_not_bypass_hostname_or_expiry_checks(svc, tls_server, failure):
    pem, port = tls_server(expired=failure == "expired")
    host = "127.0.0.1" if failure == "hostname" else "localhost"
    remote = svc.backups.remote
    remote.save(
        {
            "provider": "webdav",
            "folder": "Backups",
            "options": {"url": f"https://{host}:{port}", "user": "test", "pass": "test"},
            "ca_certificate": pem,
        }
    )
    with pytest.raises(BackupError):
        remote.rclone.json("lsjson", "remote:Backups", "--low-level-retries", "1")


@pytest.mark.skipif(shutil.which("rclone") is None, reason="optional real-rclone integration test")
def test_real_explicit_ftps_trusts_uploaded_certificate_before_login(svc, tmp_path):
    pem, key = make_certificate()
    cert_path, key_path = tmp_path / "ftp.pem", tmp_path / "ftp-key.pem"
    cert_path.write_text(pem)
    key_path.write_bytes(key)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    logins = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            self.wfile.write(b"220 Test FTP server\r\n")
            if self.rfile.readline().strip() != b"AUTH TLS":
                return
            self.wfile.write(b"234 Begin TLS\r\n")
            try:
                with context.wrap_socket(self.request, server_side=True) as secure:
                    command = secure.makefile("rb").readline().strip()
                    logins.append(command)
                    # Login intentionally fails; receiving USER proves that verified TLS succeeded.
                    secure.sendall(b"530 Test ends before authentication\r\n")
            except (ssl.SSLError, OSError):
                pass

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            remote = svc.backups.remote
            data = ftp(port=str(server.server_address[1]))
            data["options"]["host"] = "localhost"
            remote.save(data)
            with pytest.raises(BackupError):
                remote.rclone.json("lsjson", "remote:Backups", "--low-level-retries", "1")
            assert not logins
            data["options"]["pass"] = ""
            remote.save({**data, "ca_certificate": pem})
            with pytest.raises(BackupError):
                remote.rclone.json("lsjson", "remote:Backups", "--low-level-retries", "1")
            assert logins == [b"USER backup-user"]
        finally:
            server.shutdown()
            thread.join(3)
