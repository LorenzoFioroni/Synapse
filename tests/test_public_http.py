"""Real HTTP/TLS over local sockets, with public DNS answers and a rebinding trap."""

import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from backend.online import http, pdf_finder, remote_pdf
from backend.plugins.http import HttpError, Offline, PluginHttp

PUBLIC = "93.184.216.34"
PDF = b"%PDF-1.4 test"


def dns_answer(ip, port):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    address = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address


@pytest.fixture
def origin(tmp_path):
    hits, names = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append((self.path, self.headers["Host"]))
            if self.path in ("/private", "/same-host", "/other-host"):
                target = {
                    "/private": "http://127.0.0.1/private",
                    "/same-host": "/pdf",
                    "/other-host": "http://other.example/pdf",
                }[self.path]
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(PDF)))
            self.end_headers()
            self.wfile.write(PDF)

    servers = []
    for secure in (False, True):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if secure:
            key = ec.generate_private_key(ec.SECP256R1())
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "journal.example")])
            now = datetime.now(timezone.utc)
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("journal.example")]), critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256())
            )
            cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
            cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_file.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                )
            )
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cert_file, key_file)
            ctx.set_servername_callback(lambda sock, name, ctx: names.append(name))
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    yield servers, hits, names, str(cert_file)
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def pinned_network(monkeypatch, origin):
    servers, hits, names, cert = origin
    lookups, connections = [], []
    answers = {}
    connect = socket.socket.connect

    def resolve(host, port, **kwargs):
        lookups.append(host)
        ips = answers.get(host, [PUBLIC])
        if isinstance(ips, Exception):
            raise ips
        return [dns_answer(ip, port) for ip in ips]

    def route(sock, address):
        connections.append(address)
        # Route only the validated test public address to our local origin. Any private
        # connection attempt fails here instead of accidentally reaching a real service.
        assert address[0] == PUBLIC, address
        return connect(sock, ("127.0.0.1", address[1]))

    monkeypatch.setattr(http.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", route)
    monkeypatch.setattr(http, "HOST_GAPS", http.HostGaps())
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    return servers, hits, names, cert, lookups, connections, answers


def test_http_connects_only_to_first_validated_dns_answer(pinned_network, monkeypatch):
    servers, hits, _, _, lookups, connections, _ = pinned_network
    port = servers[0].server_port

    def rebind(host, port, **kwargs):
        lookups.append(host)
        return [dns_answer(PUBLIC if len(lookups) == 1 else "127.0.0.1", port)]

    monkeypatch.setattr(http.socket, "getaddrinfo", rebind)
    url = f"http://journal.example:{port}/pdf"
    with http.public_request("GET", url, timeout=2, stream=True) as response:
        assert response.content == PDF
        assert response.url == url
    assert lookups == ["journal.example"]
    assert connections == [(PUBLIC, port)]
    assert hits == [("/pdf", f"journal.example:{port}")]


def test_https_retains_host_sni_and_certificate_verification(pinned_network):
    servers, hits, names, cert, lookups, connections, _ = pinned_network
    port = servers[1].server_port
    with http.public_request("GET", f"https://journal.example:{port}/pdf", verify=cert, timeout=2) as response:
        assert response.content == PDF
    assert hits == [("/pdf", f"journal.example:{port}")]
    assert names == ["journal.example"]
    assert lookups == ["journal.example"]
    assert connections == [(PUBLIC, port)]
    with pytest.raises(requests.exceptions.SSLError):
        http.public_request("GET", f"https://wrong.example:{port}/pdf", verify=cert, timeout=2)
    with pytest.raises(requests.exceptions.SSLError):
        http.public_request("GET", f"https://journal.example:{port}/pdf", timeout=2)
    assert len(hits) == 1  # TLS failures never send the HTTP request.


@pytest.mark.parametrize("consumer", ["plugin", "pdf", "finder"])
@pytest.mark.parametrize("target", ["private", "same-host", "other-host"])
def test_each_redirect_is_revalidated(pinned_network, monkeypatch, consumer, target, tmp_path):
    servers, hits, _, _, lookups, connections, answers = pinned_network
    port = servers[0].server_port
    url = f"http://journal.example:{port}/{target}"
    if target == "other-host":
        answers["other.example"] = ["10.0.0.1"]
    if target == "private":
        answers["127.0.0.1"] = ["127.0.0.1"]
    if target == "same-host":

        def rebind(host, port, **kwargs):
            lookups.append(host)
            return [dns_answer(PUBLIC if len(lookups) == 1 else "127.0.0.1", port)]

        monkeypatch.setattr(http.socket, "getaddrinfo", rebind)
    if consumer == "finder":
        assert pdf_finder._download_first([url], None, None, None, tmp_path) is None
        assert not list(tmp_path.glob("*_downloaded.pdf"))
    elif consumer == "pdf":
        with pytest.raises(remote_pdf.RemoteError) as error:
            remote_pdf.fetch_pdf(url, "test")
        assert error.value.status == 403
    else:
        client = PluginHttp("test", ["journal.example", "other.example", "127.0.0.1"], lambda: "test")
        with pytest.raises(HttpError) as error:
            client.get(url)
        assert error.value.status == 403
    assert len(hits) == len(connections) == 1
    assert len(lookups) == 2


@pytest.mark.parametrize("consumer", ["plugin", "pdf", "finder"])
def test_all_consumers_use_the_pinned_transport(pinned_network, consumer, monkeypatch, tmp_path):
    servers, hits, _, _, lookups, connections, _ = pinned_network
    port = servers[0].server_port
    url = f"http://journal.example:{port}/pdf"
    if consumer == "finder":
        monkeypatch.setattr(pdf_finder, "verify_pdf", lambda *args: True)
        result = pdf_finder._download_first([url], None, None, None, tmp_path)
        assert result["status"] == "success"
        assert (tmp_path / result["filename"]).read_bytes() == PDF
    elif consumer == "pdf":
        assert remote_pdf.fetch_pdf(url, "test") == PDF
    else:
        assert PluginHttp("test", ["journal.example"], lambda: "test").get(url).content == PDF
    assert lookups == ["journal.example"]
    assert connections == [(PUBLIC, port)]
    assert len(hits) == 1


@pytest.mark.parametrize(
    "ips",
    [
        [PUBLIC, "10.0.0.1"],
        ["::ffff:127.0.0.1"],
        ["::1"],
        ["169.254.169.254"],
        ["224.0.0.1"],
        ["ff02::1"],
        ["0.0.0.0"],
        ["192.168.1.1"],
    ],
)
def test_nonpublic_or_mixed_answers_never_connect(pinned_network, ips):
    _, hits, _, _, _, connections, answers = pinned_network
    answers["journal.example"] = ips
    with pytest.raises(http.PublicAddressError):
        http.public_request("GET", "http://journal.example/pdf", timeout=2)
    assert not connections and not hits


def test_dns_failure_is_reported_without_connecting(pinned_network):
    _, _, _, _, _, connections, answers = pinned_network
    client = PluginHttp("test", ["journal.example"], lambda: "test")
    for failure in ([], socket.gaierror("missing")):
        answers["journal.example"] = failure
        with pytest.raises(Offline, match="Cannot resolve"):
            client.get("https://journal.example/pdf")
        with pytest.raises(remote_pdf.RemoteError) as error:
            remote_pdf.fetch_pdf("https://journal.example/pdf", "test")
        assert error.value.status == 502
    assert not connections


def test_public_ipv6_addresses_are_retained(monkeypatch):
    ips = ["2606:4700:4700::1111", "::ffff:93.184.216.34"]
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda host, port, **kwargs: [dns_answer(ip, port) for ip in ips])
    addresses = http.resolve_public_url("https://journal.example:8443/pdf")
    assert [address.sockaddr for address in addresses] == [(ip, 8443, 0, 0) for ip in ips]


def test_failed_address_is_closed_and_only_checked_alternatives_are_tried(pinned_network, monkeypatch):
    servers, _, _, _, lookups, connections, answers = pinned_network
    answers["journal.example"] = ["1.1.1.1", PUBLIC]
    connect = socket.socket.connect
    failed = []

    def fail_first(sock, address):
        if address[0] == "1.1.1.1":
            failed.append(sock)
            raise ConnectionRefusedError("first address unavailable")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", fail_first)
    port = servers[0].server_port
    with http.public_request("GET", f"http://journal.example:{port}/pdf", timeout=2) as response:
        assert response.content == PDF
    assert lookups == ["journal.example"]
    assert connections == [(PUBLIC, port)]
    assert len(failed) == 1 and failed[0].fileno() == -1


def test_connect_timeouts_are_reported_and_sockets_closed(pinned_network, monkeypatch):
    sockets = []

    def timeout(sock, address):
        sockets.append(sock)
        raise TimeoutError("connect timed out")

    monkeypatch.setattr(socket.socket, "connect", timeout)
    client = PluginHttp("test", ["journal.example"], lambda: "test")
    with pytest.raises(Offline):
        client.get("https://journal.example/pdf", timeout=0.1)
    assert sockets and all(sock.fileno() == -1 for sock in sockets)


@pytest.mark.parametrize("url", ["ftp://journal.example/pdf", "http://journal.example:99999/pdf"])
def test_invalid_web_urls_never_connect(pinned_network, url):
    _, _, _, _, lookups, connections, _ = pinned_network
    with pytest.raises((http.PublicAddressError, requests.RequestException)):
        http.public_request("GET", url, timeout=2)
    assert not lookups and not connections


@pytest.mark.parametrize(
    "address,public",
    [
        ("93.184.216.34", True),
        ("2606:4700:4700::1111", True),
        ("::ffff:93.184.216.34", True),
        ("64:ff9b::5db8:d822", True),  # DNS64 answer for a public IPv4 host
        ("::ffff:127.0.0.1", False),
        ("64:ff9b::7f00:1", False),  # NAT64 to 127.0.0.1
        ("64:ff9b::a9fe:a9fe", False),  # NAT64 to 169.254.169.254
        ("64:ff9b:1::a00:1", False),
        ("2002:7f00:1::", False),  # 6to4 of 127.0.0.1
        ("2002:c0a8:101::", False),  # 6to4 of 192.168.1.1
        ("::127.0.0.1", False),
        ("::10.0.0.1", False),
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", False),  # Teredo
        ("fec0::1", False),
        ("fc00::1", False),
        ("fe80::1", False),
    ],
)
def test_embedded_ipv4_addresses_are_judged_by_the_ipv4_address(address, public):
    import ipaddress

    assert http.is_public_address(ipaddress.ip_address(address)) is public


@pytest.mark.parametrize(
    "url",
    ["https://user:secret@journal.example/pdf", "https://journal.example@127.0.0.1/pdf", "ftp://journal.example/pdf"],
)
def test_addresses_with_credentials_or_other_schemes_are_refused_before_dns(pinned_network, url):
    *_, lookups, connections, _ = pinned_network
    with pytest.raises(http.PublicAddressError) as refused:
        http.public_request("GET", url, timeout=2)
    assert refused.value.status == 400
    assert lookups == [] and connections == []
