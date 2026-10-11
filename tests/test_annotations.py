"""Annotations as records per attachment (issue #20)."""
import json
import re
import shutil
import tarfile
from pathlib import Path

import pytest

from backend import annotations as A
from backend import create_app
from backend.config import Config

ROOT = Path(__file__).resolve().parent
PDF = ROOT / "e2e" / "fixtures" / "twocolumn.pdf"
LEGACY = json.loads((ROOT / "fixtures" / "legacy_annotations.json").read_text())["annotations"]


def attach(client, item_id, pdf=PDF, name="paper.pdf"):
    r = client.post(f"/api/items/{item_id}/attachments", data={"file": (pdf.open("rb"), name)},
                    content_type="multipart/form-data")
    return r.get_json()["attachments"][0]


@pytest.fixture
def att(client):
    item = client.post("/api/items", json={"title": "Annotated"}).get_json()
    return {**attach(client, item["id"]), "item": item}


HIGHLIGHT = {"type": "highlight", "page": 1, "rects": [[0.1, 0.2, 0.3, 0.015], [0.1, 0.215, 0.25, 0.015]],
             "text": "some words", "color": "#ffff00"}


# ============================================================================ API
def test_create_list_update_delete(client, att):
    r = client.post(f"/api/attachments/{att['id']}/annotations", json=HIGHLIGHT)
    assert r.status_code == 201
    ann = r.get_json()["annotation"]
    assert ann["rects"] == HIGHLIGHT["rects"] and ann["text"] == "some words" and ann["attachment_id"] == att["id"]
    listed = client.get(f"/api/attachments/{att['id']}/annotations").get_json()["annotations"]
    assert [a["id"] for a in listed] == [ann["id"]]
    r = client.patch(f"/api/annotations/{ann['id']}", json={"comment": "important", "color": "#00ff00", "type": "box"})
    upd = r.get_json()["annotation"]
    assert upd["comment"] == "important" and upd["color"] == "#00ff00" and upd["type"] == "highlight"
    assert upd["modified"] >= ann["modified"]
    assert client.delete(f"/api/annotations/{ann['id']}").status_code == 200
    assert client.get(f"/api/attachments/{att['id']}/annotations").get_json()["annotations"] == []
    assert client.delete(f"/api/annotations/{ann['id']}").status_code == 404
    assert client.patch(f"/api/annotations/{ann['id']}", json={"comment": "x"}).status_code == 404


@pytest.mark.parametrize("ann", [
    {"type": "underline", "page": 2, "rects": [[0.1, 0.5, 0.4, 0.014]], "color": "#ff0000", "size": 2, "text": "w"},
    {"type": "box", "page": 1, "rects": [[0.1, 0.4, 0.2, 0.1]], "color": "#000000", "size": 2},
    {"type": "ink", "page": 3, "path": [[0.1, 0.1], [0.2, 0.15], [0.3, 0.1]], "color": "#0000ff", "size": 3},
    {"type": "text", "page": 1, "rects": [[0.2, 0.6, 0.1, 0.03]], "text": "Line 1\nLine 2", "color": "#000000", "size": 16},
])
def test_every_type(client, att, ann):
    r = client.post(f"/api/attachments/{att['id']}/annotations", json=ann)
    assert r.status_code == 201, r.get_json()
    got = r.get_json()["annotation"]
    for k, v in ann.items():
        assert got[k] == v, k


def test_client_chosen_id_for_undo(client, att):
    """Undoing a delete re-creates the annotation with its old id."""
    body = {**HIGHLIGHT, "id": "a1b2c3d4e5f6"}
    assert client.post(f"/api/attachments/{att['id']}/annotations", json=body).status_code == 201
    assert client.post(f"/api/attachments/{att['id']}/annotations", json=body).status_code == 400   # exists
    client.delete("/api/annotations/a1b2c3d4e5f6")
    assert client.post(f"/api/attachments/{att['id']}/annotations", json=body).status_code == 201


@pytest.mark.parametrize("bad,msg", [
    ({"type": "scribble", "page": 1, "rects": [[0, 0, 1, 1]]}, "type"),
    ({"type": "box", "page": 0, "rects": [[0, 0, 1, 1]]}, "page"),
    ({"type": "box", "page": 1}, "rects"),
    ({"type": "ink", "page": 1}, "path"),
    ({"type": "box", "page": 1, "rects": [[0, 0, 1]]}, "[x, y, w, h]"),
    ({"type": "box", "page": 1, "rects": [[0, 0, -0.1, 1]]}, "negative"),
    ({"type": "box", "page": 1, "rects": [[0, 0, 900, 1]]}, "fractions"),
    ({"type": "box", "page": 1, "rects": [[0, 0, 1, 1]], "color": "red;x"}, "color"),
    ({"type": "box", "page": 1, "rects": [[0, 0, 1, 1]], "size": 9999}, "size"),
    ({"type": "box", "page": 1, "rects": [[0, 0, 1, 1]], "id": "../../x"}, "id"),
])
def test_validation(client, att, bad, msg):
    r = client.post(f"/api/attachments/{att['id']}/annotations", json=bad)
    assert r.status_code == 400 and msg in r.get_json()["error"]


def test_unknown_attachment(client):
    assert client.get("/api/attachments/999/annotations").status_code == 404
    assert client.post("/api/attachments/999/annotations", json=HIGHLIGHT).status_code == 404


def test_two_pdfs_of_one_item_stay_separate(client, att):
    second = attach(client, att["item"]["id"], name="supplement.pdf")
    client.post(f"/api/attachments/{att['id']}/annotations", json=HIGHLIGHT)
    client.post(f"/api/attachments/{second['id']}/annotations", json={**HIGHLIGHT, "text": "supplement"})
    a = client.get(f"/api/attachments/{att['id']}/annotations").get_json()["annotations"]
    b = client.get(f"/api/attachments/{second['id']}/annotations").get_json()["annotations"]
    assert [x["text"] for x in a] == ["some words"] and [x["text"] for x in b] == ["supplement"]
    both = client.get(f"/api/items/{att['item']['id']}/annotations").get_json()["annotations"]
    assert len(both) == 2


def test_two_tabs_do_not_overwrite_each_other(client, att):
    """Each change is its own request, so two viewers on one PDF both keep their work."""
    url = f"/api/attachments/{att['id']}/annotations"
    a = client.post(url, json=HIGHLIGHT).get_json()["annotation"]
    b = client.post(url, json={**HIGHLIGHT, "page": 2}).get_json()["annotation"]
    client.patch(f"/api/annotations/{a['id']}", json={"comment": "from tab A"})
    client.patch(f"/api/annotations/{b['id']}", json={"comment": "from tab B"})
    got = {x["id"]: x["comment"] for x in client.get(url).get_json()["annotations"]}
    assert got == {a["id"]: "from tab A", b["id"]: "from tab B"}


def test_deleting_the_pdf_or_the_item_removes_its_annotations(client, svc, att):
    client.post(f"/api/attachments/{att['id']}/annotations", json=HIGHLIGHT)
    client.delete(f"/api/attachments/{att['id']}")
    with svc.db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 0
    att2 = attach(client, att["item"]["id"])
    client.post(f"/api/attachments/{att2['id']}/annotations", json=HIGHLIGHT)
    client.post("/api/items/delete", json={"ids": [att["item"]["id"]]})
    with svc.db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 0


def test_annotations_are_in_backups_and_come_back(client, svc, att):
    client.post(f"/api/attachments/{att['id']}/annotations", json=HIGHLIGHT)
    b = svc.backups.create("manual")
    client.post(f"/api/attachments/{att['id']}/annotations", json={**HIGHLIGHT, "page": 2})
    client.post("/api/backups/restore", json={"name": b["name"]})
    assert len(client.get(f"/api/attachments/{att['id']}/annotations").get_json()["annotations"]) == 1


# ============================================================================ legacy markup
def test_parse_real_legacy_page():
    """Markup recorded with the old viewer (tests/fixtures/legacy_annotations.json)."""
    anns, skipped = A.parse_legacy_page(LEGACY["1"], 612, 792)
    assert skipped == []
    kinds = [a["type"] for a in anns]
    assert kinds == ["highlight", "underline", "box", "ink", "text"]
    hl, ul, box, ink, text = anns
    # same geometry: fractions × page size give back the old numbers (PDF points)
    assert hl["color"] == "#ffff00" and len(hl["rects"]) == 2
    assert hl["rects"][0][0] * 612 == pytest.approx(63.947916666666664)
    assert hl["rects"][0][1] * 792 == pytest.approx(114.29166666666667)
    assert hl["rects"][1][2] * 612 == pytest.approx(242.28702799479166)
    assert ul["color"] == "#ff0000" and ul["size"] == 2 and ul["rects"][0][1] * 792 == pytest.approx(198.25)
    assert box["rects"][0][2] * 612 == pytest.approx(133.33333333333331) and box["rects"][0][3] * 792 == pytest.approx(80)
    assert len(ink["path"]) == 13 and ink["path"][0][0] * 612 == pytest.approx(233.33333333333334)
    assert ink["path"][-1][1] * 792 == pytest.approx(400)
    assert text["text"] == "Check this\nsecond line" and text["color"] == "#000000" and text["size"] == 16
    assert text["rects"][0][0] * 612 == pytest.approx(80) and text["rects"][0][1] * 792 == pytest.approx(466.6666666666667)
    anns2, _ = A.parse_legacy_page(LEGACY["2"], 612, 792)
    assert [a["type"] for a in anns2] == ["box"]


def test_parse_odd_markup():
    markup = ('<rect class="selection-box" x="1" y="1" width="2" height="2"></rect>'
              '<circle cx="1" cy="1" r="1"></circle>'
              '<foreignObject x="10" y="10" width="1000" height="1000"><div><div contenteditable="true" '
              'style="color: rgb(255, 0, 0); font-size: 20px">  </div><div></div></div></foreignObject>'
              '<path d=""></path>'
              '<g class="anno-group"><rect x="0" y="0" width="10" height="5" fill="rgb(0, 255, 0)" fill-opacity="0.8"></rect></g>'
              '<foreignObject x="5" y="6"><div><div contenteditable="true" style="color: #00f; font-size: 12px">a<br>b &amp; c</div></div></foreignObject>')
    anns, skipped = A.parse_legacy_page(markup, 100, 200)
    assert [a["type"] for a in anns] == ["highlight", "text"]
    assert anns[0]["color"] == "#00ff00" and anns[1]["text"] == "a\nb & c" and anns[1]["color"] == "#0000ff"
    assert any("circle" in s for s in skipped) and any("without points" in s for s in skipped)


@pytest.mark.parametrize("rot,corners", [
    (0, {(0, 0): (10, 800), (600, 790): (610, 10)}),
    (90, {(0, 0): (10, 10), (790, 600): (610, 800)}),
    (180, {(0, 0): (610, 10), (600, 790): (10, 800)}),
    (270, {(0, 0): (610, 800), (790, 600): (10, 10)}),
])
def test_displayed_point_to_pdf_space(rot, corners):
    crop = (10, 10, 610, 800)   # 600 × 790
    for (u, v), want in corners.items():
        assert A.to_pdf(u, v, rot, crop) == pytest.approx(want)


def test_page_sizes_follow_rotation_and_crop(tmp_path):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject
    w = PdfWriter()
    for p in PdfReader(PDF).pages:
        w.add_page(p)
    w.pages[0].rotate(90)
    w.pages[1].cropbox = RectangleObject([50, 100, 562, 742])
    w.write(tmp_path / "odd.pdf")
    sizes = A.page_sizes(tmp_path / "odd.pdf")
    assert [s[:3] for s in sizes] == [(792, 612, 90), (512, 642, 0), (612, 792, 0)]


# ============================================================================ migration
def legacy_library(tmp_path, blobs: dict, pdf=True):
    """A data folder written by an older version: items with SVG blobs in papers.annotations."""
    data = tmp_path / "data"
    app = create_app(Config.from_env(data), start_indexer=False)
    s = app.extensions["zclone"]
    ids = {}
    for title, blob in blobs.items():
        item = s.papers.create({"title": title})
        if pdf:
            name = f"{item['id']}_paper.pdf"
            shutil.copy(PDF, s.config.pdf_dir / name)
            s.papers.add_attachment(item["id"], "paper.pdf", name, "pdf")
        with s.db.session() as c:
            c.execute("UPDATE papers SET annotations = ? WHERE id = ?", (json.dumps(blob), item["id"]))
            c.execute("DELETE FROM annotation_migration")
        ids[title] = item["id"]
    return data, ids


def test_migration_converts_real_blobs_once_after_a_backup(tmp_path):
    data, ids = legacy_library(tmp_path, {"Legacy": LEGACY})
    app = create_app(Config.from_env(data), start_indexer=False)   # the app starts: conversion runs
    s = app.extensions["zclone"]
    anns = s.annotations.for_item(ids["Legacy"])
    assert sorted((a["page"], a["type"]) for a in anns) == [
        (1, "box"), (1, "highlight"), (1, "ink"), (1, "text"), (1, "underline"), (2, "box")]
    hl = next(a for a in anns if a["type"] == "highlight")
    assert re.search(r"[A-Za-z]{3,}", hl["text"]), hl["text"]   # the words under it, from the PDF
    # a backup was made first, and it still holds the old blob
    [b] = s.backups.list()
    assert b["reason"] == "before converting annotations"
    # the old column is left as it was
    with s.db.session() as c:
        assert json.loads(c.execute("SELECT annotations FROM papers WHERE id = ?", (ids["Legacy"],)).fetchone()[0]) == LEGACY
    report = s.annotations.migration_report()
    assert report[0]["converted"] == 6 and report[0]["skipped"] == []
    # starting again converts nothing twice
    app2 = create_app(Config.from_env(data), start_indexer=False)
    assert len(app2.extensions["zclone"].annotations.for_item(ids["Legacy"])) == 6
    assert len(app2.extensions["zclone"].backups.list()) == 1


def test_migration_reports_what_it_cannot_convert(tmp_path):
    data, ids = legacy_library(tmp_path, {
        "Odd": {"1": '<circle r="3"></circle><rect x="1" y="1" width="5" height="5" stroke="#000000" stroke-width="2" fill="none"></rect>',
                "9": '<rect x="1" y="1" width="5" height="5"></rect>', "2": ""},
    })
    s = create_app(Config.from_env(data), start_indexer=False).extensions["zclone"]
    assert [a["type"] for a in s.annotations.for_item(ids["Odd"])] == ["box"]
    [rep] = s.annotations.migration_report()
    assert rep["converted"] == 1 and rep["title"] == "Odd"
    assert any("circle" in x for x in rep["skipped"]) and any("page 9" in x for x in rep["skipped"])
    client = create_app(Config.from_env(data), start_indexer=False).test_client()
    assert client.get("/api/annotations/migration").get_json()["items"][0]["converted"] == 1


def test_migration_without_a_pdf_keeps_the_blob(tmp_path):
    data, ids = legacy_library(tmp_path, {"No PDF": LEGACY}, pdf=False)
    s = create_app(Config.from_env(data), start_indexer=False).extensions["zclone"]
    [rep] = s.annotations.migration_report()
    assert rep["converted"] == 0 and "no PDF" in rep["skipped"][0]
    with s.db.session() as c:
        assert c.execute("SELECT annotations FROM papers").fetchone()[0]


def test_empty_blobs_need_no_backup(tmp_path):
    data, _ = legacy_library(tmp_path, {"Empty": {"1": "", "2": ""}})
    s = create_app(Config.from_env(data), start_indexer=False).extensions["zclone"]
    assert s.backups.list() == [] and s.annotations.for_item(next(iter(_.values()))) == []


def test_without_pypdfium2_the_conversion_waits(tmp_path, monkeypatch):
    import builtins
    data, ids = legacy_library(tmp_path, {"Legacy": LEGACY})
    real = builtins.__import__

    def no_pdfium(name, *a, **k):
        if name == "pypdfium2":
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_pdfium)
    s = create_app(Config.from_env(data), start_indexer=False).extensions["zclone"]
    assert s.annotations.migration_report() == [] and s.annotations.for_item(ids["Legacy"]) == []
    monkeypatch.setattr(builtins, "__import__", real)
    s = create_app(Config.from_env(data), start_indexer=False).extensions["zclone"]
    assert len(s.annotations.for_item(ids["Legacy"])) == 6
