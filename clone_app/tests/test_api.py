import io

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
    assert lib["items"] == [] and lib["counts"] == {"library": 0, "trash": 1, "duplicates": 0}
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


def test_import_preview_commit_and_export(client):
    bib = "@article{a1, title={First Paper}, author={Doe, Jane}, year={2020}, doi={10.1/x}}\n" \
          "@article{a2, title={Second Paper}, author={Roe, Rick}, year={2021}}"
    prev = client.post("/api/import/preview", data={"file": (io.BytesIO(bib.encode()), "refs.bib")},
                       content_type="multipart/form-data").get_json()
    assert prev["format"] == "bibtex" and len(prev["entries"]) == 2
    res = client.post("/api/import/commit", json={"entries": prev["entries"]}).get_json()
    assert len(res["created"]) == 2
    # second import: duplicates detected and skipped
    prev2 = client.post("/api/import/preview", json={"text": bib}).get_json()
    assert all(e["duplicate_of"] for e in prev2["entries"])
    res2 = client.post("/api/import/commit", json={"entries": prev2["entries"], "skip_duplicates": True}).get_json()
    assert res2 == {"created": [], "skipped": 2}
    for fmt in ("bibtex", "ris", "endnote", "endnote_xml", "csljson"):
        r = client.get(f"/api/export?format={fmt}")
        assert r.status_code == 200 and "First Paper" in r.get_data(as_text=True)
        assert "attachment" in r.headers["Content-Disposition"]
    one = client.get(f"/api/export?format=bibtex&ids={res['created'][0]}&download=0").get_json()
    assert one["count"] == 1 and one["text"].startswith("@article{a1,")


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


def test_empty_item_gets_citekey_once_it_has_metadata(client):
    item = client.post("/api/items", json={}).get_json()
    assert not item["citation_key"]
    item = client.patch(f"/api/items/{item['id']}", json={"title": "Tensor Networks"}).get_json()
    assert not item["citation_key"]  # no authors yet
    item = client.patch(f"/api/items/{item['id']}", json={"authors": "Doe, Jane"}).get_json()
    assert not item["citation_key"]  # the default pattern also needs the year
    item = client.patch(f"/api/items/{item['id']}", json={"year": 2026}).get_json()
    assert item["citation_key"] == "doeTensor2026"
    # later edits don't silently change an existing key
    item = client.patch(f"/api/items/{item['id']}", json={"title": "Other"}).get_json()
    assert item["citation_key"] == "doeTensor2026"


def test_items_from_pdf_without_network(client, tmp_path, monkeypatch):
    from backend import metadata

    def offline(*a, **k):
        raise metadata.LookupError_("offline")
    monkeypatch.setattr(metadata, "lookup", offline)
    pdf = make_pdf(tmp_path / "Some_Paper_Title.pdf", "Published as doi:10.1103/PhysRevB.99.094435 in PRB")
    r = client.post("/api/items/from_pdf", data={"file": (open(pdf, "rb"), "Some_Paper_Title.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 201
    c = r.get_json()["created"][0]
    assert c["identifier"] == {"doi": "10.1103/PhysRevB.99.094435"}
    assert c["item"]["title"] == "Some Paper Title" and c["item"]["doi"] == "10.1103/PhysRevB.99.094435"
    assert len(c["item"]["attachments"]) == 1


def test_items_from_pdf_with_metadata(client, tmp_path, monkeypatch):
    from backend import metadata
    monkeypatch.setattr(metadata, "lookup", lambda **k: {"title": "Asymmetry", "authors": "Doe, J", "year": "2019",
                                                         "doi": k.get("doi")})
    pdf = make_pdf(tmp_path / "x.pdf", "doi: 10.1103/PhysRevB.99.094435")
    c = client.post("/api/items/from_pdf", data={"file": (open(pdf, "rb"), "x.pdf")},
                    content_type="multipart/form-data").get_json()["created"][0]
    assert c["item"]["title"] == "Asymmetry" and c["item"]["citation_key"] == "doeAsymmetry2019"


def test_duplicates_view(client):
    a = client.post("/api/items", json={"title": "Quantum transport in chains", "year": 2020, "doi": "10.1/A"}).get_json()
    b = client.post("/api/items", json={"title": "Quantum Transport in Chains", "year": 2020}).get_json()
    c = client.post("/api/items", json={"title": "Other", "doi": "10.1/a"}).get_json()
    client.post("/api/items", json={"title": "Unrelated paper about things"})
    res = client.get("/api/items?view=duplicates").get_json()
    assert {i["id"] for i in res["items"]} == {a["id"], b["id"], c["id"]}
    assert len({i["dup_group"] for i in res["items"]}) == 1
    assert res["counts"]["duplicates"] == 3
    client.post("/api/items/trash", json={"ids": [b["id"], c["id"]]})
    assert client.get("/api/items?view=duplicates").get_json()["items"] == []
