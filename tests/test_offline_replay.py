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
