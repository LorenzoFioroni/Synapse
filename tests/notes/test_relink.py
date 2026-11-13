"""Moving or renaming notes rewrites the links that the move would break (notes/relink.py)."""

from backend.notes.vault import Conflict


def create(client, **kw):
    r = client.post("/api/notes/note", json=kw)
    assert r.status_code == 201, r.json
    return r.json


def text_of(client, path):
    return client.get("/api/notes/note", query_string={"path": path}).json["text"]


def move(client, path, to):
    r = client.post("/api/notes/move", json={"path": path, "to": to})
    assert r.status_code == 200, r.json
    return r.json


def test_path_links_follow_a_move_but_names_stay_as_written(client):
    create(client, title="Target", folder="proj")
    src = create(client, title="Source", text="[[proj/Target]] [[Target]] [[target]] [[proj/target.md]] [[a/../x]]")
    r = move(client, "proj/Target.md", "done/Target.md")
    assert r["updated_links"] == [{"path": src["path"], "count": 2}] and r["broken_links"] == []
    body = text_of(client, src["path"])
    assert "[[done/Target]] [[Target]] [[target]] [[done/Target.md]] [[a/../x]]" in body


def test_alias_heading_and_embeds_are_kept(client):
    create(client, title="Target")
    src = create(client, title="Source", text="[[Target#Part|the part]] ![[Target#Part]] [[Target|x]]")
    r = move(client, "Target.md", "Renamed.md")
    assert r["updated_links"] == [{"path": src["path"], "count": 3}]
    assert "[[Renamed#Part|the part]] ![[Renamed#Part]] [[Renamed|x]]" in text_of(client, src["path"])


def test_a_renamed_note_keeps_the_form_of_each_link(client):
    create(client, title="Old", folder="proj")
    src = create(client, title="Source", text="[[Old]] [[proj/Old]] [[proj/Old.md]]")
    move(client, "proj/Old.md", "proj/New.md")
    assert "[[New]] [[proj/New]] [[proj/New.md]]" in text_of(client, src["path"])


def test_a_name_that_would_now_find_another_note_gains_its_folder(client):
    create(client, title="Twin")  # Twin.md, the shortest path: [[Twin]] means this one
    create(client, title="Twin", folder="proj")
    src = create(client, title="Source", text="[[proj/Twin]] and [[Twin]]")
    other = create(client, title="Other", text="[[Twin]]")
    move(client, "Twin.md", "deep/er/Twin.md")
    # [[Twin]] meant the root note: it now needs a folder, or it would silently mean proj/Twin
    assert "[[proj/Twin]] and [[er/Twin]]" in text_of(client, src["path"])
    assert "[[er/Twin]]" in text_of(client, other["path"])


def test_a_link_to_another_note_with_the_same_name_is_not_touched(client):
    create(client, title="Twin")
    create(client, title="Twin", folder="proj")
    src = create(client, title="Source", text="[[Twin]]")  # means the root note
    r = move(client, "proj/Twin.md", "proj/Gone.md")
    assert r["updated_links"] == [] and "[[Twin]]" in text_of(client, src["path"])


def test_code_math_comments_and_front_matter_are_left_alone(client):
    create(client, title="Target", folder="proj")
    text = "[[proj/Target]]\n\n`[[proj/Target]]`\n\n```\n[[proj/Target]]\n```\n\n$a [[proj/Target]]$\n\n<!-- [[proj/Target]] -->\n"
    src = create(client, title="Source", text=text)
    r = move(client, "proj/Target.md", "Target.md")
    assert r["updated_links"] == [{"path": src["path"], "count": 1}]
    body = text_of(client, src["path"])
    assert body.count("[[Target]]") == 1 and body.count("[[proj/Target]]") == 4


def test_a_note_linking_to_itself_is_updated(client):
    note = create(client, title="Loop", folder="proj", text="[[proj/Loop]]")
    r = move(client, note["path"], "done/Loop.md")
    assert r["updated_links"] == [{"path": "done/Loop.md", "count": 1}]
    assert "[[done/Loop]]" in text_of(client, "done/Loop.md")


def test_folder_move_updates_links_from_outside_and_inside(client):
    create(client, title="A", folder="proj", text="[[proj/B]] [[proj/sub/C]]")
    create(client, title="B", folder="proj")
    create(client, title="C", folder="proj/sub")
    outside = create(client, title="Out", text="[[proj/A]] [[B]] [[sub/C]]")
    r = client.post("/api/notes/folder/move", json={"path": "proj", "to": "projects"}).json
    assert r["broken_links"] == []
    assert {(u["path"], u["count"]) for u in r["updated_links"]} == {
        ("Out.md", 1),
        ("projects/A.md", 2),
    }  # [[B]], [[sub/C]] still resolve
    assert "[[projects/A]] [[B]] [[sub/C]]" in text_of(client, outside["path"])
    assert "[[projects/B]] [[projects/sub/C]]" in text_of(client, "projects/A.md")


def test_a_note_that_cannot_be_written_keeps_its_links_and_is_reported(client, svc, monkeypatch):
    create(client, title="Target", folder="proj")
    src = create(client, title="Source", text="[[proj/Target]]")
    vault = svc.note_service.vault

    def busy(*a, **k):
        raise Conflict(src["path"], "other")

    monkeypatch.setattr(vault, "write", busy)
    r = move(client, "proj/Target.md", "done/Target.md")
    assert r["updated_links"] == []
    assert [(b["path"], b["target"], b["line"] > 0) for b in r["broken_links"]] == [(src["path"], "proj/Target", True)]
    assert "[[proj/Target]]" in text_of(client, src["path"])


def test_rewrites_are_one_history_entry_and_the_index_follows(client, svc):
    create(client, title="Target", folder="proj")
    src = create(client, title="Source", text="[[proj/Target]]")
    move(client, "proj/Target.md", "done/Target.md")
    links = client.get("/api/notes/links", query_string={"path": "done/Target.md"}).json
    assert [b["path"] for b in links["backlinks"]] == [src["path"]]
    # the rewrite is in the entry of the move, so it shows in the history of the note it changed
    entry = svc.note_service.history_of(src["path"])["entries"][0]
    assert entry["kind"] == "rename" and entry["summary"] == "Moved Target to done/Target.md"


def test_backup_failure_leaves_links_and_reports_them(client, svc, monkeypatch):
    create(client, title="Target", folder="proj")
    src = create(client, title="Source", text="[[proj/Target]]")

    def no_disk(self=None, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(svc.note_service.relinker, "_backup", no_disk)
    r = move(client, "proj/Target.md", "done/Target.md")
    assert r["updated_links"] == [] and [b["path"] for b in r["broken_links"]] == [src["path"]]
    assert "[[proj/Target]]" in text_of(client, src["path"])
