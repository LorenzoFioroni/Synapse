import io
import json

from tests.conftest import make_pdf


def test_ping(client):
    client.post("/api/folders", json={"name": "Physics"})
    r = client.get("/api/connector/ping").get_json()
    assert r["app"] == "synapse" and r["folders"][0]["name"] == "Physics"


def test_save_from_page_metadata_without_network(client, monkeypatch):
    from backend import connector
    from backend.online.http import LookupFailed

    def offline(**k):
        raise LookupFailed("offline")

    monkeypatch.setattr(connector, "lookup", offline)
    f = client.post("/api/folders", json={"name": "Inbox"}).get_json()
    page = {
        "title": "A page title",
        "authors": ["Jane Doe", "Bañuls, Mari Carmen"],
        "year": "2021-03-04",
        "journal": "Phys. Rev. B",
        "doi": "10.1/xyz",
        "page_url": "https://journals.example/abs/1",
        "item_type": "journalArticle",
        "folder_id": f["id"],
        "tags": ["web", "to read"],
    }
    r = client.post("/api/connector/save", json=page)
    assert r.status_code == 201
    it = r.get_json()["item"]
    assert it["authors"] == "Doe, Jane; Bañuls, Mari Carmen" and it["year"] == 2021
    assert (
        it["folder_ids"] == [f["id"]] and it["tags"] == ["to read", "web"] and it["url"].startswith("https://journals")
    )
    assert r.get_json()["source"] == "page"
    # second save is reported as a duplicate, unless forced
    r2 = client.post("/api/connector/save", json=page)
    assert r2.status_code == 409 and r2.get_json()["duplicate_of"] == it["id"]
    assert client.post("/api/connector/save", json={**page, "allow_duplicate": True}).status_code == 201


def test_save_prefers_online_metadata_and_attaches_uploaded_pdf(client, monkeypatch, tmp_path):
    from backend import connector

    monkeypatch.setattr(
        connector,
        "lookup",
        lambda **k: {
            "title": "Online title",
            "authors": "Cui, Jian",
            "year": "2015",
            "doi": "10.1103/x",
            "item_type": "journalArticle",
        },
    )
    pdf = make_pdf(tmp_path / "p.pdf", "hello")
    meta = {"title": "page title", "doi": "10.1103/x", "page_url": "https://x.org/a", "pdf_name": "paper.pdf"}
    r = client.post(
        "/api/connector/save",
        data={"meta": json.dumps(meta), "file": (open(pdf, "rb"), "paper.pdf")},
        content_type="multipart/form-data",
    )
    body = r.get_json()
    assert r.status_code == 201 and body["source"] == "online" and body["pdf"] == "attached"
    assert body["item"]["title"] == "Online title" and body["item"]["url"] == "https://x.org/a"
    assert body["item"]["attachments"][0]["name"] == "paper.pdf"
    # a non-PDF upload (e.g. a login page) is refused
    r = client.post(
        "/api/connector/save",
        data={
            "meta": json.dumps({**meta, "allow_duplicate": True}),
            "file": (io.BytesIO(b"<html>login</html>"), "x.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert r.get_json()["pdf"] == "not-a-pdf" and r.get_json()["item"]["attachments"] == []


def test_save_needs_something(client):
    assert client.post("/api/connector/save", json={}).status_code == 400


def test_pdf_tab_uses_doi_inside_the_pdf(client, monkeypatch, tmp_path):
    from backend import connector

    seen = {}

    def fake(**k):
        seen.update(k)
        return {"title": "Found by DOI", "authors": "Doe, J", "year": "2019", "doi": "10.1103/PhysRevB.99.094435"}

    monkeypatch.setattr(connector, "lookup", fake)
    pdf = make_pdf(tmp_path / "x.pdf", "doi:10.1103/PhysRevB.99.094435")
    meta = {"title": "x.pdf", "page_url": "https://example.org/x.pdf", "item_type": "webpage"}
    r = client.post(
        "/api/connector/save",
        data={"meta": json.dumps(meta), "file": (open(pdf, "rb"), "x.pdf")},
        content_type="multipart/form-data",
    ).get_json()
    assert seen["identifier"] == "10.1103/PhysRevB.99.094435"
    assert r["item"]["title"] == "Found by DOI" and r["pdf"] == "attached"


def test_cors_only_for_extension_origins(client):
    r = client.get("/api/connector/ping", headers={"Origin": "chrome-extension://abc"})
    assert r.headers["Access-Control-Allow-Origin"] == "chrome-extension://abc"
    r = client.get("/api/connector/ping", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_file_existing_item_into_another_folder(client):
    a = client.post("/api/folders", json={"name": "A"}).get_json()
    b = client.post("/api/folders", json={"name": "B"}).get_json()
    item = client.post("/api/items", json={"title": "T", "folder_id": a["id"]}).get_json()
    r = client.post("/api/connector/file", json={"id": item["id"], "folder_id": b["id"], "tags": ["x"]}).get_json()
    assert r["item"]["folder_ids"] == [a["id"], b["id"]] and r["item"]["tags"] == ["x"]


def test_malformed_folder_id_is_refused(client):
    folder = client.post("/api/folders", json={"name": "A"}).get_json()
    for bad in ("inbox", "1.5", True, [1]):
        r = client.post("/api/connector/save", json={"title": "T", "folder_id": bad})
        assert r.status_code == 400 and "folder_id" in r.get_json()["error"]
    assert client.get("/api/items").get_json()["items"] == []  # nothing was saved
    # the popup sends the folder as a string
    r = client.post("/api/connector/save", json={"title": "T", "folder_id": str(folder["id"])})
    assert r.status_code == 201 and r.get_json()["item"]["folder_ids"] == [folder["id"]]

    item = r.get_json()["item"]
    b = client.post("/api/folders", json={"name": "B"}).get_json()
    assert client.post("/api/connector/file", json={"id": item["id"], "folder_id": "B"}).status_code == 400
    r = client.post("/api/connector/file", json={"id": item["id"], "folder_id": str(b["id"])}).get_json()
    assert r["item"]["folder_ids"] == [folder["id"], b["id"]]


def test_on_add_settings_for_the_browser_button(client, svc, monkeypatch):
    from backend import connector
    from backend.online.http import LookupFailed

    def offline(**k):
        raise LookupFailed("offline")

    monkeypatch.setattr(connector, "lookup", offline)
    searched = []
    monkeypatch.setattr(type(svc), "find_pdf_later", lambda self, item_id: searched.append(item_id))
    svc.settings.update({"add_find_pdf": "ask"})
    assert client.get("/api/connector/ping").get_json()["on_add"]["find_pdf"] == "ask"

    page = {"title": "No PDF here", "doi": "10.1/nopdf", "allow_duplicate": True}
    r = client.post("/api/connector/save", json=page).get_json()  # not ticked
    assert r["pdf"] == "none" and searched == []
    r = client.post("/api/connector/save", json={**page, "find_pdf": True}).get_json()
    assert r["pdf"] == "searching" and searched == [r["item"]["id"]]
    svc.settings.update({"add_find_pdf": "never"})
    r = client.post("/api/connector/save", json={**page, "find_pdf": True}).get_json()
    assert r["pdf"] == "none" and len(searched) == 1
    svc.settings.update({"add_find_pdf": "always"})
    r = client.post("/api/connector/save", json=page).get_json()
    assert r["pdf"] == "searching" and len(searched) == 2


def test_lookup_and_references_follow_the_settings(client, svc, monkeypatch):
    from backend import connector

    looked_up, queued = [], []

    def fake(**k):
        looked_up.append(k["identifier"])
        return {"title": "Online title", "doi": "10.1/refs", "item_type": "journalArticle"}

    monkeypatch.setattr(connector, "lookup", fake)
    monkeypatch.setattr(svc.citations, "enqueue", lambda ids: queued.extend(ids))
    page = {"title": "Page title", "doi": "10.1/refs", "allow_duplicate": True}

    # the popup's checkbox decides; references need only the DOI, not an online lookup
    svc.settings.update({"add_fetch_metadata": "always", "add_fetch_references": "always"})
    r = client.post("/api/connector/save", json={**page, "lookup": False}).get_json()
    assert r["source"] == "page" and looked_up == [] and queued == [r["item"]["id"]]

    # without the checkbox, Settings → "Retrieve metadata" decides
    svc.settings.update({"add_fetch_metadata": "never", "add_fetch_references": "never"})
    r = client.post("/api/connector/save", json=page).get_json()
    assert r["source"] == "page" and looked_up == [] and len(queued) == 1
    for setting in ("ask", "always"):
        svc.settings.update({"add_fetch_metadata": setting})
        r = client.post("/api/connector/save", json=page).get_json()
        assert r["source"] == "online" and r["item"]["title"] == "Online title"
    assert looked_up == ["10.1/refs", "10.1/refs"]

    # "ask" for references: the popup's answer
    svc.settings.update({"add_fetch_references": "ask"})
    r = client.post("/api/connector/save", json={**page, "lookup": False}).get_json()
    assert len(queued) == 1
    r = client.post("/api/connector/save", json={**page, "lookup": False, "fetch_references": True}).get_json()
    assert queued[-1] == r["item"]["id"] and len(queued) == 2
    # no identifier, no reference list
    svc.settings.update({"add_fetch_references": "always"})
    client.post("/api/connector/save", json={"title": "Just a web page"})
    assert len(queued) == 2


def test_background_pdf_search_attaches_the_file(svc, monkeypatch, tmp_path):
    from backend.online import pdf_finder

    item = svc.papers.create({"title": "Some paper", "doi": "10.1/x"})

    def fake(title, doi, pdf_dir, email, **kw):
        make_pdf(pdf_dir / "found.pdf", "x")
        return {"status": "success", "filename": "found.pdf", "source": "test"}

    monkeypatch.setattr(pdf_finder, "find_and_download", fake)
    svc.find_pdf_later(item["id"])
    import time

    for _ in range(50):
        if svc.papers.get(item["id"])["attachments"]:
            break
        time.sleep(0.05)
    assert svc.papers.get(item["id"])["attachments"][0]["name"] == "Full Text PDF"


def test_page_pdf_link_is_downloaded_only_from_public_addresses(client, monkeypatch):
    """The PDF link comes from the browser, so the server fetches it the way it fetches an
    attachment's web link: public addresses only, PDFs only (online/remote_pdf.py)."""
    import socket

    from backend import connector
    from backend.online import http
    from backend.online.http import LookupFailed

    def offline(**k):
        raise LookupFailed("offline")

    class Resp:
        status_code, ok, is_redirect, headers = 200, True, False, {}

        def iter_content(self, n):
            yield b"%PDF-1.4 fake"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fetched = []
    ips = {"journal.example": "93.184.216.34"}
    monkeypatch.setattr(connector, "lookup", offline)
    monkeypatch.setattr(
        http.socket,
        "getaddrinfo",
        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ips.get(host, host), 0))],
    )

    def fetch(method, url, **kwargs):
        http.resolve_public_url(url)
        fetched.append(url)
        return Resp()

    monkeypatch.setattr(http, "public_request", fetch)

    local = client.post(
        "/api/connector/save", json={"title": "Router page", "pdf_url": "http://127.0.0.1:631/x.pdf"}
    ).get_json()
    assert local["pdf"] == "failed" and fetched == []  # refused before any request

    public = client.post(
        "/api/connector/save", json={"title": "Journal page", "pdf_url": "https://journal.example/paper.pdf"}
    ).get_json()
    assert public["pdf"] == "downloaded" and fetched == ["https://journal.example/paper.pdf"]
    assert public["item"]["attachments"][0]["name"] == "Full Text PDF"


def test_missing_pdfs_lists_publisher_dois_without_a_pdf(client, svc, tmp_path):
    physics = client.post("/api/folders", json={"name": "Physics"}).get_json()
    sub = client.post("/api/folders", json={"name": "Optics", "parent_id": physics["id"]}).get_json()
    want = client.post("/api/items", json={"title": "Wanted", "doi": "10.1103/a", "folder_id": sub["id"]}).get_json()
    other = client.post("/api/items", json={"title": "Elsewhere", "doi": "10.1038/b"}).get_json()
    client.post("/api/items", json={"title": "No DOI"})
    client.post("/api/items", json={"title": "arXiv", "doi": "10.48550/arXiv.2101.00001"})
    has = client.post("/api/items", json={"title": "Has PDF", "doi": "10.1103/c"}).get_json()
    name = make_pdf(svc.config.pdf_dir / "c.pdf", "Has PDF").name
    svc.papers.add_attachment(has["id"], "Full Text PDF", name, "pdf")

    r = client.get("/api/connector/missing-pdfs").get_json()
    assert {i["id"] for i in r["items"]} == {want["id"], other["id"]} and r["total"] == 2
    assert r["items"][0].keys() == {"id", "title", "doi", "year"}
    # a folder counts with its subfolders
    r = client.get(f"/api/connector/missing-pdfs?folder_id={physics['id']}").get_json()
    assert [i["id"] for i in r["items"]] == [want["id"]]
    assert client.get("/api/connector/missing-pdfs?limit=1").get_json()["total"] == 2
    assert len(client.get("/api/connector/missing-pdfs?limit=1").get_json()["items"]) == 1
    assert client.get("/api/connector/missing-pdfs?folder_id=x").status_code == 400
    assert client.get("/api/connector/missing-pdfs?limit=x").status_code == 400


def test_attach_pdf_checks_the_paper(client, tmp_path):
    item = client.post("/api/items", json={"title": "Quench instabilities", "doi": "10.1103/w2vx-t1mr"}).get_json()

    def send(data: bytes):
        return client.post(
            "/api/connector/attach-pdf",
            data={"id": item["id"], "file": (io.BytesIO(data), "paper.pdf")},
            content_type="multipart/form-data",
        )

    assert send(b"<html>Sign in</html>").get_json()["status"] == "not-a-pdf"
    other = make_pdf(tmp_path / "other.pdf", "Something else entirely, doi 10.1038/zzz").read_bytes()
    assert send(other).get_json()["status"] == "wrong-paper"
    right = make_pdf(tmp_path / "right.pdf", "Phys. Rev. Lett. doi:10.1103/w2vx-t1mr").read_bytes()
    r = send(right).get_json()
    assert r["status"] == "attached" and r["item"]["attachments"][0]["name"] == "Full Text PDF"
    # never a second PDF, never over the first
    assert send(right).get_json()["status"] == "has-pdf"
    assert len(client.get(f"/api/items/{item['id']}").get_json()["attachments"]) == 1
    assert client.post("/api/connector/attach-pdf", data={}, content_type="multipart/form-data").status_code == 400
    assert (
        client.post("/api/connector/attach-pdf", data={"id": "nope"}, content_type="multipart/form-data").status_code
        == 404
    )
