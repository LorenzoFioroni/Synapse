"""The notes history (backend/notes/history/): Git commits for every change, who made it, and
what the app does with them."""

import subprocess
import sys
import tarfile
import textwrap
import time
from pathlib import Path

import pytest

from backend.config import Config
from backend.library.agent import acting_as, on_device
from backend.notes.history import recorder
from backend.notes.history.git import EXCLUDE, lock_for
from backend.notes.history.legacy import LegacyHistory
from backend.services import Services


@pytest.fixture
def ns(svc):
    return svc.note_service


def git(svc, *args) -> str:
    """Plain git on the history, as someone inspecting it by hand would."""
    h = svc.note_service.history
    return h.repo.out(*args)


def kinds(ns, path):
    return [(e["by"], e["kind"]) for e in ns.history_of(path)["entries"]]


def write(ns, path, text):
    _, h = ns.vault.read(path)
    return ns.save(path, text, h)


def test_the_repository_sits_next_to_the_notes_and_keeps_only_their_text(svc, ns):
    n = ns.create(title="Alpha")
    (ns.vault.root / "attachments" / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (ns.vault.root / ".trash" / "x").mkdir(parents=True)
    (ns.vault.root / ".trash" / "x" / "old.md").write_text("old")
    (ns.vault.root / ".folders.json").write_text("{}")
    (ns.vault.root / "notes.txt").write_text("not a note")
    ns.checkpoint()
    assert svc.config.notes_history_dir.is_dir() and not (ns.vault.root / ".git").exists()
    assert git(svc, "ls-files").split("\n")[:-1] == [n["path"]]
    assert (svc.config.notes_history_dir / "info" / "exclude").read_text() == EXCLUDE
    assert "worktree" not in (svc.config.notes_history_dir / "config").read_text()  # data/ may move


def test_the_users_own_git_setup_is_ignored(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    hooks = home / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    (home / ".gitconfig").write_text(
        textwrap.dedent(f"""\
        [commit]
            gpgsign = true
        [core]
            hooksPath = {hooks}
            autocrlf = true
        [user]
            name = Someone Else
        """)
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))  # leaks from a terminal must not matter
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "index"))
    s = Services(Config(tmp_path / "data"))
    n = s.note_service.create(title="Plain", text="line one\nline two\n")
    s.note_service.checkpoint()
    entries = s.note_service.history_of(n["path"])["entries"]
    assert len(entries) == 1 and entries[0]["by"] == "human"
    raw = s.note_service.history.text_at(entries[0]["id"], n["path"])
    assert "\r\n" not in raw  # no autocrlf
    assert not (tmp_path / "elsewhere").exists()


def test_the_users_edits_wait_until_the_note_is_left_alone(svc, ns, monkeypatch):
    n = ns.create(title="Draft")
    for k in range(5):
        write(ns, n["path"], f"version {k}\n")
    assert git(svc, "rev-list", "--all") == ""  # nothing committed yet: one checkpoint later
    assert ns.history.due() == []  # not idle yet
    monkeypatch.setattr(recorder, "IDLE", 0)
    made = ns.history.due()
    assert len(made) == 1
    e = ns.history_of(n["path"])["entries"]
    assert len(e) == 1 and e[0]["summary"] == "Created Draft" and e[0]["device"] == "This computer"
    assert ns.history.text_at(e[0]["id"], n["path"]) == "version 4\n"
    write(ns, n["path"], "version 5\n")
    ns.checkpoint(n["path"])  # what the app asks when the note is closed
    assert ns.history_of(n["path"])["entries"][0]["summary"] == "Edited Draft"


def test_an_edit_undone_leaves_no_versions(svc, ns):
    p = ns.create(title="Undo", text="original\n")["path"]
    ns.checkpoint()
    start = git(svc, "rev-parse", "HEAD").strip()
    original = ns.vault.read(p)[0]
    write(ns, p, original + "typed\n")
    ns.checkpoint(p)  # e.g. the history was opened in between
    write(ns, p, original + "typed\nmore\n")
    ns.checkpoint(p)
    write(ns, p, original)  # undone, back to the start
    assert ns.checkpoint(p)["commits"] == []
    assert git(svc, "rev-parse", "HEAD").strip() == start and not git(svc, "status", "--porcelain")
    # back to an earlier version of the session: only what came after it goes
    write(ns, p, original + "kept\n")
    ns.checkpoint(p)
    kept = git(svc, "rev-parse", "HEAD").strip()
    write(ns, p, original + "kept\ndetour\n")
    ns.checkpoint(p)
    write(ns, p, original + "kept\n")
    ns.checkpoint(p)
    assert git(svc, "rev-parse", "HEAD").strip() == kept


def test_an_undone_edit_keeps_what_others_did_in_between(svc, ns):
    p = ns.create(title="Kept", text="original\n")["path"]
    ns.checkpoint()
    original = ns.vault.read(p)[0]
    write(ns, p, original + "typed\n")
    ns.checkpoint(p)
    with on_device("iPad"):  # another device's version in between
        write(ns, p, original + "typed\nfrom the iPad\n")
    ns.checkpoint(p)
    write(ns, p, original)
    ns.checkpoint(p)
    assert [e["device"] for e in ns.history_of(p)["entries"]][:3] == ["This computer", "iPad", "This computer"]
    m = ns.add_milestone("Marked")  # a milestone keeps the version it marks
    write(ns, p, original + "after the milestone\n")
    ns.checkpoint(p)
    write(ns, p, original)
    ns.checkpoint(p)
    assert m["commit"] in git(svc, "rev-list", "HEAD")
    assert ns.history_of(p)["entries"][0]["summary"] == "Edited Kept"


def test_another_device_starts_a_new_version(svc, ns):
    n = ns.create(title="Shared")
    write(ns, n["path"], "from the Mac\n")
    with on_device("iPad"):
        write(ns, n["path"], "from the iPad\n")
    ns.checkpoint()
    e = ns.history_of(n["path"])["entries"]
    assert [x["device"] for x in e] == ["iPad", "This computer"]
    assert ns.history.text_at(e[1]["id"], n["path"]) == "from the Mac\n"


def test_changes_made_outside_synapse_are_recorded_as_such_first(svc, ns):
    n = ns.create(title="Outside")
    ns.checkpoint()
    (ns.vault.root / n["path"]).write_text("edited in another editor\n")
    (ns.vault.root / "New elsewhere.md").write_text("made elsewhere\n")
    write(ns, n["path"], "edited in another editor\nthen in Synapse\n")
    ns.checkpoint()
    e = ns.history_of(n["path"])["entries"]
    assert [(x["by"], x["summary"]) for x in e] == [
        ("human", "Edited Outside"),
        ("outside", "Changed Outside outside Synapse"),
        ("human", "Created Outside"),
    ]
    other = ns.history_of("New elsewhere.md")["entries"]
    assert other[0]["by"] == "outside" and other[0]["summary"] == "Created New elsewhere outside Synapse"


def test_an_ai_change_holds_only_its_own_change(svc, ns):
    n = ns.create(title="Mixed", text="one\n")
    write(ns, n["path"], "one\ntyped by me\n")
    with acting_as("Claude Code"):
        _, h = ns.vault.read(n["path"])
        r = ns.edit(n["path"], "one\ntyped by me\nadded by Claude\n", h, by="ai", summary="Added a line")
    e = ns.history_of(n["path"])["entries"]
    assert [(x["by"], x.get("agent")) for x in e] == [("ai", "Claude Code"), ("human", None)]
    assert e[0]["id"] == r["version"] and e[0]["summary"] == "Added a line"
    v = ns.history_version(e[0]["id"], n["path"])
    assert v["before"] == "one\ntyped by me\n" and v["after"] == "one\ntyped by me\nadded by Claude\n"
    author = git(svc, "log", "-1", "--format=%an <%ae>")
    assert author.strip() == "Claude Code <ai@synapse.local>"


def test_renames_are_pure_so_the_history_follows_the_note(svc, ns):
    n = ns.create(title="Short", text="x\n")
    write(ns, n["path"], "x\ny\n")
    ns.move(n["path"], "folder/Long name.md")
    _, h = ns.vault.read("folder/Long name.md")
    ns.save("folder/Long name.md", "completely different text now\n", h)  # would defeat rename detection
    ns.checkpoint()
    e = ns.history_of("folder/Long name.md")["entries"]
    assert [x["kind"] for x in e] == ["edit", "rename", "edit"]
    assert e[1]["summary"] == "Moved Short to folder/Long name.md"
    assert e[1]["status"] == "R" and e[1]["from_path"] == n["path"] and e[1]["before_hash"] == e[1]["after_hash"]


def test_deleting_and_restoring_are_recorded(svc, ns):
    a = ns.create(title="A")["path"]
    ns.create(title="B", folder="f")
    t = ns.trash(a)
    ns.restore(t["trash_id"], a)
    many = ns.trash_many([a], ["f"])
    ns.restore_batch(many["trash_id"])
    subjects = git(svc, "log", "--format=%s").split("\n")[:-1]
    assert subjects == ["Restored from the trash", "Deleted 2 notes", "Restored A from the trash", "Created B",
                        "Deleted A", "Created A"]  # fmt: skip


def test_a_citation_key_rename_is_one_change(svc, ns):
    pid = svc.papers.create({"title": "Paper", "citation_key": "old2020"})["id"]
    ns.create(title="One", text="See [@old2020].\n")
    ns.create(title="Two", text="Also [@old2020, p. 3].\n")
    svc.papers.update(pid, {"citation_key": "new2020"})
    log = ns.history_log()["entries"]
    assert log[0]["kind"] == "bulk" and log[0]["summary"] == "Citation key old2020 became new2020"
    assert sorted(f["path"] for f in log[0]["files"]) == ["One.md", "Two.md"]
    assert log[1]["kind"] == "edit"  # the notes as they were, first


def test_milestones(svc, ns):
    a = ns.create(title="Kept", text="first\n")["path"]
    gone = ns.create(title="Gone", text="deleted later\n")["path"]
    m = ns.add_milestone("Submitted")
    assert ns.history_log()["milestones"][0]["label"] == "Submitted"
    write(ns, a, "second\n")
    ns.trash(gone)
    new = ns.create(title="New", text="made after\n")["path"]
    d = ns.history_diff(m["id"], "now")
    assert {(f["status"], f["path"]) for f in d["files"]} == {("M", a), ("D", gone), ("A", new)}
    changed = next(f for f in d["files"] if f["path"] == a)
    assert changed["before"].endswith("first\n") and changed["after"] == "second\n"
    r = ns.restore_milestone(m["id"])
    assert ns.vault.read(a)[0].endswith("first\n") and ns.vault.read(gone)[0].endswith("deleted later\n")
    assert r["trashed"] == [new] and not ns.vault.exists(new) and r["trash_id"]
    assert ns.history_log()["entries"][0]["summary"] == "Restored the milestone “Submitted”"
    assert ns.rename_milestone(m["id"], "Accepted")["label"] == "Accepted"
    ns.delete_milestone(m["id"])
    assert ns.history_log()["milestones"] == []
    with pytest.raises(ValueError):
        ns.add_milestone("  ")


def test_putting_an_earlier_version_back(svc, ns):
    p = ns.create(title="Back", text="v1\n")["path"]
    ns.checkpoint()
    old = ns.history_of(p)["entries"][0]["id"]
    write(ns, p, "v2\n")
    ns.checkpoint()
    write(ns, p, "v1\n")  # the app puts the text in the editor and saves it …
    ns.checkpoint(p, restored_from=old)  # … then says where it came from
    e = ns.history_of(p)["entries"][0]
    assert (
        e["kind"] == "restore" and e["restored_from"] == old and e["summary"] == "Restored an earlier version of Back"
    )


def test_undoing_an_ai_change_keeps_later_edits(svc, ns):
    p = ns.create(title="Rev", text="a\nb\nc\nd\ne\nf\n")["path"]
    base, h = ns.vault.read(p)  # with its front matter
    with acting_as("Codex"):
        c = ns.edit(p, base + "AI\n", h, by="ai", summary="AI line")["version"]
    top = base.replace("a\n", "TOP\na\n", 1)
    write(ns, p, top + "AI\n")
    assert ns.history.reverted(c, p, ns.vault.read(p)[0]) == top
    write(ns, p, top + "AI changed\n")
    with pytest.raises(ValueError):
        ns.history.reverted(c, p, ns.vault.read(p)[0])


def test_the_app_and_the_mcp_server_take_turns(svc, tmp_path):
    lock = svc.note_service.history.lock
    held = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""\
            import sys, time
            sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})
            from backend.notes.history.git import FileLock
            l = FileLock({str(lock.path)!r}); l.acquire(); print("held", flush=True); time.sleep(3)
        """)],
        stdout=subprocess.PIPE,
    )  # fmt: skip
    try:
        assert held.stdout.readline().strip() == b"held"
        assert not lock.acquire(timeout=0.3)
    finally:
        held.kill()
        held.wait()
    assert lock.acquire(timeout=5)
    lock.release()
    assert lock_for(lock.path) is lock  # one lock object per file in a process


def test_a_second_process_sees_the_apps_pending_edits(tmp_path):
    cfg = Config(tmp_path / "data")
    app = Services(cfg).note_service
    p = app.create(title="Both", text="start\n")["path"]
    app.checkpoint()
    write(app, p, "start\ntyped in the app\n")  # pending
    mcp = Services(cfg).note_service  # what mcp_server.py builds
    with acting_as("Claude"):
        _, h = mcp.vault.read(p)
        mcp.edit(p, "start\ntyped in the app\nby Claude\n", h, by="ai", summary="Claude's line")
    e = app.history_of(p)["entries"]
    assert [(x["by"], x["kind"]) for x in e] == [("ai", "ai"), ("human", "edit"), ("human", "edit")]


def test_saving_never_fails_because_of_the_history(svc, ns, monkeypatch):
    p = ns.create(title="Robust", text="x\n")["path"]
    monkeypatch.setattr(ns.history.repo, "exe", str(Path("/nonexistent/git")))
    write(ns, p, "still saved\n")
    with acting_as("Claude"):
        _, h = ns.vault.read(p)
        r = ns.edit(p, "still saved by AI\n", h, by="ai", summary="x")
    assert ns.vault.read(p)[0] == "still saved by AI\n" and r["version"] is None
    assert ns.history_status()["error"]


def test_without_git_ai_changes_are_kept_the_old_way(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_GIT", "")
    s = Services(Config(tmp_path / "data"))
    ns = s.note_service
    assert ns.history_status()["enabled"] is False and "not installed" in ns.history_status()["reason"]
    p = ns.create(title="NoGit", text="x\n")["path"]
    with acting_as("Claude"):
        _, h = ns.vault.read(p)
        r = ns.edit(p, "y\n", h, by="ai", summary="Changed")
    hist = ns.history_of(p)
    assert hist["enabled"] is False and hist["entries"][0]["id"] == r["version"]
    assert ns.history_version(r["version"])["before"].endswith("x\n")
    assert not s.config.notes_history_dir.exists()


def test_the_old_json_history_becomes_the_first_commits(tmp_path):
    cfg = Config(tmp_path / "data")
    notes = cfg.notes_dir
    (notes / "papers").mkdir(parents=True)
    legacy = LegacyHistory(notes)
    # note A: two AI changes, nothing typed in between, still exactly what the second left
    (notes / "A.md").write_text("a2\n")
    from backend.notes.vault import content_hash as ch

    e1 = legacy.record(
        "A.md", before="a0\n", before_hash=ch("a0\n"), after_hash=ch("a1\n"), agent="Claude", summary="first"
    )
    time.sleep(0.01)
    legacy.record(
        "A.md", before="a1\n", before_hash=ch("a1\n"), after_hash=ch("a2\n"), agent="Claude", summary="second"
    )
    # note B: the user typed after the AI change, so its own version is unknown
    (notes / "papers" / "B.md").write_text("b-now\n")
    legacy.record("papers/B.md", before="b0\n", before_hash=ch("b0\n"), after_hash=ch("b1\n"), summary="only")
    s = Services(cfg)
    ns = s.note_service
    a = ns.history_of("A.md")["entries"]
    assert [(x["kind"], x["summary"]) for x in a] == [("ai", "second"), ("ai", "first"), ("earlier", "Earlier history")]
    assert ns.history.text_at(a[1]["id"], "A.md") == "a1\n" and a[0]["current"]
    assert a[1]["at"][:16] == e1["at"][:16] and a[1]["agent"] == "Claude"
    b = ns.history_of("papers/B.md")["entries"]
    assert [x["kind"] for x in b] == ["start", "earlier"] and b[0]["summary"] == "Start of the history"
    assert ns.history.text_at(b[0]["id"], "papers/B.md") == "b-now\n"
    assert (notes / ".history").is_dir()  # the old files are left where they were


def test_backups_carry_the_history_and_restores_add_to_it(svc, ns):
    p = ns.create(title="Backed", text="before the backup\n")["path"]
    entry = svc.backups.create()
    with tarfile.open(svc.backups.dir / entry["name"]) as tar:
        assert "notes-history.bundle" in tar.getnames()
    write(ns, p, "after the backup\n")
    ns.checkpoint()
    svc.backups.restore(entry["name"])
    assert ns.vault.read(p)[0].endswith("before the backup\n")
    e = ns.history_of(p)["entries"]
    assert e[0]["summary"].startswith("Restored the backup library-") and e[1]["summary"] == "Edited Backed"


def test_a_lost_history_comes_back_from_the_backup(svc, ns):
    import shutil

    p = ns.create(title="Lost", text="kept\n")["path"]
    entry = svc.backups.create()
    shutil.rmtree(svc.config.notes_history_dir)
    ns.history._ready = False
    svc.backups.restore(entry["name"])
    e = ns.history_of(p)["entries"]
    assert [x["summary"] for x in e] == ["Created Lost"]


def test_the_history_api(client, svc):
    n = client.post("/api/notes/note", json={"title": "Api", "text": "one\n"}).json
    client.put("/api/notes/note", json={"path": n["path"], "text": "two\n", "hash": n["hash"]})
    assert client.get("/api/notes/history/status").json["enabled"] is True
    assert client.post("/api/notes/history/checkpoint", json={"path": n["path"]}).json["commits"]
    log = client.get("/api/notes/history/log").json
    c = log["entries"][0]["id"]
    d = client.get(f"/api/notes/history/diff?from={c}^&to={c}").json
    assert d["files"][0]["status"] == "A" and d["files"][0]["after"] == "two\n"
    assert client.get("/api/notes/history/diff?from=nonsense!&to=now").status_code == 404
    m = client.post("/api/notes/history/milestones", json={"label": "M1"})
    assert m.status_code == 201
    mid = m.json["id"]
    assert client.put(f"/api/notes/history/milestones/{mid}", json={"label": "M2"}).json["label"] == "M2"
    assert client.post(f"/api/notes/history/milestones/{mid}/restore").json["restored"] == []
    assert client.delete(f"/api/notes/history/milestones/{mid}").json == {"deleted": mid}
    assert client.post("/api/notes/history/checkpoint", json={"restored_from": "x; rm"}).status_code == 400


def test_refresh_worker_runs_without_git_and_retries_failed_refresh(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(recorder, "find_git", lambda: None)
    monkeypatch.setattr(recorder, "TICK", 0.01)
    history = recorder.NoteHistory(tmp_path / "notes", tmp_path / "history.git")
    retried = threading.Event()
    calls = []

    def refresh():
        calls.append(True)
        if len(calls) == 1:
            raise OSError("temporarily unavailable")
        retried.set()

    history.on_tick = refresh
    try:
        history.start()
        assert retried.wait(5)
        assert not history.enabled
    finally:
        history.stop()
