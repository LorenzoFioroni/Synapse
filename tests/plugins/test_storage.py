"""What plugins store, and that backups keep it."""

import json
import sqlite3
import tarfile

import pytest

from backend.plugins.storage import JsonStore, SqliteStore, Storage, StorageError


def test_json_store(tmp_path):
    s = JsonStore(tmp_path / "x" / "state.json")
    assert s.all() == {} and s.get("a", 1) == 1
    s.set("a", [1, 2])
    s.update({"b": "c"})
    assert JsonStore(tmp_path / "x" / "state.json").all() == {"a": [1, 2], "b": "c"}
    s.delete("a")
    assert s.all() == {"b": "c"}
    with pytest.raises(TypeError):
        s.set("bad", object())
    assert s.all() == {"b": "c"}


def test_sqlite_migrations_run_once_and_in_order(tmp_path):
    calls = []
    v1 = ["CREATE TABLE t (a TEXT);"]
    db = SqliteStore(tmp_path / "d.sqlite", v1, calls.append)
    assert db.version == 1 and calls == []  # a new database needs no backup
    with db.session() as c:
        c.execute("INSERT INTO t VALUES ('x')")
    v2 = v1 + ["ALTER TABLE t ADD COLUMN b INTEGER DEFAULT 7;"]
    db = SqliteStore(tmp_path / "d.sqlite", v2, calls.append)
    assert db.version == 2 and calls == ["d.sqlite"]  # upgrading data: backup first
    assert db.query("SELECT a, b FROM t") == [{"a": "x", "b": 7}]
    SqliteStore(tmp_path / "d.sqlite", v2, calls.append)
    assert calls == ["d.sqlite"]
    with pytest.raises(StorageError, match="newer version"):
        SqliteStore(tmp_path / "d.sqlite", v1)


def test_a_failing_migration_changes_nothing(tmp_path):
    db = SqliteStore(tmp_path / "d.sqlite", ["CREATE TABLE t (a TEXT);"])
    with pytest.raises(StorageError, match="migration 2 failed"):
        SqliteStore(tmp_path / "d.sqlite", ["CREATE TABLE t (a TEXT);", "CREATE TABLE u (a); SELECT nope FROM t;"])
    assert db.version == 1
    assert db.query("SELECT name FROM sqlite_master WHERE name = 'u'") == []


def test_session_rolls_back_on_error(tmp_path):
    db = SqliteStore(tmp_path / "d.sqlite", ["CREATE TABLE t (a TEXT);"])
    with pytest.raises(ValueError):
        with db.session() as c:
            c.execute("INSERT INTO t VALUES ('x')")
            raise ValueError
    assert db.query("SELECT * FROM t") == []


def test_store_names_are_checked(tmp_path):
    st = Storage(tmp_path)
    for bad in ("../x", "A", "", "a/b"):
        with pytest.raises(StorageError):
            st.json(bad)
    assert st.dir == tmp_path


def test_backups_hold_plugin_state_and_data_but_not_logs(client, svc, example):
    client.post("/api/plugin-host/recently-added/enable")
    client.put("/api/plugin-host/recently-added/settings", json={"keep_days": 5})
    client.post("/api/items", json={"title": "Kept in the backup"})
    svc.plugins.drain()
    data = svc.config.plugin_data_dir / "recently-added"
    assert (data / ".plugin.log").is_file()
    entry = svc.backups.create("manual")
    with tarfile.open(svc.backups.dir / entry["name"]) as tar:
        names = tar.getnames()
        assert "plugins.json" in names and "plugin-data/recently-added/added.sqlite" in names
        assert not [n for n in names if ".plugin.log" in n or n.endswith(("-wal", "-shm"))]
        copy = tar.extractfile("plugin-data/recently-added/added.sqlite").read()
    (svc.config.data_dir / "copy.sqlite").write_bytes(copy)
    rows = sqlite3.connect(str(svc.config.data_dir / "copy.sqlite")).execute("SELECT title FROM added").fetchall()
    assert rows == [("Kept in the backup",)]

    # later: another addition, the plugin turned off, its settings changed
    client.post("/api/items", json={"title": "After the backup"})
    svc.plugins.drain()
    client.put("/api/plugin-host/recently-added/settings", json={"keep_days": 50})
    client.post("/api/plugin-host/recently-added/disable")
    log_before = (data / ".plugin.log").read_text()

    client.post("/api/backups/restore", json={"name": entry["name"]})
    state = json.loads(svc.config.plugins_state_path.read_text())["plugins"]["recently-added"]
    assert state["enabled"] is True and state["settings"]["keep_days"] == 5
    info = svc.plugins.info("recently-added")
    assert info["running"]  # started again with the restored state
    titles = [i["title"] for i in client.get("/api/plugins/recently-added/recent").get_json()["items"]]
    assert titles == ["Kept in the backup"]
    assert (data / ".plugin.log").read_text().startswith(log_before)  # today's log kept


def test_restoring_an_older_backup_without_plugin_data_keeps_todays(client, svc, example):
    entry = svc.backups.create("manual")  # no plugin has stored anything yet
    client.post("/api/plugin-host/recently-added/enable")
    client.post("/api/items", json={"title": "Today"})
    svc.plugins.drain()
    client.post("/api/backups/restore", json={"name": entry["name"]})
    assert (svc.config.plugin_data_dir / "recently-added" / "added.sqlite").is_file()


def _write_shared_files(folder, worker):
    from pathlib import Path

    from backend.plugins.state import StateFile

    folder = Path(folder)
    for i in range(40):
        key = f"{worker}-{i}"
        JsonStore(folder / "shared.json").set(key, i)
        StateFile(folder / "plugins.json").update("shared", **{key: i})
    SqliteStore(folder / "shared.sqlite", ["CREATE TABLE records (value TEXT);"])


def test_app_and_mcp_writes_preserve_each_others_data(tmp_path):
    """Independent hosts may update different keys and initialize the same database at once."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    from backend.plugins.state import StateFile

    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = [pool.submit(_write_shared_files, str(tmp_path), worker) for worker in range(4)]
        for future in futures:
            future.result(timeout=30)
    expected = {f"{worker}-{i}": i for worker in range(4) for i in range(40)}
    assert JsonStore(tmp_path / "shared.json").all() == expected
    assert StateFile(tmp_path / "plugins.json").of("shared") == expected
    assert SqliteStore(tmp_path / "shared.sqlite", ["CREATE TABLE records (value TEXT);"]).version == 1
    assert not list(tmp_path.glob("*.tmp"))
