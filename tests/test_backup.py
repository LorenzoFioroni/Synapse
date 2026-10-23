"""Backups of the library and notes."""

import io
import json
import sqlite3
import tarfile
import threading
import time
from datetime import datetime, timedelta

import pytest

from backend import backup as B
from backend.backup import BackupError
from backend.settings import SettingsError
from tests.conftest import make_pdf


def members(path):
    with tarfile.open(path, "r:gz") as tar:
        return [m.name for m in tar.getmembers()]


def note(svc, rel, text):
    p = svc.config.notes_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


@pytest.fixture
def library(client, svc, tmp_path):
    """Two items (one with a PDF), a folder, a note with an image, a trashed note, every derived file."""
    a = client.post("/api/items", json={"title": "Alpha paper", "year": 2020}).get_json()
    client.post("/api/items", json={"title": "Beta paper"})
    pdf = make_pdf(tmp_path / "a.pdf", "Alpha")
    client.post(
        f"/api/items/{a['id']}/attachments",
        data={"file": (pdf.open("rb"), "a.pdf")},
        content_type="multipart/form-data",
    )
    client.post("/api/folders", json={"name": "Thesis"})
    note(svc, "ideas/plan.md", "# Plan\n\n![fig](attachments/fig.png)\n")
    (svc.config.notes_dir / "attachments" / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    note(svc, ".trash/20261010-120000/old.md", "# Old\n")
    svc.settings.update({"citekey_pattern": "{auth}{year}"})
    svc.notes.sync()
    for f in ("fulltext.db", "citations.db"):
        (svc.config.data_dir / f).touch()
    (svc.config.backups_dir).mkdir(exist_ok=True)
    (svc.config.backups_dir / "notes-20261001-100000-000000.tar.gz").write_bytes(b"rename backup")
    return a


def test_archive_holds_exactly_the_library_settings_and_notes(svc, library):
    entry = svc.backups.create("manual")
    names = members(svc.backups.dir / entry["name"])
    assert names[0] == "manifest.json"
    assert {
        "library.db",
        "settings.json",
        "notes/ideas/plan.md",
        "notes/attachments/fig.png",
        "notes/.trash/20261010-120000/old.md",
    } <= set(names)
    for n in names:
        # plugins.json: which plugins are on (the core plugins' first state is written at start)
        assert n in ("manifest.json", "library.db", "settings.json", "plugins.json") or n.startswith("notes/"), n
        assert not n.startswith(("pdfs", "notes_index", "fulltext", "citations", "backups")), n
    assert entry["items"] == 2 and entry["notes"] == 1 and entry["reason"] == "manual"


def test_listing_reads_the_manifest(svc, library):
    entry = svc.backups.create("manual")
    [listed] = svc.backups.list()
    assert listed["name"] == entry["name"] and listed["items"] == 2 and listed["notes"] == 1
    assert svc.backups.newest()["name"] == entry["name"]


def test_backup_during_writes_is_a_valid_database(svc, library, tmp_path):
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            svc.papers.create({"title": f"Busy {i}"})
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    try:
        entries = [svc.backups.create("manual") for _ in range(3)]
    finally:
        stop.set()
        t.join()
    for e in entries:
        with tarfile.open(svc.backups.dir / e["name"], "r:gz") as tar:
            dest = tmp_path / e["name"]
            dest.mkdir()
            with tar.extractfile("library.db") as library_file:
                (dest / "library.db").write_bytes(library_file.read())
        c = sqlite3.connect(tmp_path / e["name"] / "library.db")
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        c.close()


def test_retention_keeps_the_newest_and_never_the_rename_backups(svc, library):
    svc.settings.update({"backup_keep": 3})
    names = []
    for _ in range(5):
        names.append(svc.backups.create("manual")["name"])
    kept = sorted(p.name for p in svc.backups.dir.glob("library-*.tar.gz"))
    assert kept == sorted(names[-3:])
    assert (svc.config.backups_dir / "notes-20261001-100000-000000.tar.gz").exists()


def test_restore_round_trip(client, svc, library):
    first = svc.backups.create("manual")
    # change everything after the backup
    client.post("/api/items", json={"title": "Gamma added later"})
    client.post("/api/items/trash", json={"ids": [library["id"]]})
    (svc.config.notes_dir / "ideas" / "plan.md").unlink()
    note(svc, "later.md", "# Later\n")
    svc.settings.update({"citekey_pattern": "{Auth}{Title}"})
    svc.notes.sync()

    r = client.post("/api/backups/restore", json={"name": first["name"]})
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    titles = sorted(p["title"] for p in svc.papers.list())
    assert titles == ["Alpha paper", "Beta paper"]
    assert (svc.config.notes_dir / "ideas" / "plan.md").exists()
    assert (svc.config.notes_dir / "attachments" / "fig.png").exists()
    assert not (svc.config.notes_dir / "later.md").exists()
    assert svc.settings.get("citekey_pattern") == "{auth}{year}"
    assert [n["path"] for n in svc.note_service.overview()["notes"]] == ["ideas/plan.md"]
    # the PDF of the entry is untouched and still opens
    att = svc.papers.get(library["id"])["attachments"][0]
    assert (svc.config.pdf_dir / att["url"]).exists()
    # the state before the restore was saved and can itself be restored
    assert res["safety_backup"] in [b["name"] for b in res["backups"]]
    assert client.post("/api/backups/restore", json={"name": res["safety_backup"]}).status_code == 200
    assert "Gamma added later" in [p["title"] for p in svc.papers.list()]
    assert (svc.config.notes_dir / "later.md").exists()
    assert not list(svc.config.data_dir.glob(".notes-before-restore-*"))


def test_restore_keeps_the_current_backup_settings(client, svc, library, tmp_path):
    first = svc.backups.create("manual")
    elsewhere = tmp_path / "elsewhere"
    svc.settings.update({"backup_dir": str(elsewhere), "backup_keep": 30, "font_size": 16})
    (elsewhere / first["name"]).write_bytes((svc.config.backups_dir / first["name"]).read_bytes())
    assert client.post("/api/backups/restore", json={"name": first["name"]}).status_code == 200
    assert svc.settings.get("backup_dir") == str(elsewhere) and svc.settings.get("backup_keep") == 30
    assert svc.settings.get("font_size") == 13  # everything else comes from the backup


def test_unwritable_folder_fails_without_touching_data(svc, library, tmp_path, monkeypatch):
    before = sorted(p["id"] for p in svc.papers.list())
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("x")
    monkeypatch.setattr(type(svc.backups), "dir", property(lambda self: blocked / "sub"))
    with pytest.raises(BackupError):
        svc.backups.create("manual")
    assert "Backup failed" in svc.backups.status()["error"]
    assert sorted(p["id"] for p in svc.papers.list()) == before
    assert (svc.config.notes_dir / "ideas" / "plan.md").exists()


def bad_archive(svc, name, files):
    svc.backups.dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(svc.backups.dir / name, "w:gz") as tar:
        for arc, data in files:
            info = tarfile.TarInfo(arc)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize(
    "files,why",
    [
        ([("manifest.json", b"{}"), ("library.db", b"not a database at all")], "damaged"),
        ([("manifest.json", b"{}")], "no library database"),
        ([("manifest.json", b"{}"), ("../evil.txt", b"x")], "Unexpected entry"),
        ([("manifest.json", b"{}"), ("pdfs/x.pdf", b"x")], "Unexpected entry"),
    ],
)
def test_bad_archives_are_refused_and_change_nothing(client, svc, library, files, why):
    bad_archive(svc, "library-20200101-000000.tar.gz", files)
    before = sorted(p["id"] for p in svc.papers.list())
    r = client.post("/api/backups/restore", json={"name": "library-20200101-000000.tar.gz"})
    assert r.status_code == 400 and why in r.get_json()["error"]
    assert sorted(p["id"] for p in svc.papers.list()) == before
    assert (svc.config.notes_dir / "ideas" / "plan.md").exists()
    assert not (svc.config.data_dir / "evil.txt").exists()


@pytest.mark.parametrize(
    "name", ["../settings.json", "library.db", "library-x.tar.gz", "", "notes-20261001-100000-000000.tar.gz"]
)
def test_restore_only_known_backups(client, library, name):
    assert client.post("/api/backups/restore", json={"name": name}).status_code == 400


def test_due(svc, library):
    assert svc.backups.due()  # no backup yet
    svc.backups.create("scheduled")
    assert not svc.backups.due()
    assert svc.backups.due(datetime.now() + timedelta(days=1, minutes=1))
    svc.settings.update({"backup_interval": "weekly"})
    assert not svc.backups.due(datetime.now() + timedelta(days=3))
    assert svc.backups.due(datetime.now() + timedelta(days=8))
    svc.settings.update({"backup_interval": "off"})
    assert not svc.backups.due(datetime.now() + timedelta(days=80))
    assert svc.backups.run_if_due() is None


def test_backup_before_emptying_the_trash_at_most_every_ten_minutes(client, svc, library):
    client.post("/api/items/trash", json={"ids": [library["id"]]})
    assert client.post("/api/trash/empty").status_code == 200
    [b] = svc.backups.list()
    assert b["reason"] == "before emptying the Trash" and b["items"] == 1  # the trashed item is in it
    with tarfile.open(svc.backups.dir / b["name"]) as tar:
        dest = svc.config.data_dir / "check"
        dest.mkdir()
        with tar.extractfile("library.db") as library_file:
            (dest / "library.db").write_bytes(library_file.read())
    c = sqlite3.connect(svc.config.data_dir / "check" / "library.db")
    assert c.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2
    c.close()
    other = client.post("/api/items", json={"title": "Temp"}).get_json()
    client.post("/api/items/trash", json={"ids": [other["id"]]})
    client.post("/api/trash/empty")
    assert len(svc.backups.list()) == 1  # within ten minutes: no second one


def test_backup_before_import_and_regenerate_all(client, svc, library, monkeypatch):
    made = []
    monkeypatch.setattr(svc.backups, "create", lambda reason: made.append(reason) or {"name": "x"})
    client.post("/api/import/commit", json={"entries": [{"title": "Imported"}]})
    svc.backups._last_before = None
    ids = [client.post("/api/items", json={"title": f"P{i}"}).get_json()["id"] for i in range(12)]
    client.post("/api/items/citekeys", json={"ids": ids})
    assert made == ["before an import", "before regenerating citation keys"]


def test_no_backup_before_operations_when_off(client, svc, library):
    svc.settings.update({"backup_interval": "off"})
    client.post("/api/items/trash", json={"ids": [library["id"]]})
    client.post("/api/trash/empty")
    assert svc.backups.list() == []


def test_api_status_and_manual_backup(client, svc, library):
    st = client.get("/api/backups").get_json()
    assert st["backups"] == [] and st["last"] is None and st["interval"] == "daily" and st["keep"] == 14
    r = client.post("/api/backups")
    assert r.status_code == 201
    st = r.get_json()
    assert st["last"]["name"] == st["backup"]["name"] and len(st["backups"]) == 1


def test_custom_backup_folder(client, svc, library, tmp_path):
    target = tmp_path / "Dropbox" / "Synapse backups"
    svc.settings.update({"backup_dir": str(target)})
    entry = svc.backups.create("manual")
    assert (target / entry["name"]).exists()
    assert client.get("/api/backups").get_json()["dir"] == str(target)
    with pytest.raises(SettingsError):
        svc.settings.update({"backup_dir": "relative/folder"})
    with pytest.raises(SettingsError):
        svc.settings.update({"backup_keep": 0})


def test_settings_reload(svc, tmp_path):
    svc.settings.update({"font_size": 15})
    data = json.loads(svc.config.settings_path.read_text())
    data["font_size"] = 12
    svc.config.settings_path.write_text(json.dumps(data))
    svc.settings.reload()
    assert svc.settings.get("font_size") == 12


def test_scheduler_backs_up_on_start(svc, library, monkeypatch):
    monkeypatch.setattr(B, "CHECK_EVERY_S", 3600)
    svc.backups.start()
    for _ in range(50):
        if svc.backups.list():
            break
        time.sleep(0.1)
    svc.backups.stop()
    assert svc.backups.list()[0]["reason"] == "scheduled"
