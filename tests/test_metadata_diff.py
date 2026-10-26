"""Tests that Python compute_changes produces identical diffs to the shared fixtures."""

import json
from pathlib import Path

import pytest

from backend.online.metadata import compute_changes

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "metadata_diff_cases.json"


def load_cases():
    with open(FIXTURES_PATH) as f:
        return json.load(f)


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["name"])
def test_compute_changes_parity(case):
    current = case["current"]
    fetched = case["fetched"]
    expected = case["expected"]

    changes = compute_changes(current, fetched)

    assert len(changes) == len(expected), f"{case['name']}: count mismatch {len(changes)} vs {len(expected)}"
    for actual_change, expected_change in zip(changes, expected):
        assert actual_change["key"] == expected_change["key"]
        assert str(actual_change["fetched"]) == str(expected_change["fetched"])
        assert actual_change["isNew"] == expected_change["isNew"]
        assert actual_change["checked"] == expected_change["checked"]
        if expected_change["current"] is None:
            assert actual_change["current"] is None
        else:
            assert str(actual_change["current"]) == str(expected_change["current"])
