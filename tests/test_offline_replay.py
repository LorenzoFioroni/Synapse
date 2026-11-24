"""Writes a device queued while Synapse could not be reached (backend/plugins/replay.py; API 1.16),
and how Tasks merges them (plugins/tasks/server/routes.py)."""

import time

import pytest

from backend.plugins.manifest import ManifestError, parse

OFFLINE = {"X-Synapse-Offline": str(int(time.time() * 1000))}
FORCE = {**OFFLINE, "X-Synapse-Offline-Force": "1"}
T = "/api/tasks"


def make(client, **data):
    r = client.post(T, json={"title": "Pack", **data})
    assert r.status_code == 201, r.json
    return r.json["task"]


def test_the_manifest_declares_offline_use(tmp_path):
    folder = tmp_path / "trips"
    folder.mkdir()
    base = {"id": "trips", "name": "Trips", "version": "1", "api": "^1.16"}
    man = parse({**base, "offline": {"keep": ["/", "/list"], "queue": True}}, folder)
    assert man.offline_keep == ["/", "/list"] and man.offline_queue is True
    assert parse(base, folder).offline_queue is False
    for bad in ({"keep": "/"}, {"keep": ["list"]}, {"queue": "yes"}, {"other": 1}):
        with pytest.raises(ManifestError):
            parse({**base, "offline": bad}, folder)


def test_tasks_says_what_it_keeps(client):
    info = next(p for p in client.get("/api/plugin-host").json["plugins"] if p["id"] == "tasks")
    assert info["offline"] == {"keep": ["/"], "queue": True}


def test_a_task_created_offline_keeps_its_id_and_is_sent_once(client):
    r = client.post(T, json={"id": "kpackabc", "title": "Offline"}, headers=OFFLINE)
    assert r.status_code == 201 and r.json["task"]["id"] == "kpackabc"
    again = client.post(T, json={"id": "kpackabc", "title": "Offline"}, headers=OFFLINE)
    assert again.status_code == 200 and again.json["task"]["id"] == "kpackabc"
    assert client.post(T, json={"id": "kpackabc", "title": "x"}).status_code == 400  # not offline: taken
    assert client.post(T, json={"id": "NOT-AN-ID", "title": "x"}).status_code == 400


def test_offline_edits_merge_field_by_field(client):
    t = make(client)
    client.patch(f"{T}/{t['id']}", json={"due": "2026-12-01"})  # here, meanwhile
    r = client.patch(f"{T}/{t['id']}", json={"title": "Pack bags", "base": {"title": "Pack"}}, headers=OFFLINE)
    assert r.status_code == 200
    got = client.get(f"{T}/{t['id']}").json["task"]
    assert got["title"] == "Pack bags" and got["due"] == "2026-12-01"


def test_the_same_field_changed_on_both_sides_asks(client):
    t = make(client)
    client.patch(f"{T}/{t['id']}", json={"title": "Pack the car"})  # here, meanwhile
    r = client.patch(
        f"{T}/{t['id']}",
        json={"title": "Pack bags", "status": "done", "base": {"title": "Pack", "status": "open"}},
        headers=OFFLINE,
    )
    assert r.status_code == 409
    c = r.json["conflict"]
    assert c["retry"] == {"title": "Pack bags"} and c["fields"]["title"]["theirs"] == "Pack the car"
    got = client.get(f"{T}/{t['id']}").json["task"]
    assert got["title"] == "Pack the car" and got["status"] == "done"  # the field without a clash went in
    kept = client.patch(f"{T}/{t['id']}", json=c["retry"], headers=FORCE)
    assert kept.status_code == 200 and kept.json["task"]["title"] == "Pack bags"


def test_the_same_change_on_both_sides_is_no_conflict(client):
    t = make(client)
    client.patch(f"{T}/{t['id']}", json={"status": "done"})
    r = client.patch(f"{T}/{t['id']}", json={"status": "done", "base": {"status": "open"}}, headers=OFFLINE)
    assert r.status_code == 200


def test_without_the_header_base_is_ignored(client):
    t = make(client)
    client.patch(f"{T}/{t['id']}", json={"title": "Elsewhere"})
    r = client.patch(f"{T}/{t['id']}", json={"title": "Mine", "base": {"title": "Pack"}})
    assert r.status_code == 200 and r.json["task"]["title"] == "Mine"


def test_a_deletion_made_offline_keeps_its_batch_so_its_undo_works(client):
    parent = make(client)
    child = make(client, title="Socks", parent_id=parent["id"])
    batch = "20261124120000-a1b2c3"
    r = client.post(f"{T}/delete", json={"ids": [parent["id"]], "batch": batch}, headers=OFFLINE)
    assert r.status_code == 200 and r.json["batch"] == batch and set(r.json["ids"]) == {parent["id"], child["id"]}
    assert client.post(f"{T}/restore", json={"batch": batch}, headers=OFFLINE).json["ids"]
    assert not client.get(f"{T}/{child['id']}").json["task"]["deleted_at"]
    assert client.post(f"{T}/delete", json={"ids": [parent["id"]], "batch": "../x"}).status_code == 400


def test_a_deletion_replayed_for_tasks_already_gone_is_done(client):
    t = make(client)
    client.post(f"{T}/delete", json={"ids": [t["id"]]})  # here, meanwhile
    r = client.post(
        f"{T}/delete", json={"ids": [t["id"], "zzzzzzzz"], "batch": "20261124120000-000000"}, headers=OFFLINE
    )
    assert r.status_code == 200 and r.json["ids"] == []
    assert client.post(f"{T}/delete", json={"ids": ["zzzzzzzz"]}).status_code == 404  # not offline: an error


def test_a_move_made_offline_under_a_task_deleted_meanwhile_leaves_it_in_place(client):
    a, b = make(client, title="A"), make(client, title="B")
    client.post(f"{T}/delete", json={"ids": [b["id"]]})
    r = client.patch(f"{T}/{a['id']}", json={"parent_id": b["id"]}, headers=OFFLINE)
    assert r.status_code == 200 and r.json["task"]["parent_id"] is None
    c = make(client, title="C")
    moved = client.patch(f"{T}/{a['id']}", json={"parent_id": c["id"], "before": "gone1234"}, headers=OFFLINE)
    assert moved.status_code == 200 and moved.json["task"]["parent_id"] == c["id"]


def test_a_task_made_offline_for_a_note_goes_into_it(client, app):
    app.extensions["synapse"].note_service.save("trip.md", "# Trip\n", None)
    r = client.post(T, json={"id": "kntabcde", "title": "Tickets", "note": "trip.md"}, headers=OFFLINE)
    assert r.status_code == 201 and r.json["ref"]["path"] == "trip.md"
    assert "{{task:kntabcde}}" in client.get("/api/notes/note", query_string={"path": "trip.md"}).json["text"]
