"""Background work: cron rules, intervals, schedules catching up, the job queue and its runner."""

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.plugins.errors import PluginError
from backend.plugins.http import HttpError
from backend.plugins.jobs import Handler, JobQueue, Jobs, Runner
from backend.plugins.schedule import Cron, every_seconds, make

ROME = ZoneInfo("Europe/Rome")


def at(*a, tz=ROME):
    return datetime(*a, tzinfo=tz)


def test_cron_next_times():
    weekday_night = Cron.parse("30 2 * * 1-5")
    assert weekday_night.next_after(at(2026, 10, 22, 1, 0)) == at(2026, 10, 22, 2, 30)  # a Thursday
    assert weekday_night.next_after(at(2026, 10, 22, 2, 30)) == at(2026, 10, 23, 2, 30)
    assert weekday_night.next_after(at(2026, 10, 23, 3, 0)) == at(2026, 10, 26, 2, 30)  # Fri → Mon
    assert Cron.parse("*/15 * * * *").next_after(at(2026, 1, 1, 10, 7)) == at(2026, 1, 1, 10, 15)
    assert Cron.parse("0 9 1 jan *").next_after(at(2026, 3, 1, 0, 0)) == at(2027, 1, 1, 9, 0)
    assert Cron.parse("0 0 * * sun").next_after(at(2026, 10, 22, 0, 0)) == at(2026, 10, 25, 0, 0)
    assert Cron.parse("0 0 * * 7").next_after(at(2026, 10, 22, 0, 0)) == at(2026, 10, 25, 0, 0)
    # both day fields restricted: either one (cron's rule)
    assert Cron.parse("0 0 13 * fri").next_after(at(2026, 10, 1, 0, 0)) == at(2026, 10, 2, 0, 0)


@pytest.mark.parametrize("bad", ["* * * *", "61 * * * *", "* 24 * * *", "x * * * *", "*/0 * * * *", "5-1 * * * *"])
def test_bad_cron_rules(bad):
    with pytest.raises(PluginError):
        Cron.parse(bad)


def test_every():
    assert every_seconds("30m") == 1800 and every_seconds("6h") == 21600 and every_seconds(120) == 120
    assert every_seconds("1d") == 86400
    for bad in ("10s", "soon", 5, True):
        with pytest.raises(PluginError):
            every_seconds(bad)


def test_make_needs_one_rule():
    with pytest.raises(PluginError, match="one of them"):
        make("x", lambda: None, cron=None, every=None, tz=None, catch_up=True)
    with pytest.raises(PluginError, match="time zone"):
        make("x", lambda: None, cron="0 0 * * *", every=None, tz="Mars/Olympus", catch_up=True)


def test_due_and_catching_up():
    s = make("d", lambda: None, cron="30 2 * * *", every=None, tz="Europe/Rome", catch_up=True)
    now = at(2026, 10, 22, 10, 0)
    assert s.due(now, None) == (True, None)  # the first time: at once
    assert s.due(now, at(2026, 10, 22, 2, 30)) == (False, None)  # ran today
    assert s.due(now, at(2026, 10, 20, 2, 30)) == (True, None)  # missed while away: once
    lazy = make("d", lambda: None, cron="30 2 * * *", every=None, tz="Europe/Rome", catch_up=False)
    assert lazy.due(now, None) == (False, now)
    assert lazy.due(now, at(2026, 10, 20, 2, 30)) == (False, now)  # missed: skipped
    assert lazy.due(at(2026, 10, 23, 2, 31), at(2026, 10, 22, 2, 30)) == (True, None)  # on time
    hourly = make("h", lambda: None, cron=None, every="1h", tz=None, catch_up=True)
    t = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    assert hourly.due(t, t - timedelta(minutes=30)) == (False, None)
    assert hourly.due(t, t - timedelta(minutes=61))[0]


def test_queue_dedupes_by_key(tmp_path):
    q = JobQueue(tmp_path)
    assert q.add("fetch", ["a"], key="k1")
    assert not q.add("fetch", ["b"], key="k1")
    assert q.add("fetch", ["c"])
    assert q.add("fetch", ["d"], delay=3600)
    first = q.next_due()
    assert json.loads(first["args"]) == ["a"]
    q.done(first["id"])
    assert q.add("fetch", ["again"], key="k1")  # done: the key is free again
    q.retry(q.next_due()["id"], "boom", 3600)
    assert [json.loads(j["args"]) for j in q.pending()] == [["again"], ["d"], ["c"]]  # c tried again later


def runner(tmp_path, handlers, schedules=(), faults=None):
    faults = [] if faults is None else faults
    return Runner(
        "demo", tmp_path, logging.getLogger("t"), handlers, list(schedules), lambda w, e: faults.append((w, e))
    )


def test_runner_runs_jobs_and_retries(tmp_path):
    done, faults = [], []
    calls = {"n": 0}

    def flaky(x):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HttpError("offline for a moment")
        done.append(x)

    def broken():
        raise ValueError("bug")

    handlers = {"flaky": Handler(flaky), "broken": Handler(broken, retries=1)}
    r = runner(tmp_path, handlers, faults=faults)
    jobs = Jobs(tmp_path, handlers, lambda: None)
    jobs.enqueue("flaky", 7)
    assert r.run_one()  # fails: retried later, not a fault (the web was unreachable)
    assert not faults and r.queue.pending()[0]["attempts"] == 1
    r.queue.retry(r.queue.pending()[0]["id"], "", 0)  # make it due now
    assert r.run_one() and done == [7] and not r.queue.pending()
    jobs.enqueue("broken")
    r.run_one()
    assert faults and faults[0][0] == "job broken"  # a bug counts against the plugin
    r.queue.retry(r.queue.pending()[0]["id"], "", 0)
    r.run_one()
    assert not r.queue.pending()  # given up after its retries
    with pytest.raises(PluginError, match="No job handler"):
        jobs.enqueue("unknown")
    with pytest.raises(PluginError, match="JSON"):
        jobs.enqueue("flaky", object())


def test_another_process_can_queue_work(tmp_path):
    done = []
    handlers = {"note": Handler(lambda x: done.append(x))}
    r = runner(tmp_path, handlers)
    Jobs(tmp_path, dict(handlers), lambda: None).enqueue("note", "from the MCP server")
    assert r.run_one() and done == ["from the MCP server"]


def test_runner_runs_due_schedules_once(tmp_path):
    ran = []
    s = make("daily", lambda: ran.append(1), cron="0 9 * * *", every=None, tz="UTC", catch_up=True)
    r = runner(tmp_path, {}, [s])
    r.now = lambda: datetime(2026, 10, 22, 10, tzinfo=timezone.utc)
    assert r.run_schedules() == 1 and ran == [1]  # first time: at once
    assert r.run_schedules() == 0  # not again today
    r.now = lambda: datetime(2026, 10, 23, 9, 1, tzinfo=timezone.utc)
    assert r.run_schedules() == 1 and ran == [1, 1]
    # remembered across restarts
    again = runner(tmp_path, {}, [s])
    again.now = r.now
    assert again.run_schedules() == 0


def test_a_failing_schedule_is_a_fault_and_not_retried_at_once(tmp_path):
    faults = []

    def boom():
        raise RuntimeError("x")

    s = make("x", boom, cron=None, every="1h", tz=None, catch_up=True)
    r = runner(tmp_path, {}, [s], faults)
    assert r.run_schedules() == 1 and faults and faults[0][0] == "schedule x"
    assert r.run_schedules() == 0
