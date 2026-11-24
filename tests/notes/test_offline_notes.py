"""Edits made on a device while Synapse could not be reached (NotesService.apply_offline,
POST /api/notes/offline; docs/offline.md)."""

import time

import pytest

from backend.library.agent import on_device

ms = lambda t: int(t * 1000)  # noqa: E731


@pytest.fixture
def ns(svc):
    return svc.note_service


def test_offline_versions_become_dated_commits_by_the_device(ns):
    n = ns.create(title="Trip")
    base, h = ns.vault.read(n["path"])
    t0 = time.time() - 3600
    with on_device("iPad"):
        got = ns.apply_offline(
            n["path"], h, base, [{"text": base + "one\n", "at": ms(t0)}, {"text": base + "two\n", "at": ms(t0 + 600)}]
        )
    assert got["status"] == "applied" and got["text"] == base + "two\n"
    assert ns.vault.read(n["path"])[0] == base + "two\n"
    e = ns.history_of(n["path"])["entries"]
    offline = [x for x in e if "offline" in x["summary"]]
    assert len(offline) == 2 and {x["device"] for x in offline} == {"iPad"}
    assert abs(offline[1]["ts"] - t0) < 2 and abs(offline[0]["ts"] - (t0 + 600)) < 2  # dated when made
    assert ns.history.text_at(offline[1]["id"], n["path"]) == base + "one\n"


def test_edits_made_here_meanwhile_are_merged(ns):
    n = ns.create(title="Merge")
    ns.save(n["path"], "# Merge\n\nfirst\n\nmiddle\n\nlast\n", ns.vault.read(n["path"])[1])
    base, h = ns.vault.read(n["path"])
    ns.save(n["path"], base.replace("first", "FIRST"), h)  # here, meanwhile
    got = ns.apply_offline(n["path"], h, base, [{"text": base.replace("last", "LAST"), "at": ms(time.time())}])
    assert got["status"] == "merged"
    assert ns.vault.read(n["path"])[0] == "# Merge\n\nFIRST\n\nmiddle\n\nLAST\n"


def test_a_conflict_writes_nothing_and_returns_the_texts(ns):
    n = ns.create(title="Clash")
    ns.save(n["path"], "# Clash\n\nline\n", ns.vault.read(n["path"])[1])
    base, h = ns.vault.read(n["path"])
    ns.save(n["path"], "# Clash\n\nhere\n", h)
    now, now_hash = ns.vault.read(n["path"])
    got = ns.apply_offline(n["path"], h, base, [{"text": "# Clash\n\nthere\n", "at": ms(time.time())}])
    assert got["status"] == "conflict" and got["hash"] == now_hash
    assert got["current"] == now and got["mine"] == "# Clash\n\nthere\n" and got["base"] == base
    assert "<<<<<<< Saved in Synapse" in got["merged"] and ">>>>>>> Your offline edit" in got["merged"]
    assert ns.vault.read(n["path"])[0] == now


def test_a_note_deleted_here_meanwhile_is_reported(ns):
    n = ns.create(title="Gone")
    base, h = ns.vault.read(n["path"])
    ns.trash(n["path"])
    got = ns.apply_offline(n["path"], h, base, [{"text": base + "x\n", "at": ms(time.time())}])
    assert got["status"] == "deleted" and got["mine"] == base + "x\n"


def test_a_note_created_offline(ns):
    got = ns.apply_offline("trip/ideas.md", None, None, [{"text": "# Ideas\n", "at": ms(time.time())}])
    assert got["status"] == "applied" and ns.vault.read("trip/ideas.md")[0] == "# Ideas\n"


def test_a_clock_in_the_future_is_not_believed(ns):
    n = ns.create(title="Clock")
    base, h = ns.vault.read(n["path"])
    ns.apply_offline(n["path"], h, base, [{"text": base + "x\n", "at": ms(time.time() + 10**6)}])
    e = [x for x in ns.history_of(n["path"])["entries"] if "offline" in x["summary"]][0]
    assert e["ts"] <= time.time() + 5


def test_the_api(client, svc):
    n = svc.note_service.create(title="Api")
    base, h = svc.note_service.vault.read(n["path"])
    r = client.post("/api/notes/offline", json={"path": n["path"], "base_hash": h, "base_text": base,
                                                 "versions": [{"text": base + "y\n", "at": ms(time.time())}]})  # fmt: skip
    assert r.status_code == 200 and r.get_json()["status"] == "applied"
    assert client.post("/api/notes/offline", json={"path": n["path"], "versions": []}).status_code == 400
    assert client.post("/api/notes/offline", json={"path": "x.pdf", "versions": [{"text": "a"}]}).status_code == 400


def test_a_locked_note_is_refused(client, svc):
    svc.note_password.set("hunter22")
    secret = "---\nlocked: true\n---\n# Diary\n"
    client.put(
        "/api/notes/note", json={"path": "secret.md", "text": secret, "create": True}, headers={"X-Synapse-Client": "a"}
    )
    _, h = svc.note_service.vault.read("secret.md")
    r = client.post("/api/notes/offline", json={"path": "secret.md", "base_hash": h, "base_text": secret,
                                                 "versions": [{"text": secret + "x\n"}]}, headers={"X-Synapse-Client": "b"})  # fmt: skip
    assert r.status_code == 423
