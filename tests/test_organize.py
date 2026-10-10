import io


def mk(client, **data):
    return client.post("/api/items", json={"title": "T", "authors": "Doe, J", "year": 2020, **data}).get_json()


def test_folders_tree_and_multiple_membership(client):
    phys = client.post("/api/folders", json={"name": "Physics"}).get_json()
    tr = client.post("/api/folders", json={"name": "Transport", "parent_id": phys["id"]}).get_json()
    rg = client.post("/api/folders", json={"name": "Reading group"}).get_json()
    assert client.post("/api/folders", json={"name": "physics"}).status_code == 400   # sibling names are unique
    assert client.post("/api/folders", json={"name": "  "}).status_code == 400
    a, b = mk(client, title="A"), mk(client, title="B", folder_id=tr["id"])
    assert b["folder_ids"] == [tr["id"]]
    # an item can be in several folders; adding twice is harmless
    client.post("/api/items/folders", json={"ids": [a["id"]], "add": [tr["id"], rg["id"]]})
    client.post("/api/items/folders", json={"ids": [a["id"]], "add": [rg["id"]]})
    assert client.get(f"/api/items/{a['id']}").get_json()["folder_ids"] == [tr["id"], rg["id"]]
    ids = lambda v: sorted(i["id"] for i in client.get(f"/api/items?view={v}").get_json()["items"])
    assert ids(f"folder:{rg['id']}") == [a["id"]] and ids(f"folder:{tr['id']}") == sorted([a["id"], b["id"]])
    # the item is stored once: still one library row, no duplicated data
    lib = client.get("/api/items").get_json()
    assert lib["counts"]["library"] == 2 and len(lib["items"]) == 2
    # parent folder shows subfolder items (default setting)
    assert ids(f"folder:{phys['id']}") == sorted([a["id"], b["id"]])
    client.put("/api/settings", json={"show_subfolder_items": False})
    assert ids(f"folder:{phys['id']}") == []
    tree = {f["name"]: f for f in lib["sidebar"]["folders"]}
    assert tree["Transport"]["count"] == 2 and tree["Reading group"]["count"] == 1
    # "move" = remove from one folder + add to another; removing never deletes the item
    client.post("/api/items/folders", json={"ids": [a["id"]], "remove": [tr["id"]], "add": [phys["id"]]})
    assert client.get(f"/api/items/{a['id']}").get_json()["folder_ids"] == [phys["id"], rg["id"]]
    client.patch(f"/api/items/{a['id']}", json={"folder_ids": [rg["id"]]})          # replace all memberships
    assert client.get(f"/api/items/{a['id']}").get_json()["folder_ids"] == [rg["id"]]
    assert client.post("/api/items/folders", json={"ids": [a["id"]], "add": [999]}).status_code == 404
    unfiled = lambda: sorted(i["id"] for i in client.get("/api/items?q=folder:none").get_json()["items"])
    assert unfiled() == []
    # no cycles
    assert client.patch(f"/api/folders/{phys['id']}", json={"parent_id": tr["id"]}).status_code == 400
    r = client.patch(f"/api/folders/{tr['id']}", json={"name": "Transport theory", "parent_id": None}).get_json()
    assert r["name"] == "Transport theory" and r["parent_id"] is None
    # deleting a folder keeps its items; only those in no other folder end up in none
    client.post("/api/items/folders", json={"ids": [b["id"]], "add": [rg["id"]]})
    res = client.delete(f"/api/folders/{tr['id']}").get_json()
    assert res["unfiled"] == 0 and unfiled() == []
    res = client.delete(f"/api/folders/{rg['id']}").get_json()
    assert res["unfiled"] == 2 and unfiled() == sorted([a["id"], b["id"]])
    assert client.get("/api/items").get_json()["counts"].keys() == {"library", "trash", "duplicates"}
    assert client.get("/api/items").get_json()["counts"]["library"] == 2


def test_tags(client):
    a, b = mk(client, title="A", tags=["Quantum"]), mk(client, title="B")
    assert a["tags"] == ["Quantum"]
    client.post("/api/items/tags", json={"ids": [a["id"], b["id"]], "add": ["transport", "QUANTUM"]})
    assert client.get(f"/api/items/{b['id']}").get_json()["tags"] == ["Quantum", "transport"]  # existing casing wins
    tags = {t["name"]: t for t in client.get("/api/tags").get_json()["tags"]}
    assert tags["Quantum"]["count"] == 2
    # filter by tags (AND)
    res = client.get("/api/items?tags=Quantum,transport").get_json()["items"]
    assert len(res) == 2
    client.patch(f"/api/items/{a['id']}", json={"tags": ["Quantum"]})            # replace an item's tags
    assert [i["id"] for i in client.get("/api/items?tags=transport").get_json()["items"]] == [b["id"]]
    # colour + rename-merge
    assert client.patch(f"/api/tags/{tags['transport']['id']}", json={"color": "red"}).status_code == 400
    client.patch(f"/api/tags/{tags['transport']['id']}", json={"color": "#e5484d"})
    merged = client.patch(f"/api/tags/{tags['transport']['id']}", json={"name": "quantum"}).get_json()
    assert merged["name"] == "Quantum"
    assert client.get(f"/api/items/{b['id']}").get_json()["tags"] == ["Quantum"]
    # unused tags without a colour disappear
    client.post("/api/items/tags", json={"ids": [a["id"], b["id"]], "remove": ["Quantum"]})
    assert [t["name"] for t in client.get("/api/tags").get_json()["tags"]] == ["Quantum"]  # kept: it has a colour now
    client.delete(f"/api/tags/{merged['id']}")
    assert client.get("/api/tags").get_json()["tags"] == []


def test_smart_folders(client):
    phys = client.post("/api/folders", json={"name": "Physics"}).get_json()
    a = mk(client, title="Spin transport", year=2021, tags=["transport"], folder_id=phys["id"])
    mk(client, title="Spin glass", year=2010)
    assert client.post("/api/smart", json={"name": "x", "query": ""}).status_code == 400
    assert client.post("/api/smart", json={"name": "x", "query": "a", "mode": "nope"}).status_code == 400
    sf = client.post("/api/smart", json={"name": "Recent transport", "query": "tag:transport year:>2020"}).get_json()
    res = client.get(f"/api/items?view=smart:{sf['id']}").get_json()
    assert [i["id"] for i in res["items"]] == [a["id"]]
    assert res["sidebar"]["smart"][0]["name"] == "Recent transport"
    assert client.get("/api/search/preview?q=spin&mode=basic").get_json()["count"] == 2
    assert client.get("/api/search/preview?q=folder:physics").get_json()["count"] == 1
    client.patch(f"/api/smart/{sf['id']}", json={"query": "spin"})
    assert len(client.get(f"/api/items?view=smart:{sf['id']}").get_json()["items"]) == 2
    client.delete(f"/api/smart/{sf['id']}")
    assert client.get("/api/smart").get_json()["smart"] == []


def test_import_keywords_become_tags_and_export_them(client):
    phys = client.post("/api/folders", json={"name": "Physics"}).get_json()
    bib = "@article{k1, title={Paper}, author={Doe, J}, year={2020}, keywords={spin chains, transport}}"
    prev = client.post("/api/import/preview", json={"text": bib}).get_json()
    res = client.post("/api/import/commit", json={"entries": prev["entries"], "folder_id": phys["id"],
                                                  "keywords_as_tags": True}).get_json()
    item = client.get(f"/api/items/{res['created'][0]}").get_json()
    assert item["tags"] == ["spin chains", "transport"] and not item["keywords"] and item["folder_ids"] == [phys["id"]]
    out = client.get(f"/api/export?format=bibtex&ids={item['id']}&download=0").get_json()["text"]
    assert "keywords = {spin chains, transport}" in out


def test_import_tags_setting(client, svc):
    bib = "@article{k1, title={Paper}, author={Doe, J}, year={2020}, keywords={spin chains, transport}}"
    entries = client.post("/api/import/preview", json={"text": bib}).get_json()["entries"]

    def imported(**extra):
        res = client.post("/api/import/commit", json={"entries": entries, "skip_duplicates": False, **extra}).get_json()
        return client.get(f"/api/items/{res['created'][0]}").get_json()

    assert svc.settings.get("import_tags") == "ask"              # the default: the import dialog asks
    no = imported()                                              # not ticked: keywords stay keywords
    assert no["tags"] == [] and no["keywords"] == "spin chains; transport"
    assert imported(keywords_as_tags=True)["tags"] == ["spin chains", "transport"]
    svc.settings.update({"import_tags": "never"})
    assert imported(keywords_as_tags=True)["tags"] == []
    svc.settings.update({"import_tags": "always"})
    assert imported()["tags"] == ["spin chains", "transport"]


def test_old_settings_file_is_migrated(tmp_path):
    import json
    from backend.settings import SettingsStore
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"import_keywords_as_tags": False, "references_auto": False}))
    s = SettingsStore(path)
    assert s.get("import_tags") == "never" and s.get("add_fetch_references") == "never"
    path.write_text(json.dumps({"import_keywords_as_tags": True, "references_auto": True}))
    s = SettingsStore(path)   # the old defaults: the new defaults apply
    assert s.get("import_tags") == "ask" and s.get("add_fetch_references") == "always"
    import pytest
    from backend.settings import SettingsError
    with pytest.raises(SettingsError):
        s.update({"add_find_pdf": "sometimes"})


def test_permanent_delete_cleans_tags(client):
    a = mk(client, tags=["solo"])
    client.post("/api/items/trash", json={"ids": [a["id"]]})
    assert client.get("/api/tags").get_json()["tags"][0]["count"] == 0   # trashed items don't count
    client.post("/api/trash/empty")
    assert client.get("/api/tags").get_json()["tags"] == []


def test_migration_from_single_folder_column(tmp_path):
    """Libraries created with the one-folder-per-item version keep their folders."""
    import sqlite3
    from backend import create_app
    from backend.config import Config
    data = tmp_path / "data"
    app = create_app(Config.from_env(data), start_indexer=False)
    c = app.test_client()
    f = c.post("/api/folders", json={"name": "Old"}).get_json()
    item = c.post("/api/items", json={"title": "X"}).get_json()
    con = sqlite3.connect(data / "library.db")     # simulate the previous layout
    con.execute("DELETE FROM paper_folders")
    con.execute("UPDATE papers SET folder_id = ? WHERE id = ?", (f["id"], item["id"]))
    con.commit()
    con.close()
    app2 = create_app(Config.from_env(data), start_indexer=False)
    assert app2.test_client().get(f"/api/items/{item['id']}").get_json()["folder_ids"] == [f["id"]]
