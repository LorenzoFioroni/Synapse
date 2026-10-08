from tests.conftest import make_pdf


def test_create_edit_trash_restore(client):
    r = client.post("/api/items", json={"title": "Quantum Stuff", "authors": "Doe, Jane", "year": "2021"})
    assert r.status_code == 201
    item = r.get_json()
    assert item["citation_key"] == "doeQuantum2021"  # auto-generated with the default pattern
    assert item["date_added"]
    r = client.patch(f"/api/items/{item['id']}", json={"journal": "Nature", "bogus": "x"})
    assert r.get_json()["journal"] == "Nature" and "bogus" not in r.get_json()
    client.post("/api/items/trash", json={"ids": [item["id"]]})
    lib = client.get("/api/items").get_json()
    assert lib["items"] == [] and lib["counts"] == {"library": 0, "trash": 1}
    assert len(client.get("/api/items?view=trash").get_json()["items"]) == 1
    client.post("/api/items/restore", json={"ids": [item["id"]]})
    assert len(client.get("/api/items").get_json()["items"]) == 1
    client.post("/api/items/trash", json={"ids": [item["id"]]})
    client.post("/api/trash/empty")
    assert client.get("/api/items?view=trash").get_json()["items"] == []
    assert client.get(f"/api/items/{item['id']}").status_code == 404


def test_settings_validation_and_citekey_style(client):
    assert client.put("/api/settings", json={"theme": "neon"}).status_code == 400
    assert client.put("/api/settings", json={"nope": 1}).status_code == 400
    r = client.put("/api/settings", json={"citekey_pattern": "{Auth}{year}", "default_search_mode": "all"})
    assert r.status_code == 200
    item = client.post("/api/items", json={"title": "T", "authors": "Doe, J", "year": 2020}).get_json()
    assert item["citation_key"] == "Doe2020"
    item2 = client.post("/api/items", json={"title": "U", "authors": "Doe, J", "year": 2020}).get_json()
    assert item2["citation_key"] == "Doe2020a"
    prev = client.get("/api/settings/citekey_preview?pattern={auth}_{year}").get_json()
    assert prev["examples"][0] == "cui_2015"
    client.put("/api/settings", json={"citekey_pattern": "{auth}{year}"})
    keys = client.post("/api/items/citekeys", json={"ids": [item["id"], item2["id"]]}).get_json()["keys"]
    assert sorted(keys.values()) == ["doe2020", "doe2020a"]


def test_settings_persist(app, tmp_path):
    from backend import create_app
    from backend.config import Config
    c = app.test_client()
    c.put("/api/settings", json={"density": "compact"})
    app2 = create_app(Config.from_env(tmp_path / "data"), start_indexer=False)
    assert app2.test_client().get("/api/settings").get_json()["settings"]["density"] == "compact"


def test_attachments_and_fulltext_search(client, svc, tmp_path):
    item = client.post("/api/items", json={"title": "Plain title", "authors": "Doe, Jane"}).get_json()
    pdf = make_pdf(tmp_path / "paper.pdf", "The Lindblad master equation describes open systems")
    r = client.post(f"/api/items/{item['id']}/attachments",
                    data={"file": (open(pdf, "rb"), "paper.pdf")}, content_type="multipart/form-data")
    assert r.status_code == 201
    att = r.get_json()["attachments"][0]
    svc.indexer.wait(30)
    svc.indexer.sync(background=False)
    assert client.get("/api/items?q=lindblad&mode=all").get_json()["items"] == []
    res = client.get("/api/items?q=lindblad&mode=fulltext").get_json()
    assert [i["id"] for i in res["items"]] == [item["id"]]
    assert "<mark>" in res["hits"][item["id"]]["fulltext"]
    # file is served, rename works, delete removes from the index
    assert client.get(f"/pdfs/{att['url']}").status_code == 200
    assert client.patch(f"/api/attachments/{att['id']}", json={"name": "Main"}).get_json()["name"] == "Main"
    client.delete(f"/api/attachments/{att['id']}")
    assert client.get("/api/items?q=lindblad&mode=fulltext").get_json()["items"] == []
    assert svc.fulltext.stats()["documents"] == 0


def test_fulltext_index_is_separate_file(svc):
    assert svc.config.fulltext_db_path != svc.config.db_path
    assert svc.config.fulltext_db_path.exists()


def test_annotations_roundtrip(client):
    item = client.post("/api/items", json={"title": "A"}).get_json()
    client.post("/api/annotations", json={"paper_id": item["id"], "annotations": {"1": [{"t": "x"}]}})
    assert client.get(f"/api/annotations?paper_id={item['id']}").get_json() == {"annotations": {"1": [{"t": "x"}]}}


def test_schema(client):
    s = client.get("/api/schema").get_json()
    assert any(f["key"] == "doi" for f in s["fields"]) and s["typeFields"]["journalArticle"]


def test_legacy_layout_migration(tmp_path, monkeypatch):
    import sqlite3
    from backend import config as cfg
    root = tmp_path / "app"
    (root / "pdf_storage").mkdir(parents=True)
    (root / "pdf_storage" / "x.pdf").write_bytes(b"%PDF")
    con = sqlite3.connect(root / "papers.db")
    con.execute("CREATE TABLE papers (id TEXT PRIMARY KEY, title TEXT, authors TEXT, year INTEGER, journal TEXT, "
                "abstract TEXT, pdfUrl TEXT)")
    con.execute("INSERT INTO papers (id, title) VALUES ('p1', 'Old')")
    con.commit()
    con.close()
    monkeypatch.setattr(cfg, "APP_ROOT", root)
    from backend import create_app
    app = create_app(cfg.Config(root / "data"), start_indexer=False)
    items = app.test_client().get("/api/items").get_json()["items"]
    assert items[0]["title"] == "Old" and items[0]["date_added"] and items[0]["item_type"] == "journalArticle"
    assert (root / "data" / "pdfs" / "x.pdf").exists() and not (root / "papers.db").exists()
