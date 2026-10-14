import io
import json

from tests.conftest import make_pdf


def test_ping(client):
    client.post("/api/folders", json={"name": "Physics"})
    r = client.get("/api/connector/ping").get_json()
    assert r["app"] == "synapse" and r["folders"][0]["name"] == "Physics"


def test_save_from_page_metadata_without_network(client, monkeypatch):
    from backend.api import connector
    from backend.online.http import LookupError_

    def offline(**k):
        raise LookupError_("offline")
    monkeypatch.setattr(connector, "lookup", offline)
    f = client.post("/api/folders", json={"name": "Inbox"}).get_json()
    page = {"title": "A page title", "authors": ["Jane Doe", "Bañuls, Mari Carmen"], "year": "2021-03-04",
            "journal": "Phys. Rev. B", "doi": "10.1/xyz", "page_url": "https://journals.example/abs/1",
            "item_type": "journalArticle", "folder_id": f["id"], "tags": ["web", "to read"]}
    r = client.post("/api/connector/save", json=page)
    assert r.status_code == 201
    it = r.get_json()["item"]
    assert it["authors"] == "Doe, Jane; Bañuls, Mari Carmen" and it["year"] == 2021
    assert it["folder_ids"] == [f["id"]] and it["tags"] == ["to read", "web"] and it["url"].startswith("https://journals")
    assert r.get_json()["source"] == "page"
    # second save is reported as a duplicate, unless forced
    r2 = client.post("/api/connector/save", json=page)
    assert r2.status_code == 409 and r2.get_json()["duplicate_of"] == it["id"]
    assert client.post("/api/connector/save", json={**page, "allow_duplicate": True}).status_code == 201


def test_save_prefers_online_metadata_and_attaches_uploaded_pdf(client, monkeypatch, tmp_path):
    from backend.api import connector
    monkeypatch.setattr(connector, "lookup", lambda **k: {"title": "Online title", "authors": "Cui, Jian",
                                                          "year": "2015", "doi": "10.1103/x", "item_type": "journalArticle"})
    pdf = make_pdf(tmp_path / "p.pdf", "hello")
    meta = {"title": "page title", "doi": "10.1103/x", "page_url": "https://x.org/a", "pdf_name": "paper.pdf"}
    r = client.post("/api/connector/save", data={"meta": json.dumps(meta), "file": (open(pdf, "rb"), "paper.pdf")},
                    content_type="multipart/form-data")
    body = r.get_json()
    assert r.status_code == 201 and body["source"] == "online" and body["pdf"] == "attached"
    assert body["item"]["title"] == "Online title" and body["item"]["url"] == "https://x.org/a"
    assert body["item"]["attachments"][0]["name"] == "paper.pdf"
    # a non-PDF upload (e.g. a login page) is refused
    r = client.post("/api/connector/save", data={"meta": json.dumps({**meta, "allow_duplicate": True}),
                                                 "file": (io.BytesIO(b"<html>login</html>"), "x.pdf")},
                    content_type="multipart/form-data")
    assert r.get_json()["pdf"] == "not-a-pdf" and r.get_json()["item"]["attachments"] == []


def test_save_needs_something(client):
    assert client.post("/api/connector/save", json={}).status_code == 400


def test_pdf_tab_uses_doi_inside_the_pdf(client, monkeypatch, tmp_path):
    from backend.api import connector
    seen = {}

    def fake(**k):
        seen.update(k)
        return {"title": "Found by DOI", "authors": "Doe, J", "year": "2019", "doi": "10.1103/PhysRevB.99.094435"}
    monkeypatch.setattr(connector, "lookup", fake)
    pdf = make_pdf(tmp_path / "x.pdf", "doi:10.1103/PhysRevB.99.094435")
    meta = {"title": "x.pdf", "page_url": "https://example.org/x.pdf", "item_type": "webpage"}
    r = client.post("/api/connector/save", data={"meta": json.dumps(meta), "file": (open(pdf, "rb"), "x.pdf")},
                    content_type="multipart/form-data").get_json()
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


def test_on_add_settings_for_the_browser_button(client, svc, monkeypatch):
    from backend.api import connector
    from backend.online.http import LookupError_

    def offline(**k):
        raise LookupError_("offline")
    monkeypatch.setattr(connector, "lookup", offline)
    searched = []
    monkeypatch.setattr(type(svc), "find_pdf_later", lambda self, item_id: searched.append(item_id))
    svc.settings.update({"add_find_pdf": "ask"})
    assert client.get("/api/connector/ping").get_json()["on_add"]["find_pdf"] == "ask"

    page = {"title": "No PDF here", "doi": "10.1/nopdf", "allow_duplicate": True}
    r = client.post("/api/connector/save", json=page).get_json()          # not ticked
    assert r["pdf"] == "none" and searched == []
    r = client.post("/api/connector/save", json={**page, "find_pdf": True}).get_json()
    assert r["pdf"] == "searching" and searched == [r["item"]["id"]]
    svc.settings.update({"add_find_pdf": "never"})
    r = client.post("/api/connector/save", json={**page, "find_pdf": True}).get_json()
    assert r["pdf"] == "none" and len(searched) == 1
    svc.settings.update({"add_find_pdf": "always"})
    r = client.post("/api/connector/save", json=page).get_json()
    assert r["pdf"] == "searching" and len(searched) == 2


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
