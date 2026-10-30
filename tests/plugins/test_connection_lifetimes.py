"""Plugin stores release SQLite handles without waiting for garbage collection."""

import sqlite3

import pytest

from backend.plugins.fields import FieldStore
from backend.plugins.jobs import JobQueue


@pytest.fixture(params=[FieldStore, JobQueue], ids=["fields", "jobs"])
def store(request, tmp_path, monkeypatch):
    connections = []
    connect = sqlite3.connect

    def tracked(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked)
    path = tmp_path / "fields.sqlite" if request.param is FieldStore else tmp_path / "jobs"
    return request.param(path), connections


def assert_closed(connections):
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_reads_and_writes_close_their_connections(store):
    database, connections = store
    if isinstance(database, FieldStore):
        database.put("demo", "score", {"paper": (7, None)})
        assert database.rows("demo", "score")["paper"]["value"] == 7
        assert database.for_keys(["paper"])[0]["value"] == 7
        assert database.expire("demo", "score") == 1
        assert database.delete("demo", "score") == 1
    else:
        assert database.add("score", ["paper"], key="paper")
        assert database.pending()
        job = database.next_due()
        assert job is not None
        database.retry(job["id"], "offline", 10)
        database.done(job["id"])
        assert database.pending() == []
    assert_closed(connections)


def test_failed_transaction_rolls_back_and_closes_its_connection(store):
    database, connections = store
    with database._connect() as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
    with pytest.raises(RuntimeError, match="interrupted"):
        with database._connect() as connection:
            connection.execute("INSERT INTO probe VALUES ('discarded')")
            raise RuntimeError("interrupted")
    with database._connect() as connection:
        assert connection.execute("SELECT * FROM probe").fetchall() == []
    assert_closed(connections)
