import io

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def create(client, **kw):
    r = client.post("/api/notes/note", json=kw)
    assert r.status_code == 201, r.json
    return r.json


def test_crud_cycle(client, svc):
    n = create(client, title="My idea", folder="ideas")
    assert n["path"] == "ideas/My idea.md"
    assert n["text"].startswith("---\nid: ")
    assert "type: note" in n["text"] and "# My idea" in n["text"]
    # second note with the same title gets a new name, not an overwrite
    assert create(client, title="My idea", folder="ideas")["path"] == "ideas/My idea 2.md"

    got = client.get("/api/notes/note", query_string={"path": n["path"]}).json
    assert got["hash"] == n["hash"] and got["info"]["title"] == "My idea"

    r = client.put("/api/notes/note", json={"path": n["path"], "text": got["text"] + "See [[Other]].", "hash": got["hash"]})
    assert r.status_code == 200
    new_hash = r.json["hash"]

    # stale save is refused with the current hash
    r = client.put("/api/notes/note", json={"path": n["path"], "text": "lost?", "hash": got["hash"]})
    assert r.status_code == 409 and r.json["conflict"] and r.json["current_hash"] == new_hash
    assert "See [[Other]]." in client.get("/api/notes/note", query_string={"path": n["path"]}).json["text"]

    overview = client.get("/api/notes").json
    assert {x["path"] for x in overview["notes"]} == {"ideas/My idea.md", "ideas/My idea 2.md"}
    assert "ideas" in overview["folders"]

    r = client.post("/api/notes/move", json={"path": n["path"], "to": "done/Idea.md"})
    assert r.json["path"] == "done/Idea.md"
    r = client.post("/api/notes/trash", json={"path": "done/Idea.md"})
    tid = r.json["trash_id"]
    assert client.get("/api/notes/note", query_string={"path": "done/Idea.md"}).status_code == 404
    assert client.get("/api/notes/trash").json["trash"][0]["path"] == "done/Idea.md"
    assert client.post("/api/notes/restore", json={"trash_id": tid, "path": "done/Idea.md"}).status_code == 200
    assert client.get("/api/notes/note", query_string={"path": "done/Idea.md"}).status_code == 200


def test_bad_paths_are_400(client):
    for p in ["../x.md", "/abs.md", ".trash/x.md", "x.txt"]:
        assert client.get("/api/notes/note", query_string={"path": p}).status_code == 400
        assert client.post("/api/notes/note", json={"path": p}).status_code == 400
    assert client.get("/api/notes/attachments/../library.db").status_code in (400, 404)
    assert client.get("/api/notes/attachments/%2E%2E/library.db").status_code in (400, 404)


def test_paper_note_links_and_backlinks(client, svc):
    item = svc.papers.create({"authors": "Smith, Anna", "title": "Quantum things", "year": 2021})
    key = item["citation_key"]
    pn = create(client, item_id=item["id"])
    assert pn["path"] == f"papers/{key}.md" and f"paper: {key}" in pn["text"]
    r = client.post("/api/notes/note", json={"item_id": item["id"]})
    assert r.status_code == 409 and r.json["path"] == pn["path"]

    review = create(client, title="Review", text=f"Builds on [@{key}, p. 7] and [@ghost2000]. See [[{key}]].")
    links = client.get("/api/notes/links", query_string={"path": review["path"]}).json
    cited = {c["key"]: c for c in links["cited"]}
    assert cited[key]["item"]["id"] == item["id"]
    assert cited["ghost2000"]["item"] is None
    assert [u["target"] for u in links["unresolved"]] == ["ghost2000"]
    back = client.get("/api/notes/links", query_string={"path": pn["path"]}).json["backlinks"]
    assert [b["path"] for b in back] == [review["path"]]

    notes = client.get(f"/api/items/{item['id']}/notes").json
    assert notes["key"] == key
    assert [n["path"] for n in notes["paper_notes"]] == [pn["path"]]
    assert {(c["path"], c["page"]) for c in notes["citing"]} == {(review["path"], 7), (pn["path"], None)}

    # trashed items still resolve, flagged as trashed
    svc.papers.trash([item["id"]])
    links = client.get("/api/notes/links", query_string={"path": review["path"]}).json
    assert {c["key"]: c for c in links["cited"]}[key]["item"]["trashed"] is True

    idx = {r["key"]: r for r in client.get("/api/notes/cite_index").json["items"]}
    assert idx[key]["trashed"] is True   # autocomplete greys out trashed items
    svc.papers.restore([item["id"]])
    idx = {r["key"]: r for r in client.get("/api/notes/cite_index").json["items"]}
    assert idx[key]["trashed"] is False


def test_move_reports_broken_links(client):
    create(client, title="Target")
    src = create(client, title="Source", text="see [[Target]]")
    r = client.post("/api/notes/move", json={"path": "Target.md", "to": "sub/Target.md"})
    assert r.json["broken_links"] == []          # name unchanged: link still works
    r = client.post("/api/notes/move", json={"path": "sub/Target.md", "to": "sub/Renamed.md"})
    assert [b["path"] for b in r.json["broken_links"]] == [src["path"]]


def test_image_upload(client, svc):
    r = client.post("/api/notes/attachments", data={"file": (io.BytesIO(PNG), "My Plot.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 201
    rel = r.json["path"]
    assert rel.startswith("attachments/") and rel.endswith("-My-Plot.png")
    assert r.json["markdown"] == f"![]({rel})"
    got = client.get(f"/api/notes/{rel}")
    assert got.status_code == 200 and got.data == PNG
    for data, name in [(b"<svg onload=alert(1)>", "x.svg"), (b"not an image", "x.png"), (b"%PDF-1.4", "x.png")]:
        r = client.post("/api/notes/attachments", data={"file": (io.BytesIO(data), name)},
                        content_type="multipart/form-data")
        assert r.status_code == 400


def test_search_and_rescan(client, svc):
    create(client, title="Entanglement", text="area laws")
    (svc.vault.root / "outside.md").write_text("written in another editor: entanglement")
    assert client.post("/api/notes/rescan").json["added"] == 1
    paths = {r["path"] for r in client.get("/api/notes/search", query_string={"q": "entanglement"}).json["results"]}
    assert paths == {"Entanglement.md", "outside.md"}


def test_library_db_is_untouched(app, svc, client):
    import sqlite3
    def tables():
        c = sqlite3.connect(svc.config.db_path)
        t = sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        c.close()
        return t
    before = tables()
    create(client, title="x", text="[@a]")
    assert tables() == before
    assert not any("note" in t for t in before)


def test_save_with_create_flag(client):
    r = client.put("/api/notes/note", json={"path": "gone.md", "text": "exact text", "create": True})
    assert r.status_code == 200
    assert client.get("/api/notes/note", query_string={"path": "gone.md"}).json["text"] == "exact text"
    r = client.put("/api/notes/note", json={"path": "gone.md", "text": "again", "create": True})
    assert r.status_code == 409 and r.json["exists"]
    assert client.put("/api/notes/note", json={"path": "gone.md", "text": "x"}).status_code == 400


def test_cite_index(client, svc, tmp_path):
    from tests.conftest import make_pdf
    a = svc.papers.create({"authors": "Smith, Anna", "title": "Quantum things", "year": 2021, "journal": "PRL"})
    b = svc.papers.create({"authors": "Doe, J", "title": "Gone", "year": 1999})
    svc.papers.create({"title": ""}, auto_citekey=False)          # no key: not citable
    pdf = make_pdf(svc.config.pdf_dir / "x.pdf", "hello")
    svc.papers.add_attachment(a["id"], "paper.pdf", pdf.name, "pdf")
    svc.papers.trash([b["id"]])
    create(client, item_id=a["id"])
    items = {i["key"]: i for i in client.get("/api/notes/cite_index").json["items"]}
    assert set(items) == {a["citation_key"], b["citation_key"]}
    ia = items[a["citation_key"]]
    assert ia["journal"] == "PRL" and ia["pdf"]["url"] == "x.pdf" and ia["note"] == f"papers/{a['citation_key']}.md"
    assert ia["trashed"] is False and items[b["citation_key"]]["trashed"] is True and items[b["citation_key"]]["pdf"] is None


def test_append_to_paper_note(client, svc):
    item = svc.papers.create({"authors": "Smith, Anna", "title": "Quantum things", "year": 2021})
    key = item["citation_key"]
    r = client.post("/api/notes/append", json={"item_id": item["id"], "markdown": f"> First quote [@{key}, p. 2]"})
    assert r.status_code == 200 and r.json["created"] is True
    path = r.json["path"]
    r = client.post("/api/notes/append", json={"item_id": item["id"], "markdown": f"> Second [@{key}, p. 5]\n"})
    assert r.json["created"] is False and r.json["path"] == path
    text = svc.vault.read(path)[0]
    assert text.endswith(f"\n\n> First quote [@{key}, p. 2]\n\n> Second [@{key}, p. 5]\n")
    assert {c["page"] for c in svc.notes.citing(key)} >= {2, 5}
    assert client.post("/api/notes/append", json={"item_id": item["id"], "markdown": "  "}).status_code == 400
    nokey = svc.papers.create({"title": ""}, auto_citekey=False)
    assert client.post("/api/notes/append", json={"item_id": nokey["id"], "markdown": "x"}).status_code == 400


def test_delete_and_rename_folders(client):
    create(client, title="Target", folder="proj")
    create(client, title="Plan", folder="proj/sub")
    src = create(client, title="Source", text="see [[proj/Target]] and [[Plan]]")
    r = client.post("/api/notes/folder/move", json={"path": "proj", "to": "projects"})
    assert r.status_code == 200 and r.json["path"] == "projects"
    assert {m["to"] for m in r.json["moved"]} == {"projects/Target.md", "projects/sub/Plan.md"}
    assert [(b["path"], b["target"]) for b in r.json["broken_links"]] == [(src["path"], "proj/Target")]
    paths = {n["path"] for n in client.get("/api/notes").json["notes"]}
    assert paths == {"projects/Target.md", "projects/sub/Plan.md", "Source.md"}

    r = client.post("/api/notes/trash", json={"folders": ["projects"], "paths": ["Source.md"]})
    assert r.status_code == 200
    assert r.json["paths"] == ["Source.md", "projects/Target.md", "projects/sub/Plan.md"]
    ov = client.get("/api/notes").json
    assert ov["notes"] == [] and "projects" not in ov["folders"]

    r = client.post("/api/notes/restore", json={"trash_id": r.json["trash_id"]})   # one undo for all
    assert sorted(r.json["paths"]) == ["Source.md", "projects/Target.md", "projects/sub/Plan.md"]
    assert len(client.get("/api/notes").json["notes"]) == 3
    assert client.get("/api/notes/links", query_string={"path": "projects/sub/Plan.md"}).json["backlinks"]

    assert client.post("/api/notes/folder/move", json={"path": "attachments", "to": "x"}).status_code == 400
    assert client.post("/api/notes/folder/move", json={"path": "nope", "to": "x"}).status_code == 404


def test_key_summary(client, svc):
    item = svc.papers.create({"title": "Quantum things", "authors": "Smith, A", "year": 2021})
    key = svc.papers.get(item["id"])["citation_key"]
    pn = client.post("/api/notes/note", json={"item_id": item["id"]}).json      # cites itself: not counted
    create(client, title="A", text=f"[@{key}] and again [@{key}]")
    create(client, title="B", text=f"[@{key}; @other2020]")
    keys = client.get("/api/notes/keys").json["keys"]
    assert keys[key] == {"paper": pn["path"], "citing": 2}
    assert keys["other2020"] == {"paper": None, "citing": 1}
