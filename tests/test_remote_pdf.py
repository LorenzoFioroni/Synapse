"""PDFs of attachments stored as web links: only the attachment's own URL, only
public addresses, only PDFs, and a size cap. The network and DNS are faked."""

import socket

import pytest

from backend.online import http, remote_pdf

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"

ADDRESSES = {
    "journal.example": "93.184.216.34",
    "router.example": "192.168.1.1",
    "meta.example": "169.254.169.254",
    "local.example": "127.0.0.1",
    "v6local.example": "::1",
    "mapped.example": "::ffff:10.0.0.5",
}


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    @property
    def ok(self):
        return self.status_code < 400

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i : i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def net(monkeypatch):
    """routes: url → FakeResponse; records the URLs requested."""
    routes, calls = {}, []

    def fake_getaddrinfo(host, *a, **k):
        if host not in ADDRESSES:
            raise socket.gaierror("unknown")
        ip = ADDRESSES[host]
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (ip, 0))]

    def fake_get(method, url, **kw):
        http.resolve_public_url(url)
        assert kw.get("allow_redirects") is False  # redirects are checked one by one
        calls.append(url)
        return routes.get(url, FakeResponse(404))

    monkeypatch.setattr(http.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(http, "public_request", fake_get)
    return routes, calls


def remote_attachment(svc, url):
    item = svc.papers.create({"title": "Linked"})
    return svc.papers.add_attachment(item["id"], "Linked PDF", url, "pdf")


def test_opens_the_attachments_pdf(client, svc, net):
    routes, _ = net
    routes["https://journal.example/a.pdf"] = FakeResponse(200, PDF, {"Content-Type": "application/pdf"})
    att = remote_attachment(svc, "https://journal.example/a.pdf")
    r = client.get(f"/api/attachments/{att['id']}/remote")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data == PDF


def test_follows_public_redirects(client, svc, net):
    routes, calls = net
    routes["https://journal.example/a"] = FakeResponse(302, headers={"Location": "/b.pdf"})
    routes["https://journal.example/b.pdf"] = FakeResponse(200, PDF)
    att = remote_attachment(svc, "https://journal.example/a")
    assert client.get(f"/api/attachments/{att['id']}/remote").status_code == 200
    assert calls == ["https://journal.example/a", "https://journal.example/b.pdf"]


@pytest.mark.parametrize(
    "host", ["router.example", "meta.example", "local.example", "v6local.example", "mapped.example"]
)
def test_local_network_is_refused(client, svc, net, host):
    _, calls = net
    att = remote_attachment(svc, f"http://{host}/admin")
    r = client.get(f"/api/attachments/{att['id']}/remote")
    assert r.status_code == 403
    assert calls == []  # nothing was requested


def test_redirect_into_local_network_is_refused(client, svc, net):
    routes, calls = net
    routes["https://journal.example/a.pdf"] = FakeResponse(302, headers={"Location": "http://192.168.1.1/"})
    ADDRESSES["192.168.1.1"] = "192.168.1.1"
    try:
        att = remote_attachment(svc, "https://journal.example/a.pdf")
        assert client.get(f"/api/attachments/{att['id']}/remote").status_code == 403
        assert calls == ["https://journal.example/a.pdf"]
    finally:
        del ADDRESSES["192.168.1.1"]


def test_not_a_pdf(client, svc, net):
    routes, _ = net
    routes["https://journal.example/page"] = FakeResponse(200, b"<html>login</html>", {"Content-Type": "text/html"})
    att = remote_attachment(svc, "https://journal.example/page")
    r = client.get(f"/api/attachments/{att['id']}/remote")
    assert r.status_code == 415 and "PDF" in r.get_json()["error"]


def test_too_large(client, svc, net, monkeypatch):
    routes, _ = net
    monkeypatch.setattr(remote_pdf, "MAX_BYTES", 1000)
    routes["https://journal.example/big.pdf"] = FakeResponse(200, PDF + b"x" * 5000)
    routes["https://journal.example/declared.pdf"] = FakeResponse(200, PDF, {"Content-Length": "999999"})
    for name in ("big", "declared"):
        att = remote_attachment(svc, f"https://journal.example/{name}.pdf")
        r = client.get(f"/api/attachments/{att['id']}/remote")
        assert r.status_code == 413, name


def test_redirect_loop(client, svc, net):
    routes, calls = net
    routes["https://journal.example/loop"] = FakeResponse(302, headers={"Location": "/loop"})
    att = remote_attachment(svc, "https://journal.example/loop")
    assert client.get(f"/api/attachments/{att['id']}/remote").status_code == 502
    assert len(calls) == remote_pdf.MAX_REDIRECTS + 1


def test_local_files_and_missing_attachments(client, svc, net):
    item = svc.papers.create({"title": "Local"})
    att = svc.papers.add_attachment(item["id"], "file", "abc_paper.pdf", "pdf")
    assert client.get(f"/api/attachments/{att['id']}/remote").status_code == 404
    assert client.get("/api/attachments/99999/remote").status_code == 404


def test_old_open_proxy_is_gone(client, net):
    _, calls = net
    r = client.get("/api/proxy?url=https://journal.example/a.pdf")
    assert r.status_code == 404 and calls == []
