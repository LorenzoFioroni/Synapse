"""PDFs kept in the notes folders: upload, serving, identity across moves, trash and outside changes."""

import io
import os
import sys
from pathlib import Path

import pytest

from backend.notes.vault import TRASH
from tests.conftest import make_pdf

PDF = Path(__file__).resolve().parent / "e2e" / "fixtures" / "twocolumn.pdf"


def upload(client, name="paper.pdf", folder="", data=None, **kw):
    data = PDF.read_bytes() if data is None else data
    return client.post(
        "/api/notes/pdf",
        data={"file": (io.BytesIO(data), name), "folder": folder},
        content_type="multipart/form-data",
        **kw,
    )


def add(client, name="paper.pdf", folder=""):
    r = upload(client, name, folder)
    assert r.status_code == 201, r.json
    return r.json


def listed(client):
    return {p["path"]: p["id"] for p in client.get("/api/notes").json["pdfs"]}


# ------------------------------------------------------------------ upload and serving
def test_upload_lists_and_serves(client, svc):
    p = add(client, "My Paper.pdf", "reading/2026")
    assert p["path"] == "reading/2026/My Paper.pdf" and p["name"] == "My Paper.pdf"
    assert (svc.vault.root / p["path"]).read_bytes() == PDF.read_bytes()
    assert listed(client) == {p["path"]: p["id"]}
    assert "reading/2026" in client.get("/api/notes").json["folders"]
    r = client.get(f"/api/note-pdfs/{p['id']}/file")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data == PDF.read_bytes()
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "immutable" not in r.headers["Cache-Control"] and "no-cache" in r.headers["Cache-Control"]
    assert client.get(f"/api/note-pdfs/{p['id']}/file", headers={"Range": "bytes=0-9"}).status_code == 206
    assert client.get("/api/note-pdfs/999/file").status_code == 404


def test_upload_never_overwrites_and_slugifies(client):
    a = add(client, "x.pdf")
    b = add(client, "x.pdf")
    c = add(client, "../we:ird?.txt")  # the name is cleaned and the type is always .pdf
    assert (a["path"], b["path"]) == ("x.pdf", "x 2.pdf") and a["id"] != b["id"]
    assert c["path"] == "weird.pdf"


def test_upload_must_be_a_pdf(client, svc):
    r = upload(client, "page.pdf", data=b"<html><script>alert(1)</script></html>")
    assert r.status_code == 415 and "not a PDF" in r.json["error"]
    assert not list(svc.vault.root.glob("*.pdf")) and listed(client) == {}
    assert client.post("/api/notes/pdf").status_code == 400


def test_upload_size_cap(client, svc, monkeypatch):
    from backend.notes import service

    monkeypatch.setattr(service, "MAX_PDF_BYTES", 1000)
    r = upload(client, "big.pdf", data=b"%PDF-1.4\n" + b"0" * 5000)
    assert r.status_code == 413 and "larger than" in r.json["error"]
    assert not list(svc.vault.root.rglob("*.pdf"))
    assert not list(svc.vault.root.glob(".*.tmp"))


@pytest.mark.parametrize("folder", ["../out", "/abs", ".hidden", "a/.b", "attachments", "Attachments/sub"])
def test_upload_refuses_bad_folders(client, svc, folder):
    assert upload(client, folder=folder).status_code == 400
    assert not list(svc.vault.root.rglob("*.pdf"))
    assert listed(client) == {}


def test_serving_refuses_what_is_not_a_pdf(client, svc):
    p = add(client)
    (svc.vault.root / p["path"]).write_bytes(b"<html>hi</html>")
    r = client.get(f"/api/note-pdfs/{p['id']}/file")
    assert r.mimetype == "application/octet-stream" and "attachment" in r.headers["Content-Disposition"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_symlinks_are_not_served_or_listed(client, svc, tmp_path):
    p = add(client)
    target = tmp_path / "outside.pdf"
    target.write_bytes(PDF.read_bytes())
    real = svc.vault.root / p["path"]
    real.unlink()
    os.symlink(target, real)
    assert client.get(f"/api/note-pdfs/{p['id']}/file").status_code == 404
    svc.note_pdfs.sync()
    assert listed(client) == {}


def test_hidden_and_reserved_paths_are_not_pdfs(client, svc):
    for rel in (".trash/x.pdf", ".hidden/x.pdf", "attachments/x.pdf"):
        f = svc.vault.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(PDF.read_bytes())
    svc.note_pdfs.sync()
    assert listed(client) == {}
    for bad in ("../x.pdf", "a/../../x.pdf", ".trash/x.pdf", "attachments/x.pdf"):
        r = client.post("/api/notes/move", json={"path": "x.pdf", "to": bad})
        assert r.status_code in (400, 404)


# ------------------------------------------------------------------ identity
def test_rename_and_move_keep_the_id(client, svc):
    p = add(client, "a.pdf")
    r = client.post("/api/notes/move", json={"path": "a.pdf", "to": "papers/b.pdf"})
    assert r.status_code == 200 and r.json["path"] == "papers/b.pdf"
    assert listed(client) == {"papers/b.pdf": p["id"]}
    assert client.get(f"/api/note-pdfs/{p['id']}/file").status_code == 200
    assert client.post("/api/notes/move", json={"path": "papers/b.pdf", "to": "papers/b.md"}).status_code == 400
    add(client, "c.pdf")
    assert client.post("/api/notes/move", json={"path": "papers/b.pdf", "to": "c.pdf"}).status_code == 409


def test_folder_move_keeps_ids(client):
    a, b = add(client, "a.pdf", "f"), add(client, "b.pdf", "f/sub")
    assert client.post("/api/notes/folder/move", json={"path": "f", "to": "g/h"}).status_code == 200
    assert listed(client) == {"g/h/a.pdf": a["id"], "g/h/sub/b.pdf": b["id"]}


def test_trash_and_restore_keep_the_id(client, svc):
    p = add(client, "a.pdf", "f")
    r = client.post("/api/notes/trash", json={"path": "f/a.pdf"})
    assert r.status_code == 200 and r.json["pdfs"] == ["f/a.pdf"]
    tid = r.json["trash_id"]
    assert listed(client) == {}
    assert client.get(f"/api/note-pdfs/{p['id']}/file").status_code == 404
    assert (svc.vault.root / TRASH / tid / "f/a.pdf").is_file()
    assert {"trash_id": tid, "path": "f/a.pdf"}.items() <= client.get("/api/notes/trash").json["trash"][0].items()
    r = client.post("/api/notes/restore", json={"trash_id": tid, "path": "f/a.pdf"})
    assert r.status_code == 200 and listed(client) == {"f/a.pdf": p["id"]}


def test_trash_a_folder_and_restore_it_together(client):
    a = add(client, "a.pdf", "f")
    n = client.post("/api/notes/note", json={"title": "N", "folder": "f"}).json
    r = client.post("/api/notes/trash", json={"folders": ["f"], "paths": []})
    assert r.json["pdfs"] == ["f/a.pdf"] and r.json["paths"] == [n["path"]]
    assert listed(client) == {}
    r = client.post("/api/notes/restore", json={"trash_id": r.json["trash_id"]})
    assert r.json["pdfs"] == ["f/a.pdf"] and r.json["paths"] == [n["path"]]
    assert listed(client) == {"f/a.pdf": a["id"]}


def test_pdfs_never_enter_the_notes_history(client, svc):
    add(client, "a.pdf")
    client.post("/api/notes/move", json={"path": "a.pdf", "to": "b.pdf"})
    client.post("/api/notes/trash", json={"path": "b.pdf"})
    log = client.get("/api/notes/history/log").json
    assert not [e for e in log.get("entries", []) if "pdf" in str(e).lower()]


def test_purging_the_trash_forgets_the_row(client, svc):
    p = add(client, "a.pdf")
    tid = client.post("/api/notes/trash", json={"path": "a.pdf"}).json["trash_id"]
    svc.settings.update({"notes_trash_retention": "1d"})
    svc.attachments.purge(now=10**11)  # far in the future
    assert not (svc.vault.root / TRASH / tid).exists()
    assert svc.note_pdfs.get(p["id"]) is None


# ------------------------------------------------------------------ changes outside the app
def test_external_rename_is_adopted_by_content(client, svc):
    p = add(client, "a.pdf", "f")
    (svc.vault.root / "f" / "a.pdf").rename(svc.vault.root / "renamed.pdf")
    assert svc.note_pdfs.sync() == {"added": 0, "changed": 0, "adopted": 1, "missing": 0, "forgotten": 0}
    assert listed(client) == {"renamed.pdf": p["id"]}


def test_external_replacement_keeps_the_id(client, svc):
    p = add(client, "a.pdf")
    make_pdf(svc.vault.root / "a.pdf", "a different document")
    assert svc.note_pdfs.sync()["changed"] == 1
    row = svc.note_pdfs.get(p["id"])
    assert row["path"] == "a.pdf" and row["state"] == "present" and row["sha256"] != p["sha256"]


def test_external_new_file_and_removal(client, svc):
    p = add(client, "a.pdf")
    make_pdf(svc.vault.root / "new.pdf", "new")
    (svc.vault.root / "a.pdf").unlink()
    assert svc.note_pdfs.sync() == {"added": 1, "changed": 0, "adopted": 0, "missing": 1, "forgotten": 0}
    assert list(listed(client)) == ["new.pdf"]
    assert svc.note_pdfs.get(p["id"])["state"] == "missing"  # never deleted by itself
    assert client.get(f"/api/note-pdfs/{p['id']}/file").status_code == 404
    # the file comes back: the same PDF again
    (svc.vault.root / "back.pdf").write_bytes(PDF.read_bytes())
    svc.note_pdfs.sync()
    assert listed(client)["back.pdf"] == p["id"]


def test_a_new_pdf_may_take_the_place_of_a_missing_one(client, svc):
    p = add(client, "a.pdf")
    (svc.vault.root / "a.pdf").unlink()
    svc.note_pdfs.sync()
    q = add(client, "a.pdf")  # the name is free again
    assert q["path"] == "a.pdf" and q["id"] != p["id"]
    assert svc.note_pdfs.get(p["id"])["state"] == "missing"


def test_registry_is_additive_and_survives_a_second_start(app, client):
    from backend import create_app
    from backend.config import Config

    p = add(client)
    again = create_app(Config.from_env(app.extensions["synapse"].config.data_dir), start_indexer=False)
    assert again.extensions["synapse"].note_pdfs.get(p["id"])["path"] == "paper.pdf"


# ------------------------------------------------------------------ lifetime of rows and highlights


def _backdate(svc, pdf_id, days):
    from datetime import datetime, timedelta, timezone

    when = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with svc.db.session() as c:
        c.execute("UPDATE note_pdfs SET since = ? WHERE id = ?", (when, pdf_id))


def test_trash_deleted_outside_the_app_forgets_the_row(client, svc):
    import shutil

    p = add(client)
    tid = client.post("/api/notes/trash", json={"path": "paper.pdf"}).json["trash_id"]
    shutil.rmtree(svc.vault.root / TRASH / tid)  # the trash emptied by hand
    assert svc.note_pdfs.sync()["forgotten"] == 1 and svc.note_pdfs.get(p["id"]) is None


def test_missing_rows_expire_after_the_retention(client, svc):
    svc.settings.update({"notes_trash_retention": "1w"})
    kept, gone = add(client, "a.pdf"), add(client, "b.pdf")
    for n in ("a.pdf", "b.pdf"):
        (svc.vault.root / n).unlink()
    svc.note_pdfs.sync()
    _backdate(svc, gone["id"], 8)
    _backdate(svc, kept["id"], 2)
    assert svc.note_pdfs.sync()["forgotten"] == 1
    assert svc.note_pdfs.get(gone["id"]) is None and svc.note_pdfs.get(kept["id"])["state"] == "missing"


def test_missing_rows_are_kept_when_the_trash_is_never_emptied(client, svc):
    svc.settings.update({"notes_trash_retention": "never"})
    p = add(client)
    (svc.vault.root / "paper.pdf").unlink()
    svc.note_pdfs.sync()
    _backdate(svc, p["id"], 3000)
    assert svc.note_pdfs.sync()["forgotten"] == 0 and svc.note_pdfs.get(p["id"])


# ------------------------------------------------------------------ highlights
def _highlight(client, pdf_id):
    return client.post(
        f"/api/note-pdfs/{pdf_id}/annotations", json={"type": "highlight", "page": 1, "rects": [[0.1, 0.1, 0.2, 0.02]]}
    )


def test_reappearing_file_within_the_retention_gets_its_highlights_back(client, svc):
    svc.settings.update({"notes_trash_retention": "1w"})
    p = add(client)
    ann = _highlight(client, p["id"]).json["annotation"]
    (svc.vault.root / "paper.pdf").rename(svc.vault.root / "elsewhere.tmp")
    svc.note_pdfs.sync()
    (svc.vault.root / "elsewhere.tmp").rename(svc.vault.root / "other.pdf")
    svc.note_pdfs.sync()
    assert listed(client) == {"other.pdf": p["id"]}
    assert [a["id"] for a in client.get(f"/api/note-pdfs/{p['id']}/annotations").json["annotations"]] == [ann["id"]]


def test_purge_deletes_the_highlights_too(client, svc):
    svc.settings.update({"notes_trash_retention": "1d"})
    p = add(client)
    ann = _highlight(client, p["id"]).json["annotation"]
    client.post("/api/notes/trash", json={"path": "paper.pdf"})
    svc.attachments.purge(now=10**11)
    assert svc.annotations.get(ann["id"]) is None


HIGHLIGHT = {
    "type": "highlight",
    "page": 1,
    "rects": [[0.1, 0.2, 0.3, 0.015]],
    "text": "some words",
    "color": "#ffff00",
}


def test_annotation_records_on_a_notes_pdf(client, svc):
    p = add(client)
    r = client.post(f"/api/note-pdfs/{p['id']}/annotations", json=HIGHLIGHT)
    assert r.status_code == 201
    ann = r.json["annotation"]
    assert ann["pdf_id"] == p["id"] and ann["rects"] == HIGHLIGHT["rects"] and "attachment_id" not in ann
    assert [a["id"] for a in client.get(f"/api/note-pdfs/{p['id']}/annotations").json["annotations"]] == [ann["id"]]
    # the record API finds it in either table
    r = client.patch(f"/api/annotations/{ann['id']}", json={"comment": "key", "type": "box"})
    assert (
        r.status_code == 200
        and r.json["annotation"]["comment"] == "key"
        and r.json["annotation"]["type"] == "highlight"
    )
    assert client.delete(f"/api/annotations/{ann['id']}").status_code == 200
    assert client.delete(f"/api/annotations/{ann['id']}").status_code == 404
    assert client.get(f"/api/note-pdfs/{p['id']}/annotations").json["annotations"] == []


def test_annotations_survive_rename_and_are_separate_from_attachments(client, svc):
    p = add(client, "a.pdf")
    item = client.post("/api/items", json={"title": "Paper"}).json
    att = client.post(
        f"/api/items/{item['id']}/attachments",
        data={"file": (io.BytesIO(PDF.read_bytes()), "p.pdf")},
        content_type="multipart/form-data",
    ).json["attachments"][0]
    mine = client.post(f"/api/note-pdfs/{p['id']}/annotations", json=HIGHLIGHT).json["annotation"]
    theirs = client.post(f"/api/attachments/{att['id']}/annotations", json=HIGHLIGHT).json["annotation"]
    client.post("/api/notes/move", json={"path": "a.pdf", "to": "x/b.pdf"})
    assert [a["id"] for a in client.get(f"/api/note-pdfs/{p['id']}/annotations").json["annotations"]] == [mine["id"]]
    assert [a["id"] for a in client.get(f"/api/attachments/{att['id']}/annotations").json["annotations"]] == [
        theirs["id"]
    ]
    # an annotation id is unique across both tables
    r = client.post(f"/api/note-pdfs/{p['id']}/annotations", json={**HIGHLIGHT, "id": theirs["id"]})
    assert r.status_code == 400


def test_notes_pdf_annotation_errors(client):
    assert client.post("/api/note-pdfs/999/annotations", json=HIGHLIGHT).status_code == 404
    assert client.get("/api/note-pdfs/999/annotations").status_code == 404
    p = add(client)
    assert client.post(f"/api/note-pdfs/{p['id']}/annotations", json={"type": "nope", "page": 1}).status_code == 400
    client.post("/api/notes/trash", json={"path": "paper.pdf"})  # a deleted PDF can be read but not marked
    assert client.post(f"/api/note-pdfs/{p['id']}/annotations", json=HIGHLIGHT).status_code == 404
    assert client.get(f"/api/note-pdfs/{p['id']}/annotations").status_code == 200


def test_annotations_cascade_with_the_row(client, svc):
    p = add(client)
    ann = client.post(f"/api/note-pdfs/{p['id']}/annotations", json=HIGHLIGHT).json["annotation"]
    svc.note_pdfs.forget_under("paper.pdf")
    assert svc.annotations.get(ann["id"]) is None


def test_change_kind_of_note_pdf_annotations():
    from backend.changes import kind_of

    assert kind_of("/api/note-pdfs/3/annotations") == "annotations"
    assert kind_of("/api/annotations/abc") == "annotations"
    assert kind_of("/api/attachments/3/annotations") == "annotations"
    assert kind_of("/api/notes/pdf") == "notes"
    assert kind_of("/api/items/x") == "library"


# ------------------------------------------------------------------ links from notes
def note(client, title, text, folder=""):
    n = client.post("/api/notes/note", json={"title": title, "folder": folder, "text": text}).json
    return n["path"]


def test_parser_finds_pdf_links():
    from backend.notes import parser

    p = parser.parse(
        "See [[Papers/a b.pdf#p=4]], [[a.pdf#page=7|the proof]], [[a.pdf]], ![[a.pdf#p=2]] and `[[code.pdf]]`.\n"
        "```\n[[fenced.pdf]]\n```\nA note [[Other#Heading]] and [[notpdf]].\n"
    )
    pdfs = [(l.target, l.page, l.alias) for l in p.links if l.kind == "pdf"]
    assert pdfs == [("Papers/a b.pdf", 4, ""), ("a.pdf", 7, "the proof"), ("a.pdf", None, ""), ("a.pdf", 2, "")]
    assert [(l.kind, l.target, l.heading) for l in p.links if l.kind != "pdf"] == [
        ("note", "Other", "Heading"),
        ("note", "notpdf", ""),
    ]
    spans = [s for s in parser.link_spans("x [[f/a.pdf#p=3|t]] y") if s["kind"] == "pdf"]
    assert [(s["target"]) for s in spans] == ["f/a.pdf"]


def test_pdf_links_resolve_backlink_and_graph(client, svc):
    add(client, "paper.pdf", "refs")
    n = note(client, "Reader", "Read [[paper.pdf#p=3]] and [[refs/paper.pdf]] and [[missing.pdf]].")
    out = client.get("/api/notes/links", query_string={"path": n}).json
    pdfs = [l for l in out["outgoing"] if l["kind"] == "pdf"]
    assert [(l["resolved"], l["page"]) for l in pdfs] == [("refs/paper.pdf", 3), ("refs/paper.pdf", None), (None, None)]
    assert [u["target"] for u in out["unresolved"]] == ["missing.pdf"]
    back = client.get("/api/notes/links", query_string={"path": "refs/paper.pdf"}).json["backlinks"]
    assert {(b["path"], b["page"]) for b in back} == {(n, 3), (n, None)}
    assert {"source": n, "kind": "pdf", "target": "refs/paper.pdf"} in svc.notes.link_graph()
    assert client.post("/api/notes/trash", json={"path": "refs/paper.pdf"}).status_code == 200
    out = client.get("/api/notes/links", query_string={"path": n}).json
    assert [l["resolved"] for l in out["outgoing"] if l["kind"] == "pdf"] == [None, None, None]


def test_renaming_or_moving_a_pdf_rewrites_links(client, svc):
    add(client, "paper.pdf", "refs")
    n = note(client, "Reader", "By name [[paper.pdf#p=3|see]], by path [[refs/paper.pdf#p=1]].")
    r = client.post("/api/notes/move", json={"path": "refs/paper.pdf", "to": "refs/Renamed.pdf"})
    assert r.status_code == 200 and r.json["updated_links"] == [{"path": n, "count": 2}] and not r.json["broken_links"]
    text = client.get("/api/notes/note", query_string={"path": n}).json["text"]
    assert "[[Renamed.pdf#p=3|see]]" in text and "[[refs/Renamed.pdf#p=1]]" in text
    # a move to another folder keeps a link by name, and rewrites one by path
    r = client.post("/api/notes/move", json={"path": "refs/Renamed.pdf", "to": "other/Renamed.pdf"})
    assert r.json["updated_links"] == [{"path": n, "count": 1}]
    text = client.get("/api/notes/note", query_string={"path": n}).json["text"]
    assert "[[Renamed.pdf#p=3|see]]" in text and "[[other/Renamed.pdf#p=1]]" in text
    # the change is in the notes history under the note, not as a PDF
    assert not [
        e
        for e in client.get("/api/notes/history", query_string={"path": n}).json["entries"]
        if "pdf" in str(e.get("path", ""))
    ]


def test_moving_a_folder_rewrites_links_to_its_pdfs(client):
    add(client, "p.pdf", "f/sub")
    n = note(client, "Reader", "[[f/sub/p.pdf#p=2]]")
    r = client.post("/api/notes/folder/move", json={"path": "f", "to": "g"})
    assert r.status_code == 200 and r.json["updated_links"] == [{"path": n, "count": 1}]
    assert "[[g/sub/p.pdf#p=2]]" in client.get("/api/notes/note", query_string={"path": n}).json["text"]


def test_index_files_follow_the_folder(client, svc):
    add(client, "a.pdf", "x")
    assert svc.notes.resolve_pdf("a.pdf") == ["x/a.pdf"]
    (svc.vault.root / "x" / "a.pdf").rename(svc.vault.root / "b.pdf")
    svc.notes.sync()
    assert svc.notes.resolve_pdf("a.pdf") == [] and svc.notes.resolve_pdf("B.PDF") == ["b.pdf"]


# ------------------------------------------------------------------ text search
def test_notes_search_finds_the_text_of_notes_pdfs_but_the_library_search_does_not(client, svc):
    from tests.conftest import make_pdf

    make_pdf(svc.vault.root / "scalar.pdf", "quasiparticle superdiffusion everywhere")
    svc.note_pdfs.sync()
    svc.indexer.sync(background=False)
    r = client.get("/api/notes/search", query_string={"q": "superdiffusion"}).json["results"]
    hit = [x for x in r if x.get("kind") == "pdf"]
    assert len(hit) == 1 and hit[0]["path"] == "scalar.pdf" and hit[0]["title"] == "scalar.pdf"
    assert "<mark>" in hit[0]["snippet"] and hit[0]["id"] == listed(client)["scalar.pdf"]
    # the library's full-text search never returns it, nor does it count among the attachments
    assert svc.fulltext.match("superdiffusion") == {} and svc.fulltext.stats()["documents"] == 0
    assert svc.fulltext.note_stats()["documents"] == 1
    # an uploaded PDF is indexed on its own (automatic indexing), a replaced one again, a deleted one is dropped
    p = add(client, "paper.pdf")
    svc.indexer.wait()
    make_pdf(svc.vault.root / "paper.pdf", "exotic hydrodynamics")
    svc.note_pdfs.sync()
    svc.indexer.sync(background=False)
    found = lambda q: [x["path"] for x in client.get("/api/notes/search", query_string={"q": q}).json["results"]]  # noqa: E731
    assert "paper.pdf" in found("hydrodynamics")
    client.post("/api/notes/trash", json={"path": "paper.pdf"})
    assert "paper.pdf" not in found("hydrodynamics")  # not listed any more, even before the index catches up
    svc.indexer.sync(background=False)
    assert p["id"] not in svc.fulltext.note_indexed_ids()


def test_library_search_by_text_never_lists_a_notes_pdf(client, svc):
    from tests.conftest import make_pdf

    make_pdf(svc.vault.root / "n.pdf", "needleword inside a notes pdf")
    item = client.post("/api/items", json={"title": "Item"}).json
    pdf = make_pdf(svc.config.pdf_dir / "lib.pdf", "needleword inside a library pdf")
    svc.papers.add_attachment(item["id"], "lib.pdf", pdf.name, "pdf")
    svc.note_pdfs.sync()
    svc.indexer.sync(background=False)
    assert set(svc.fulltext.match("needleword")) == {item["id"]}  # paper ids only
    assert [r["path"] for r in svc.note_service.search("needleword") if r.get("kind") == "pdf"] == ["n.pdf"]
    ids = [x["id"] for x in client.get("/api/items", query_string={"q": "needleword"}).json.get("items", [])]
    assert ids in ([], [item["id"]])
