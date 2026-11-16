"""The companion rendezvous: hand-out, events, cancellation, expiry, bounds (backend/companion/hub.py)."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.companion import hub
from backend.companion.hub import Companions

CLAUDE = [{"id": "claude", "label": "Claude Code"}]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def companions(clock):
    return Companions(clock=clock)


def connect(c, client="pc", providers=CLAUDE, running=()):
    return c.poll(client, "My PC", providers, list(running), timeout=0)


def test_job_goes_to_one_connected_companion_only_once(companions):
    connect(companions, "a")
    connect(companions, "b")
    reply = companions.ask("question", "system", provider="claude")
    owner = reply["companion"]["id"]
    other = "b" if owner == "a" else "a"
    assert connect(companions, other)["job"] is None
    job = connect(companions, owner)["job"]
    assert job["prompt"] == "question" and job["system"] == "system" and job["provider"] == "claude"
    assert job["resume"] is False and job["conversation"] == reply["conversation"] and job["id"] == reply["job"]
    assert connect(companions, owner, running=[job["id"]])["job"] is None  # not handed out twice


def test_long_poll_wakes_when_a_question_arrives(companions):
    with ThreadPoolExecutor(2) as pool:
        waiting = pool.submit(companions.poll, "pc", "PC", CLAUDE, [], timeout=5)
        for _ in range(100):
            if companions.status():
                break
            pool.submit(lambda: None).result()
        companions.ask("hi", "s")
        assert waiting.result(timeout=5)["job"]["prompt"] == "hi"


def test_asking_needs_a_connected_companion_with_the_provider(companions, clock):
    with pytest.raises(hub.NoCompanion):
        companions.ask("q", "s")
    connect(companions)
    with pytest.raises(hub.NoCompanion, match="codex"):
        companions.ask("q", "s", provider="codex")
    with pytest.raises(hub.NoCompanion):
        companions.ask("q", "s", companion="someone-else")
    assert companions.ask("q", "s", provider="claude", companion="pc")["provider"] == "claude"
    # connected only while polling or shortly after
    clock.now += hub.GRACE_SECONDS + 1
    assert companions.status() == []
    with pytest.raises(hub.NoCompanion):
        companions.ask("q", "s")


def test_least_busy_companion_is_chosen(companions):
    connect(companions, "a")
    connect(companions, "b")
    first = companions.ask("1", "s")["companion"]["id"]
    second = companions.ask("2", "s")["companion"]["id"]
    assert first != second
    assert {c["id"]: c["busy"] for c in companions.status()} == {"a": 1, "b": 1}


def test_events_stream_to_the_watcher_and_end_the_job(companions):
    connect(companions)
    reply = companions.ask("q", "s")
    job = connect(companions)["job"]["id"]
    assert companions.watch(reply["job"], 0, timeout=0) == {"events": [], "next": 0, "state": "running"}
    assert (
        companions.post_events("pc", job, [{"type": "tool", "name": "get_note"}, {"type": "text", "text": "He"}])
        is False
    )
    companions.post_events("pc", job, [{"type": "text", "text": "llo"}, {"type": "bogus"}, {"type": "done"}])
    seen = companions.watch(job, 0, timeout=0)
    assert seen["state"] == "done"
    assert seen["events"] == [
        {"type": "tool", "name": "get_note"},
        {"type": "text", "text": "He"},
        {"type": "text", "text": "llo"},
        {"type": "done"},
    ]
    assert companions.watch(job, seen["next"], timeout=0)["events"] == []
    companions.post_events("pc", job, [{"type": "text", "text": "late"}])  # nothing after the end
    assert companions.watch(job, 0, timeout=0)["next"] == seen["next"]


def test_watch_waits_for_new_events(companions):
    connect(companions)
    job = companions.ask("q", "s")["job"]
    connect(companions)
    with ThreadPoolExecutor(1) as pool:
        waiting = pool.submit(companions.watch, job, 0, timeout=5)
        companions.post_events("pc", job, [{"type": "text", "text": "x"}])
        assert waiting.result(timeout=5)["events"] == [{"type": "text", "text": "x"}]


def test_only_the_companion_with_the_job_may_post(companions):
    connect(companions, "a")
    connect(companions, "b")
    owner = companions.ask("q", "s")["companion"]["id"]
    other = "b" if owner == "a" else "a"
    job = connect(companions, owner)["job"]["id"]
    with pytest.raises(hub.WrongCompanion):
        companions.post_events(other, job, [{"type": "done"}])
    with pytest.raises(hub.UnknownJob):
        companions.post_events(owner, "nope", [])
    with pytest.raises(hub.UnknownJob):
        companions.watch("nope")


def test_cancel_reaches_the_companion_by_poll_and_by_events(companions):
    connect(companions)
    reply = companions.ask("q", "s")
    job = connect(companions)["job"]["id"]
    assert companions.cancel(reply["job"]) == "cancelled"
    assert companions.watch(job, 0, timeout=0)["state"] == "cancelled"
    assert connect(companions, running=[job])["cancelled"] == [job]
    assert companions.post_events("pc", job, [{"type": "text", "text": "x"}]) is True
    assert connect(companions)["cancelled"] == []  # no longer running there


def test_cancel_wakes_a_waiting_companion(companions):
    connect(companions)
    reply = companions.ask("q", "s")
    job = connect(companions)["job"]["id"]
    with ThreadPoolExecutor(1) as pool:
        waiting = pool.submit(companions.poll, "pc", "PC", CLAUDE, [job], timeout=5)
        companions.cancel(reply["job"])
        assert waiting.result(timeout=5) == {"job": None, "cancelled": [job]}


def test_cancelling_a_queued_job_means_it_is_never_handed_out(companions):
    connect(companions)
    job = companions.ask("q", "s")["job"]
    companions.cancel(job)
    assert connect(companions)["job"] is None


def test_unclaimed_job_ends_with_an_error(companions, clock):
    connect(companions)
    job = companions.ask("q", "s")["job"]
    clock.now += hub.PICKUP_SECONDS + 1
    seen = companions.watch(job, 0, timeout=0)
    assert seen["state"] == "error" and "picked up" in seen["events"][-1]["message"]


def test_job_of_a_vanished_companion_ends_with_an_error(companions, clock):
    connect(companions)
    companions.ask("q", "s")
    job = connect(companions)["job"]["id"]
    clock.now += hub.SILENCE_SECONDS - 5
    companions.post_events("pc", job, [{"type": "text", "text": "x"}])  # still alive
    clock.now += hub.SILENCE_SECONDS - 5
    assert companions.watch(job, 0, timeout=0)["state"] == "running"
    clock.now += 10
    seen = companions.watch(job, 0, timeout=0)
    assert seen["state"] == "error" and "stopped responding" in seen["events"][-1]["message"]
    assert companions.post_events("pc", job, [{"type": "done"}]) is True  # told to stop


def test_finished_jobs_are_kept_for_a_while_then_forgotten(companions, clock, monkeypatch):
    connect(companions)
    first = companions.ask("q", "s")["job"]
    companions.cancel(first)
    clock.now += hub.KEEP_SECONDS - 1
    assert companions.watch(first, 0, timeout=0)["state"] == "cancelled"
    clock.now += 2
    with pytest.raises(hub.UnknownJob):
        companions.watch(first, 0, timeout=0)
    monkeypatch.setattr(hub, "MAX_FINISHED", 2)
    jobs = []
    for _ in range(4):
        connect(companions)
        jobs.append(companions.ask("q", "s")["job"])
        companions.cancel(jobs[-1])
        clock.now += 1
    with pytest.raises(hub.UnknownJob):
        companions.watch(jobs[0], 0, timeout=0)
    assert companions.watch(jobs[-1], 0, timeout=0)["state"] == "cancelled"


def test_conversation_is_pinned_to_its_companion_and_provider(companions, clock):
    connect(companions, "a", providers=CLAUDE + [{"id": "codex", "label": "Codex"}])
    connect(companions, "b")
    first = companions.ask("1", "s", provider="codex")
    job = connect(companions, "a")["job"]["id"]
    with pytest.raises(hub.TooBusy):  # still answering
        companions.ask("2", "s", conversation=first["conversation"])
    companions.post_events("a", job, [{"type": "done"}])
    follow = companions.ask("2", "s", conversation=first["conversation"], provider="claude")
    assert (follow["conversation"], follow["provider"], follow["companion"]["id"]) == (
        first["conversation"],
        "codex",
        "a",
    )
    assert connect(companions, "a")["job"]["resume"] is True
    with pytest.raises(hub.NoCompanion, match="new conversation"):
        companions.ask("3", "s", conversation="unknown")
    # the pinned companion going away ends the conversation
    clock.now += hub.GRACE_SECONDS + 1
    connect(companions, "b")
    with pytest.raises(hub.NoCompanion, match="new conversation"):
        companions.ask("4", "s", conversation=first["conversation"])


def test_bounds_on_companions_events_and_jobs(companions, monkeypatch):
    monkeypatch.setattr(hub, "MAX_COMPANIONS", 2)
    connect(companions, "a")
    connect(companions, "b")
    with pytest.raises(hub.CompanionLimit):
        connect(companions, "c")
    for bad in ("", "x y", "a" * 129, None):
        with pytest.raises(hub.InvalidCompanion):
            companions.poll(bad, "n", CLAUDE, [], timeout=0)
    with pytest.raises(hub.InvalidCompanion):
        companions.poll("a", "n", [{"id": "Bad Id"}], [], timeout=0)
    owner = companions.ask("q", "s")["companion"]["id"]
    job = connect(companions, owner)["job"]["id"]
    with pytest.raises(hub.InvalidEvents):
        companions.post_events(owner, job, [{"type": "tool", "name": "x"}] * (hub.MAX_BATCH_EVENTS + 1))
    with pytest.raises(hub.InvalidEvents):
        companions.post_events(owner, job, [{"type": "text", "text": "x" * (hub.MAX_BATCH_BYTES + 1)}])


def test_a_job_with_too_much_text_ends_with_an_error(companions, monkeypatch):
    monkeypatch.setattr(hub, "MAX_JOB_TEXT", 10)
    connect(companions)
    companions.ask("q", "s")
    job = connect(companions)["job"]["id"]
    assert companions.post_events("pc", job, [{"type": "text", "text": "x" * 11}]) is True
    seen = companions.watch(job, 0, timeout=0)
    assert seen["state"] == "error" and "too long" in seen["events"][-1]["message"]


def test_open_questions_are_limited(companions, monkeypatch):
    monkeypatch.setattr(hub, "MAX_ACTIVE", 2)
    connect(companions)
    companions.ask("1", "s")
    companions.ask("2", "s")
    with pytest.raises(hub.TooBusy):
        companions.ask("3", "s")


def test_companion_runs_only_two_jobs_at_once(companions):
    connect(companions)
    for i in range(3):
        companions.ask(str(i), "s")
    running = []
    for _ in range(2):
        running.append(connect(companions, running=running)["job"]["id"])
    assert connect(companions, running=running)["job"] is None
    companions.post_events("pc", running[0], [{"type": "done"}])
    assert connect(companions, running=running[1:])["job"]["prompt"] == "2"


def test_instances_do_not_share_state(companions):
    connect(companions)
    other = Companions()
    assert other.status() == []
    with pytest.raises(hub.NoCompanion):
        other.ask("q", "s")


# ---------------------------------------------------------------------- fix round


def test_a_busy_companion_is_named_as_the_reason(companions, clock):
    connect(companions)
    for i in range(3):
        companions.ask(str(i), "s")
    running = [connect(companions, running=[])["job"]["id"], None]
    running[1] = connect(companions, running=running[:1])["job"]["id"]
    third = [j for j in companions._jobs.values() if j.state == "queued"][0].id
    clock.now += hub.PICKUP_SECONDS + 1
    connect(companions, running=running)
    seen = companions.watch(third, 0, timeout=0)
    assert seen["state"] == "error" and seen["events"][-1]["message"] == "The companion is busy with other questions."


def test_a_companion_id_belongs_to_one_token(companions):
    companions.poll("pc", "PC", CLAUDE, [], token="t1", timeout=0)
    with pytest.raises(hub.WrongCompanion):
        companions.poll("pc", "PC", CLAUDE, [], token="t2", timeout=0)
    owner = companions.ask("q", "s")["job"]
    companions.poll("pc", "PC", CLAUDE, [], token="t1", timeout=0)
    with pytest.raises(hub.WrongCompanion):
        companions.post_events("pc", owner, [{"type": "done"}], token="t2")
    assert companions.post_events("pc", owner, [{"type": "done"}], token="t1") is False


def test_companions_per_token_are_capped(companions):
    for i in range(hub.MAX_PER_TOKEN):
        companions.poll(f"a{i}", "PC", CLAUDE, [], token="t1", timeout=0)
    with pytest.raises(hub.CompanionLimit):
        companions.poll("a-more", "PC", CLAUDE, [], token="t1", timeout=0)
    companions.poll("b0", "PC", CLAUDE, [], token="t2", timeout=0)  # other tokens still fit


def test_a_job_has_an_overall_time_limit(companions, clock):
    connect(companions)
    companions.ask("q", "s")
    job = connect(companions)["job"]["id"]
    for _ in range(19):  # a companion that keeps polling and posting never finishes it
        clock.now += 50
        connect(companions, running=[job])
        companions.post_events("pc", job, [{"type": "tool", "name": "x"}])
    seen = companions.watch(job, 0, timeout=0)
    assert seen["state"] == "error" and "took too long" in seen["events"][-1]["message"]
