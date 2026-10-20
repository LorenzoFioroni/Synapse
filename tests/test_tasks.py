"""Tasks: the store in library.db, the {{task:id}} lines in notes, converting checkbox lists, the API."""

import pytest

from backend.notes.vault import NoteMissing
from backend.tasks.service import insert_ref, ref_ids, remove_refs, token
from backend.tasks.store import TaskError, TaskMissing


@pytest.fixture
def store(svc):
    return svc.task_store


# ------------------------------------------------------------------ store
def test_create_and_update(store):
    t = store.create("  Write\nthe   intro ", due="2026-10-27")
    assert t["title"] == "Write the intro"  # one line
    assert t["due"] == "2026-10-27"
    assert t["status"] == "open" and t["done_at"] is None and len(t["id"]) == 8
    u = store.update(t["id"], {"status": "done", "body": "Some *details*\r\nwith $x$"})
    assert u["status"] == "done" and u["done_at"] and u["body"] == "Some *details*\nwith $x$"
    u = store.update(t["id"], {"status": "open", "due": None})
    assert u["done_at"] is None and u["due"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "  "},
        {"due": "next friday"},
        {"due": "2026-02-30"},
        {"priority": "high"},
        {"status": "doing"},
        {"tags": ["ising"]},
        {"colour": "red"},
    ],
)
def test_bad_values_are_refused(store, changes):
    t = store.create("x")
    with pytest.raises(TaskError):
        store.update(t["id"], changes)
    assert store.get(t["id"])["title"] == "x"


def test_subtasks_order_and_moves(store):
    a = store.create("A")
    b = store.create("B", parent_id=a["id"])
    c = store.create("C", parent_id=a["id"])
    store.create("D", parent_id=a["id"], before=c["id"])
    assert [t["title"] for t in store.children(a["id"])] == ["B", "D", "C"]
    store.move(c["id"], parent_id=a["id"], before=b["id"])
    assert [t["title"] for t in store.children(a["id"])] == ["C", "B", "D"]
    e = store.create("E", parent_id=b["id"])
    with pytest.raises(TaskError):
        store.move(a["id"], parent_id=e["id"])  # never under its own sub-task
    with pytest.raises(TaskError):
        store.move(a["id"], parent_id=a["id"])
    store.move(e["id"], parent_id=None)
    assert store.get(e["id"])["parent_id"] is None
    # many inserts at the same spot keep a strict order (positions are renumbered when needed)
    first = store.children(a["id"])[0]["id"]
    for i in range(80):
        store.create(f"n{i}", parent_id=a["id"], before=first)
    titles = [t["title"] for t in store.children(a["id"])]
    assert titles[:3] == ["n0", "n1", "n2"] and titles[79] == "n79" and titles[80] == "C"


def test_done_with_subtasks(store):
    a = store.create("A")
    b = store.create("B", parent_id=a["id"])
    c = store.create("C", parent_id=b["id"])
    changed = store.set_status(a["id"], "done", with_subtasks=True)
    assert {t["id"] for t in changed} == {a["id"], b["id"], c["id"]}
    assert store.set_status(a["id"], "open") and store.get(c["id"])["status"] == "done"


def test_trash_and_restore(store):
    a = store.create("A")
    b = store.create("B", parent_id=a["id"])
    other = store.create("Other")
    out = store.delete([a["id"]])
    assert set(out["ids"]) == {a["id"], b["id"]}
    assert {t["id"] for t in store.list()} == {other["id"]}
    assert {t["id"] for t in store.list(trashed=True)} == {a["id"], b["id"]}
    with pytest.raises(TaskMissing):
        store.update(a["id"], {"title": "x"})
    with pytest.raises(TaskMissing):
        store.create("child of trashed", parent_id=a["id"])
    assert set(store.restore(batch=out["batch"])) == {a["id"], b["id"]}
    assert store.get(b["id"])["parent_id"] == a["id"] and not store.get(b["id"])["deleted_at"]


def test_restoring_a_subtask_whose_parent_is_gone(store):
    a = store.create("A")
    b = store.create("B", parent_id=a["id"])
    first = store.delete([b["id"]])
    store.delete([a["id"]])
    store.purge("9999-01-01T00:00:00Z")  # everything in the trash, for good
    with pytest.raises(TaskMissing):
        store.get(b["id"])
    c = store.create("C")
    d = store.create("D", parent_id=c["id"])
    store.delete([d["id"]])
    store.delete([c["id"]])
    store.restore(task_id=d["id"])
    assert store.get(d["id"])["parent_id"] is None  # its parent is still in the trash
    assert first["batch"]


def test_purge_keeps_recent(store):
    a = store.create("A")
    store.delete([a["id"]])
    assert store.purge("2000-01-01T00:00:00Z") == 0
    assert store.get(a["id"])["deleted_at"]


def test_history_and_undo(store):
    t = store.create("A", by="ai")
    u = store.update(t["id"], {"title": "B", "due": "2026-11-01"}, by="ai")
    h = store.history(t["id"])
    assert [x["action"] for x in h] == ["update", "create"] and h[0]["by"] == "ai"
    assert h[0]["before"]["title"] == "A" and h[0]["after"]["title"] == "B"
    back = store.undo(h[0]["id"])
    assert back["title"] == "A" and back["due"] is None and u["title"] == "B"
    with pytest.raises(TaskError):
        store.undo(h[0]["id"])  # once only
    store.update(t["id"], {"title": "C"})
    store.update(t["id"], {"title": "D"})
    older = store.history(t["id"])[1]  # C, overwritten since by D
    with pytest.raises(TaskError):
        store.undo(older["id"])
    assert store.get(t["id"])["title"] == "D"
    store.undo(store.history(t["id"])[-1]["id"])  # undoing the creation trashes it
    assert store.get(t["id"])["deleted_at"]


def test_schema_comes_back_after_migrate(svc):
    with svc.db.session() as c:
        c.execute("DROP TABLE tasks")
    svc.db.migrate()  # what restoring an older backup runs
    assert svc.task_store.create("x")["title"] == "x"


# ------------------------------------------------------------------ lines in notes
def test_insert_ref_places():
    text, line = insert_ref("# N\n\nSome text\n", "aaaa1111")
    assert text == "# N\n\nSome text\n\n{{task:aaaa1111}}\n" and line == 5
    text, line = insert_ref(text, "bbbb2222")
    assert text.endswith("{{task:aaaa1111}}\n{{task:bbbb2222}}\n") and line == 6
    listed = "## TODOs\n- {{task:aaaa1111}}\n\nMore text.\n"
    text, line = insert_ref(listed, "cccc3333")
    assert text == "## TODOs\n- {{task:aaaa1111}}\n- {{task:cccc3333}}\n\nMore text.\n" and line == 3
    text, line = insert_ref("a\r\nb\r\n", "dddd4444", after_line=1)
    assert text == "a\r\n{{task:dddd4444}}\r\nb\r\n" and line == 2
    text, _ = insert_ref("", "eeee5555")
    assert text == "{{task:eeee5555}}\n"


def test_remove_refs():
    text = "# N\n- {{task:aaaa1111}}\nSee {{task:aaaa1111}} today.\n`{{task:aaaa1111}}`\n{{task:bbbb2222}}\n"
    out, n = remove_refs(text, "aaaa1111")
    assert n == 2 and out == "# N\nSee today.\n`{{task:aaaa1111}}`\n{{task:bbbb2222}}\n"
    assert ref_ids(text) == ["aaaa1111", "bbbb2222"]


def test_add_and_remove_in_a_note(svc, store):
    svc.note_service.create(path="Notes/a.md", text="# A\n\n## TODOs\n")
    t = store.create("Plot chi")
    ref = svc.tasks.add_to_note(t["id"], "Notes/a.md")
    got = svc.note_service.get("Notes/a.md")
    assert got["text"].splitlines()[ref["line"] - 1] == token(t["id"])
    refs = svc.tasks.refs()[t["id"]]
    assert refs == [{"path": "Notes/a.md", "title": "A", "line": ref["line"], "heading": "TODOs"}]
    svc.tasks.add_to_note(t["id"], "Notes/a.md", by="ai")  # Claude's change goes to the history
    assert svc.note_service.history_of("Notes/a.md")["entries"]
    assert svc.tasks.remove_from_note(t["id"], "Notes/a.md")["removed"] == 2
    assert t["id"] not in svc.tasks.refs()


def test_create_in_a_note_that_does_not_exist_leaves_no_task(svc, store):
    with pytest.raises(NoteMissing):
        svc.tasks.create({"title": "x", "note": "Notes/missing.md"})
    assert store.list() == []


# ------------------------------------------------------------------ citation keys
def test_a_new_citation_key_is_written_into_tasks(svc, client):
    item = svc.papers.create({"authors": "Smith, Anna", "title": "Quantum things", "year": 2021})
    other = svc.papers.create({"authors": "Smith, Anna", "title": "Quantum things b", "year": 2021})
    key = item["citation_key"]
    t = svc.task_store.create(
        f"Read [@{key}, p. 3]", body=f"See [see @{key}; @{other['citation_key']}].\n`[@{key}]` and $[@{key}]$"
    )
    gone = svc.task_store.create(f"Old [@{key}]")
    svc.task_store.delete([gone["id"]])
    untouched = svc.task_store.create("Nothing to cite")
    client.patch(f"/api/items/{item['id']}", json={"citation_key": "smith2021"})
    got = svc.task_store.get(t["id"])
    assert got["title"] == "Read [@smith2021, p. 3]"
    assert (
        got["body"] == f"See [see @smith2021; @{other['citation_key']}].\n`[@{key}]` and $[@{key}]$"
    )  # not in code or math
    assert svc.task_store.get(gone["id"])["title"] == "Old [@smith2021]"  # trashed tasks too
    assert svc.task_store.get(untouched["id"])["updated_at"] == untouched["updated_at"]
    assert svc.task_store.history(t["id"])[0]["after"]["title"] == "Read [@smith2021, p. 3]"


def test_a_body_starting_with_a_rule_is_not_front_matter():
    from backend.tasks.service import rewrite_key

    assert rewrite_key("---\n[@a]\n---\n[@a]", "a", "b") == "---\n[@b]\n---\n[@b]"


# ------------------------------------------------------------------ converting checkbox lists
def test_convert_checkbox_list(svc, store):
    text = (
        "## TODOs\n"
        "- [ ] Add plot $\\chi$ vs $\\varepsilon$\n"
        "- [ ] Discuss the Ising model\n"
        "  Start from the linear response.\n"
        "  $$\n"
        "  J = 1\n"
        "  $$\n"
        "  - [x] Reproduce $N = 20$\n"
        "  - [ ] Simulate $N = 100$\n"
        "    - [ ] Check $\\chi$\n"
        "\n"
        "- [ ] Move to the Potts model\n"
        "\n"
        "Plain text stays.\n"
        "```\n- [ ] in code\n```\n"
    )
    out = svc.tasks.convert(text)
    tasks = {t["title"]: t for t in out["tasks"]}
    assert list(tasks) == [
        "Add plot $\\chi$ vs $\\varepsilon$",
        "Discuss the Ising model",
        "Reproduce $N = 20$",
        "Simulate $N = 100$",
        "Check $\\chi$",
        "Move to the Potts model",
    ]
    ising = tasks["Discuss the Ising model"]
    assert ising["body"] == "Start from the linear response.\n$$\nJ = 1\n$$"
    assert tasks["Reproduce $N = 20$"]["parent_id"] == ising["id"] and tasks["Reproduce $N = 20$"]["status"] == "done"
    assert tasks["Check $\\chi$"]["parent_id"] == tasks["Simulate $N = 100$"]["id"]
    ids = [
        tasks[k]["id"] for k in ("Add plot $\\chi$ vs $\\varepsilon$", "Discuss the Ising model", "Move to the Potts model")
    ]
    assert out["text"] == (
        f"## TODOs\n{token(ids[0])}\n{token(ids[1])}\n\n{token(ids[2])}\n\nPlain text stays.\n```\n- [ ] in code\n```\n"
    )


# ------------------------------------------------------------------ API
def test_api_round_trip(client, svc):
    svc.note_service.create(path="Notes/a.md", text="# A\n")
    r = client.post("/api/tasks", json={"title": "Plot", "due": "2026-10-21", "note": "Notes/a.md"})
    assert r.status_code == 201
    tid = r.json["task"]["id"]
    assert r.json["ref"]["path"] == "Notes/a.md"
    ov = client.get("/api/tasks").json
    assert [t["id"] for t in ov["tasks"]] == [tid] and ov["refs"][tid][0]["path"] == "Notes/a.md"
    sub = client.post("/api/tasks", json={"title": "Sub", "parent_id": tid}).json["task"]
    r = client.patch(f"/api/tasks/{tid}", json={"status": "done", "with_subtasks": True})
    assert {t["id"] for t in r.json["changed"]} == {tid, sub["id"]}
    r = client.patch(f"/api/tasks/{sub['id']}", json={"parent_id": None, "title": "Sub 2"})
    assert r.json["task"]["parent_id"] is None and r.json["task"]["title"] == "Sub 2"
    assert client.patch(f"/api/tasks/{tid}", json={"due": "soon"}).status_code == 400
    assert client.get("/api/tasks/nope0000").status_code == 404
    full = client.get(f"/api/tasks/{tid}").json["task"]
    assert full["refs"][0]["path"] == "Notes/a.md" and full["subtasks"] == []
    d = client.post("/api/tasks/delete", json={"ids": [tid]}).json
    assert client.get("/api/tasks/trash").json["tasks"][0]["id"] == tid
    client.post("/api/tasks/restore", json={"batch": d["batch"]})
    assert client.get("/api/tasks/trash").json["tasks"] == []
    r = client.post(f"/api/tasks/{tid}/notes/remove", json={"path": "Notes/a.md"})
    assert r.json["removed"] == 1 and client.get("/api/tasks").json["refs"] == {}
    r = client.post("/api/tasks/convert", json={"text": "- [ ] One\n- [ ] Two\n"})
    assert len(r.json["tasks"]) == 2 and r.json["text"].count("{{task:") == 2
    h = client.get(f"/api/tasks/{sub['id']}/history").json["history"]
    assert client.post("/api/tasks/undo", json={"history_id": h[0]["id"]}).json["task"]["title"] == "Sub"


def test_task_writes_are_their_own_change_kind(client, svc):
    seq = svc.changes.since(None, None, None)
    first = client.post("/api/tasks", json={"title": "x"}, headers={"X-Synapse-Client": "a"})
    assert first.status_code == 201
    out = svc.changes.since(seq["seq"], seq["epoch"], "b")
    assert out["changes"] == ["tasks"]


def test_images_in_task_bodies_stay_in_use(svc, store):
    store.create("With a plot", body="![plot](attachments/20261020-120000-plot.png)")
    assert svc.attachments.usage()["20261020-120000-plot.png"][0].startswith("task:")
