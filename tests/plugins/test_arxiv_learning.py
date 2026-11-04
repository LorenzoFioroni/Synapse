"""Learning the arXiv Today weights (plugins/arxiv-today/backend/learning.py): labels from what the
user did, and a fit that moves toward what they want while staying near the hand-set weights."""

import importlib
import random

learning = importlib.import_module("plugins.arxiv-today.backend.learning")
scoring = importlib.import_module("plugins.arxiv-today.backend.scoring")


def stored(**kw):
    return {"day": "2026-11-04", "dismissed": 0, "opened_at": None, "passed": 0, **kw}


def test_labels():
    assert learning.label(stored(), "2026-11-04T09:00:00Z") == (1.0, learning.ADDED)
    assert learning.label(stored(opened_at="x"), None) == (1.0, learning.OPENED)
    assert learning.label(stored(dismissed=1, opened_at="x"), None) == (0.0, learning.DISMISSED)
    assert learning.label(stored(passed=1), None) == (0.0, learning.PASSED)
    assert learning.label(stored(), None) is None  # never reached, or the day was never opened
    # already in the library before it was announced (a new version of one of your papers): no label
    assert learning.label(stored(), "2026-10-01T09:00:00Z") is None


def test_too_few_labels_learn_nothing():
    samples = [({"anchors": 1.0}, 1.0, 1.0)] * 5 + [({"anchors": 0.0}, 0.0, 0.3)] * 2
    assert learning.fit(samples) is None


def _day_of_reading(n, rng):
    """The user wants papers close in topic and ignores ones that only cite generic library papers."""
    samples = []
    for _ in range(n):
        topic, anchors = rng.random() * 2, rng.random() * 0.6
        wanted = topic > 1.2
        samples.append(({"topic": topic, "anchors": anchors}, 1.0 if wanted else 0.0, 1.0 if wanted else 0.3))
    return samples


def test_the_fit_follows_what_the_user_wants():
    learned = learning.fit(_day_of_reading(300, random.Random(1)))
    assert learned is not None
    assert learned["topic"] > scoring.WEIGHTS["topic"]
    for k, w in learned.items():
        low, high = sorted((0.0, learning.MAX_FACTOR * scoring.WEIGHTS[k]))
        assert low <= w <= high


def test_few_labels_move_the_weights_little():
    few = learning.fit(_day_of_reading(12, random.Random(2)))
    many = learning.fit(_day_of_reading(400, random.Random(2)))
    assert few is not None and many is not None
    assert abs(few["topic"] - scoring.WEIGHTS["topic"]) < abs(many["topic"] - scoring.WEIGHTS["topic"])
    # evidence the labels say nothing about stays where it was
    assert few["own"] == scoring.WEIGHTS["own"] and few["shared"] == scoring.WEIGHTS["shared"]
