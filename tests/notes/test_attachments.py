"""Attachments no note uses go to their own trash; both notes trashes are emptied after a while."""

import io
import os
import time
from datetime import datetime, timedelta

from backend.notes.attachments import ATTACHMENT_TRASH, STAMP, attachment_name

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
OLD = time.time() - 2 * 3600  # older than the grace period


def upload(client, name="fig.png"):
    r = client.post(
        "/api/notes/attachments", data={"file": (io.BytesIO(PNG), name)}, content_type="multipart/form-data"
    )
    assert r.status_code == 201, r.json
    return r.json["path"]  # attachments/<stamp>-fig.png


def note(client, title, text):
    n = client.post("/api/notes/note", json={"title": title}).json
    cur = client.get("/api/notes/note", query_string={"path": n["path"]}).json
    r = client.put("/api/notes/note", json={"path": n["path"], "text": cur["text"] + text, "hash": cur["hash"]})
    assert r.status_code == 200, r.json
    return n["path"]


def age(svc, path, when=OLD):
    os.utime(svc.vault.root / path, (when, when))


def test_attachment_name():
    assert attachment_name("attachments/a.png") == "a.png"
    assert attachment_name("./attachments/sub/a.png") == "sub/a.png"
    assert attachment_name("a.png") == "a.png"  # resolved under attachments/ by the editor
    assert attachment_name("../attachments/a.png?x=1#y") == "a.png"
    assert attachment_name("https://example.org/a.png") is None
    assert attachment_name("data:image/png;base64,xx") is None
    assert attachment_name("attachments/../../etc/passwd") is None


def test_sweep_moves_unused_and_brings_them_back(client, svc):
    used, unused, fresh, in_trashed = (
        upload(client, "used.png"),
        upload(client, "unused.png"),
        upload(client, "fresh.png"),
        upload(client, "t.png"),
    )
    for p in (used, unused, in_trashed):
        age(svc, p)
    note(client, "Keeps", f"\n![]({used})\n")
    gone = note(client, "Gone", f"\n![x](./{in_trashed})\n")
    client.post("/api/notes/trash", json={"path": gone})

    r = svc.attachments.sweep()
    name = lambda p: p.split("/", 1)[1]  # noqa: E731
    assert r["trashed"] == [name(unused)]  # not the fresh one (grace), nor the used ones
    assert (svc.vault.root / used).exists() and (svc.vault.root / fresh).exists()
    assert (svc.vault.root / in_trashed).exists()  # a note in the notes trash still uses it
    assert not (svc.vault.root / unused).exists()

    ov = client.get("/api/notes/attachments").json
    by = {a["name"]: a for a in ov["attachments"]}
    assert by[name(used)]["used_by"] == ["Keeps.md"]
    assert by[name(in_trashed)]["used_by"][0].startswith(".trash/")
    assert by[name(fresh)]["used_by"] == []
    assert [t["name"] for t in ov["trash"]] == [name(unused)] and ov["trash"][0]["deletes_at"]

    # the trashed file can be previewed
    t = ov["trash"][0]
    assert client.get(f"/api/notes/attachments/trash/{t['trash_id']}/{t['name']}").data == PNG

    # used again (an undo, a pasted link): it comes back by itself
    note(client, "Again", f"\n![]({unused})\n")
    assert svc.attachments.sweep()["restored"] == [name(unused)]
    assert (svc.vault.root / unused).exists() and client.get("/api/notes/attachments").json["trash"] == []


def test_restore_and_delete_from_the_trash(client, svc):
    a, b = upload(client, "a.png"), upload(client, "b.png")
    age(svc, a)
    age(svc, b)
    svc.attachments.sweep()
    trash = {t["name"]: t for t in client.get("/api/notes/attachments").json["trash"]}
    ta, tb = trash[a.split("/", 1)[1]], trash[b.split("/", 1)[1]]
    r = client.post("/api/notes/attachments/restore", json=ta)
    assert r.status_code == 200 and r.json["path"] == a and (svc.vault.root / a).exists()
    assert svc.attachments.sweep()["trashed"] == []  # just restored: not swept again right away
    assert client.post("/api/notes/attachments/delete", json=tb).json == {"ok": True}
    assert client.get("/api/notes/attachments").json["trash"] == []
    assert client.post("/api/notes/attachments/delete", json=tb).status_code == 404
    assert client.post("/api/notes/attachments/restore", json={"trash_id": "../x", "name": "a.png"}).status_code == 400
    assert client.post(
        "/api/notes/attachments/delete", json={"trash_id": ta["trash_id"], "name": "../../notes.db"}
    ).status_code in (400, 404)


def test_purge_after_the_retention(client, svc):
    # a deleted note and a trashed attachment from 10 days ago, and recent ones
    old_stamp = (datetime.now() - timedelta(days=10)).strftime(STAMP)
    new_stamp = datetime.now().strftime(STAMP)
    for stamp in (old_stamp, new_stamp):
        d = svc.vault.root / ".trash" / stamp
        d.mkdir(parents=True)
        (d / f"n{stamp[-3:]}.md").write_text("# deleted\n")
        a = svc.vault.root / ATTACHMENT_TRASH / stamp
        a.mkdir(parents=True)
        (a / "x.png").write_bytes(PNG)

    # right after updating, nothing old is deleted: the clock starts when the feature first runs
    svc.settings.update({"notes_trash_retention": "1d"})
    assert svc.attachments.purge() == {"attachments": 0, "notes": 0}
    (svc.vault.root / ATTACHMENT_TRASH / ".since").write_text(str(time.time() - 30 * 86400))  # as if long ago

    svc.settings.update({"notes_trash_retention": "never"})
    assert svc.attachments.purge() == {"attachments": 0, "notes": 0}
    svc.settings.update({"notes_trash_retention": "2w"})
    assert svc.attachments.purge() == {"attachments": 0, "notes": 0}
    svc.settings.update({"notes_trash_retention": "1w"})
    assert svc.attachments.purge() == {"attachments": 1, "notes": 1}
    assert not (svc.vault.root / ".trash" / old_stamp).exists() and (svc.vault.root / ".trash" / new_stamp).exists()
    assert not (svc.vault.root / ATTACHMENT_TRASH / old_stamp).exists()

    listed = client.get("/api/notes/trash").json
    assert listed["retention"] == "1w" and len(listed["trash"]) == 1
    t = listed["trash"][0]
    assert datetime.fromisoformat(t["deletes_at"]) - datetime.fromisoformat(t["trashed_at"]) == timedelta(days=7)

    r = client.post("/api/notes/attachments/clean").json
    assert set(r) == {"trashed", "restored", "purged"}


def test_retention_setting_is_validated(client):
    assert client.put("/api/settings", json={"notes_trash_retention": "3d"}).status_code == 400
    assert client.put("/api/settings", json={"notes_trash_retention": "6m"}).status_code == 200
