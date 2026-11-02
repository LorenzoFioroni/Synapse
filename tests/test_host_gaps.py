"""Service spacing must follow actual wake-ups, including delayed or early sleeps."""

from types import SimpleNamespace

import pytest

from backend.online import http


@pytest.mark.parametrize("first_sleep", [0.25, 1.25])
def test_spacing_follows_actual_wake_time(monkeypatch, first_sleep):
    now = 10.0
    sleeps = []

    def sleep(delay):
        nonlocal now
        sleeps.append(delay)
        now += first_sleep if len(sleeps) == 1 else delay

    monkeypatch.setattr(http, "time", SimpleNamespace(monotonic=lambda: now, sleep=sleep))
    gaps = http.HostGaps()
    stamps = []
    for _ in range(3):
        gaps.wait("arxiv.org", 1.0)
        stamps.append(now)
    assert all(b - a >= 1.0 for a, b in zip(stamps, stamps[1:]))
    assert sleeps[0] == 1.0


def test_waiters_recheck_after_another_caller_takes_a_turn(monkeypatch):
    now = 10.0
    competitor = []
    gaps = http.HostGaps()

    def sleep(delay):
        nonlocal now
        now += delay
        if not competitor:
            # Another caller wakes first and claims this host's next turn.
            competitor.append(now)
            gaps.wait("arxiv.org", 1.0)
        # A different service can proceed while this host is waiting.
        gaps.wait("example.org", 0.0)

    monkeypatch.setattr(http, "time", SimpleNamespace(monotonic=lambda: now, sleep=sleep))
    gaps.wait("arxiv.org", 1.0)
    gaps.wait("arxiv.org", 1.0)
    assert now - competitor[0] >= 1.0
