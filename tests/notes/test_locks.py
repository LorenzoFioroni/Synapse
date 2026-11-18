"""Locked notes (backend/notes/locks.py): what a page that did not unlock a note can see of it."""

import pytest

from backend.notes.locks import NoteLocked

A = {"X-Synapse-Client": "page-a"}
B = {"X-Synapse-Client": "page-b"}
SETUP = {"X-Synapse-Client": "setup"}  # the page that wrote the notes (it keeps them unlocked)
SECRET = "---\nlocked: true\n---\n# Diary\n\nThe zebracorn plan cites [@smith2021] and links [[public]].\n#private\n"


@pytest.fixture
def notes(client, svc):
    svc.note_password.set("hunter22")
    assert (
        client.put(
            "/api/notes/note", json={"path": "secret.md", "text": SECRET, "create": True}, headers=SETUP
        ).status_code
        < 300
    )
    pub = "# Public\n\nSee [[secret]].\n"
    assert (
        client.put(
            "/api/notes/note", json={"path": "public.md", "text": pub, "create": True}, headers=SETUP
        ).status_code
        < 300
    )
    return svc


def unlock(client, headers=A, password="hunter22", path="secret.md"):
    return client.post("/api/notes/unlock", json={"path": path, "password": password}, headers=headers)


def test_a_locked_note_opens_only_for_the_page_that_unlocked_it(client, notes):
    r = client.get("/api/notes/note?path=secret.md", headers=A)
    assert r.status_code == 423 and r.get_json()["locked"] is True
    assert "zebracorn" not in r.get_data(as_text=True)
    assert unlock(client, password="wrong").status_code == 403
    r = unlock(client)
    assert r.status_code < 300 and r.get_json()["unlocked_for"] == 600
    got = client.get("/api/notes/note?path=secret.md", headers=A).get_json()
    assert "zebracorn" in got["text"] and got["locked"] is True and got["unlocked_for"] > 0
    assert client.get("/api/notes/note?path=secret.md", headers=B).status_code == 423
    assert client.get("/api/notes/note?path=secret.md").status_code == 423  # no page id
    assert client.post("/api/notes/relock", json={"paths": ["secret.md"]}, headers=A).status_code < 300
    assert client.get("/api/notes/note?path=secret.md", headers=A).status_code == 423


def test_an_unlocked_note_locks_again_after_the_timeout_unless_used(client, notes):
    now = [1000.0]
    notes.note_service.grants.clock = lambda: now[0]
    notes.settings.update({"notes_lock_minutes": 1})
    unlock(client)
    now[0] += 50  # a click, a scroll …: open for a whole minute from now
    r = client.post("/api/notes/unlock/touch", json={"paths": ["secret.md"]}, headers=A)
    assert r.get_json()["unlocked_for"] == {"secret.md": 60}
    now[0] += 50
    h = client.get("/api/notes/note?path=secret.md", headers=A).get_json()["hash"]
    r = client.put("/api/notes/note", json={"path": "secret.md", "text": SECRET + "more\n", "hash": h}, headers=A)
    assert r.status_code < 300  # a save: open for a whole minute from now
    now[0] += 50
    assert client.get("/api/notes/note?path=secret.md&unless=x", headers=A).status_code < 300
    now[0] += 11  # reading (the tab checking for changes) did not postpone it: a forgotten tab locks
    assert client.get("/api/notes/note?path=secret.md", headers=A).status_code == 423
    r = client.post("/api/notes/unlock/touch", json={"paths": ["secret.md"]}, headers=A)
    assert r.get_json()["unlocked_for"] == {"secret.md": None}


def test_nothing_of_a_locked_note_shows_elsewhere(client, notes):
    assert client.get("/api/notes/search?q=zebracorn", headers=A).get_json()["results"] == []
    listing = {n["path"]: n for n in client.get("/api/notes").get_json()["notes"]}
    assert listing["secret.md"]["locked"] is True and listing["secret.md"]["tags"] == []
    assert listing["secret.md"]["title"] == "Diary"  # the title stays visible
    links = client.get("/api/notes/links?path=public.md", headers=A).get_json()
    assert links["backlinks"] == []  # secret links to public: not shown
    assert all(e["source"] != "secret.md" for e in notes.notes.link_graph())
    assert notes.notes.citing("smith2021") == []
    assert notes.notes.key_summary() == {}
    assert client.get("/api/notes/links?path=secret.md", headers=A).status_code == 423
    unlock(client)  # unlocked, its own links show; still nowhere else
    assert client.get("/api/notes/links?path=secret.md", headers=A).status_code < 300
    assert client.get("/api/notes/search?q=zebracorn", headers=A).get_json()["results"] == []


def test_saving_history_and_lock_removal_need_the_note_unlocked(client, notes):
    h = notes.vault.read("secret.md")[1]
    r = client.put("/api/notes/note", json={"path": "secret.md", "text": "gone", "hash": h}, headers=A)
    assert r.status_code == 423
    assert client.get("/api/notes/history?path=secret.md", headers=A).status_code == 423
    assert client.post("/api/notes/lock", json={"path": "secret.md", "locked": False}, headers=A).status_code == 423
    unlock(client)
    assert client.get("/api/notes/history?path=secret.md", headers=A).status_code < 300
    r = client.post("/api/notes/lock", json={"path": "secret.md", "locked": False}, headers=A)
    assert r.status_code < 300 and "locked" not in notes.vault.read("secret.md")[0]
    assert client.get("/api/notes/note?path=secret.md", headers=B).status_code < 300


def test_locking_needs_a_password_and_keeps_the_note_open_for_the_page_that_locked_it(client, svc):
    client.put("/api/notes/note", json={"path": "n.md", "text": "# N\n\nbody\n", "create": True}, headers=A)
    assert client.post("/api/notes/lock", json={"path": "n.md", "locked": True}, headers=A).status_code == 409
    svc.note_password.set("hunter22")
    r = client.post("/api/notes/lock", json={"path": "n.md", "locked": True}, headers=A)
    assert r.status_code < 300 and svc.vault.read("n.md")[0].startswith("---\nlocked: true\n---\n")
    assert client.get("/api/notes/note?path=n.md", headers=A).status_code < 300
    assert client.get("/api/notes/note?path=n.md", headers=B).status_code == 423


def test_history_diffs_leave_out_the_text_of_locked_notes(client, notes):
    notes.note_service.history.checkpoint()
    log = client.get("/api/notes/history/log", headers=A).get_json()
    if not log.get("enabled"):
        pytest.skip("Git is not installed")
    d = client.get("/api/notes/history/diff?from=" + log["entries"][-1]["id"] + "^&to=now", headers=A).get_json()
    secret = next(f for f in d["files"] if f["path"] == "secret.md")
    assert secret["locked"] is True and "after" not in secret
    assert "zebracorn" not in str(d)


def test_ai_apps_never_read_a_locked_note(notes):
    with pytest.raises(NoteLocked):
        notes.note_service.get("secret.md")  # outside a request of the app: the MCP server
    from backend.companion import prompt

    with pytest.raises(ValueError, match="locked"):
        prompt.build("what?", {"kind": "note", "path": "secret.md"}, notes=notes.note_service, papers=notes.papers)


def test_a_moved_note_stays_unlocked_and_wrong_passwords_slow_down(client, notes):
    unlock(client)
    assert client.post("/api/notes/move", json={"path": "secret.md", "to": "diary.md"}, headers=A).status_code < 300
    assert client.get("/api/notes/note?path=diary.md", headers=A).status_code < 300
    for _ in range(5):
        assert unlock(client, B, "nope", "diary.md").status_code == 403
    assert unlock(client, B, "hunter22", "diary.md").status_code == 429


def test_the_password_is_set_here_and_changed_with_the_current_one(client, svc):
    assert client.get("/api/notes/locks").get_json()["password_set"] is False
    assert client.put("/api/notes/locks/password", json={"password": "abc"}).status_code == 400
    assert client.put("/api/notes/locks/password", json={"password": "hunter22"}).status_code < 300
    assert svc.note_password.check("hunter22", "x")
    settings = svc.config.data_dir / "settings.json"
    assert not settings.exists() or "hunter22" not in settings.read_text()
