"""The change feed: other windows and devices learn what changed. See backend/changes.py."""

import sqlite3

from backend import changes as ch
from tests.conftest import make_pdf

MAC = {"X-Synapse-Client": "mac"}
IPAD = {"X-Synapse-Client": "ipad"}


def start(client, who):
    r = client.get("/api/changes", headers=who).get_json()
    return r["seq"], r["epoch"]


def poll(client, who, seq, epoch):
    return client.get(f"/api/changes?since={seq}&epoch={epoch}", headers=who).get_json()


def test_first_poll_starts_from_now(client):
    client.post("/api/items", json={"title": "Before"})
    r = client.get("/api/changes", headers=MAC).get_json()
    assert r["changes"] == [] and r["reset"] is False and r["seq"] >= 1


def test_own_edits_are_not_reported_back(client):
    seq, ep = start(client, MAC)
    client.post("/api/items", json={"title": "From the Mac"}, headers=MAC)
    assert poll(client, MAC, seq, ep)["changes"] == []
    assert poll(client, IPAD, seq, ep)["changes"] == ["library"]


def test_seq_moves_on(client):
    seq, ep = start(client, IPAD)
    client.post("/api/items", json={"title": "x"}, headers=MAC)
    r = poll(client, IPAD, seq, ep)
    assert r["changes"] == ["library"]
    assert poll(client, IPAD, r["seq"], ep)["changes"] == []


def test_background_work_is_reported_to_everyone(client, svc):
    seq, ep = start(client, MAC)
    svc.papers.create({"title": "Fetched in the background"})  # no request: e.g. the metadata queue
    assert poll(client, MAC, seq, ep)["changes"] == ["library"]
    assert poll(client, IPAD, seq, ep)["changes"] == ["library"]


def test_annotations_are_their_own_kind(client, svc, tmp_path):
    item = client.post("/api/items", json={"title": "Annotated"}).get_json()
    pdf = make_pdf(tmp_path / "a.pdf", "hello")
    with open(pdf, "rb") as fh:
        att = client.post(
            f"/api/items/{item['id']}/attachments", data={"file": (fh, "a.pdf")}, content_type="multipart/form-data"
        ).get_json()["attachments"][0]
    seq, ep = start(client, MAC)
    r = client.post(
        f"/api/attachments/{att['id']}/annotations",
        headers=IPAD,
        json={"type": "box", "page": 1, "rects": [[0.1, 0.1, 0.2, 0.2]]},
    )
    assert r.status_code in (200, 201), r.get_json()
    assert poll(client, MAC, seq, ep)["changes"] == ["annotations"]
    ann = r.get_json()["annotation"]
    seq2 = poll(client, MAC, seq, ep)["seq"]
    client.delete(f"/api/annotations/{ann['id']}", headers=IPAD)
    assert poll(client, MAC, seq2, ep)["changes"] == ["annotations"]


def test_notes_saved_through_the_api(client):
    seq, ep = start(client, MAC)
    r = client.put("/api/notes/note", json={"path": "Idea.md", "create": True, "text": "# Idea\n"}, headers=IPAD)
    assert r.status_code in (200, 201), r.get_json()
    assert "notes" in poll(client, MAC, seq, ep)["changes"]
    assert "notes" not in poll(client, IPAD, seq, ep)["changes"]


def test_writes_from_outside_the_server_are_noticed(client, svc):
    seq, ep = start(client, MAC)
    with sqlite3.connect(svc.config.db_path) as c:  # e.g. another program on the library
        c.execute("UPDATE papers SET title = title")
        c.execute("CREATE TABLE IF NOT EXISTS outside (x)")
    r = poll(client, MAC, seq, ep)
    assert "library" in r["changes"]
    assert poll(client, MAC, r["seq"], ep)["changes"] == []  # only once


def test_note_index_changed_outside(client, svc):
    seq, ep = start(client, MAC)
    assert svc.notes is not None  # the index exists
    with sqlite3.connect(svc.config.notes_index_path) as c:
        c.execute("CREATE TABLE IF NOT EXISTS outside (x)")
    assert "notes" in poll(client, MAC, seq, ep)["changes"]


def test_restart_means_refresh_everything(client):
    seq, _ = start(client, MAC)
    r = poll(client, MAC, seq, "old-epoch")
    assert r["reset"] is True and set(r["changes"]) == {"library", "annotations", "notes", "tasks", "plugins", "fields"}


def test_far_behind_means_refresh_everything(client, svc, monkeypatch):
    seq, ep = start(client, MAC)
    feed = svc.changes
    for _ in range(ch.KEEP + 5):
        feed.record({"library"}, "ipad")
    r = poll(client, MAC, seq, ep)
    assert r["reset"] is True and "notes" in r["changes"]


def test_kind_of():
    assert ch.kind_of("/api/annotations/abc") == "annotations"
    assert ch.kind_of("/api/attachments/3/annotations") == "annotations"
    assert ch.kind_of("/api/attachments/3") == "library"
    assert ch.kind_of("/api/items") == "library"


def test_a_failing_listener_never_breaks_saving(client, svc):
    svc.db.on_write = lambda: 1 / 0
    r = client.post("/api/items", json={"title": "Still saved"})
    assert r.status_code in (200, 201)
    assert any(p["title"] == "Still saved" for p in svc.papers.list())
