"""Bounded context assembly at the model boundary; old clients still send prefix/suffix only."""

import pytest

from backend.config import APP_ROOT
from backend.plugins.loading import load_module


@pytest.fixture(scope="module")
def prompt():
    module, _ = load_module("autocompletion", APP_ROOT / "plugins/autocompletion/server")
    return module.prompt


def build(prompt, prefix, context=None, fim=True):
    return prompt.build("m", prefix, "$.", fim=fim, thinking=False, length="line", context=context)


@pytest.mark.parametrize("fim", [True, False])
def test_preserves_selected_definition_and_cursor_tail_within_the_original_budget(prompt, fim):
    prefix = "Recent numerical protocol.\n" * 300 + "The reset parameter is $"
    context = {"title": "Cooling", "headings": ["## Results"], "excerpts": [r"We denote the reset by $\zeta$. "]}
    p = build(prompt, prefix, context, fim)
    assert len(p["prompt"]) <= 4000
    assert r"We denote the reset by $\zeta$." in p["prompt"]
    assert "# Cooling" in p["prompt"] and "## Results" in p["prompt"]
    assert p["prompt"].endswith("The reset parameter is $")
    assert p["options"] == build(prompt, prefix, None, fim)["options"]
    assert p.get("suffix") == ("$." if fim else None)


def test_short_notes_and_legacy_clients_keep_their_original_prompt(prompt):
    prefix = "# Cooling\n\nWe define the reset.\n\nThe reset is $"
    context = {"title": "Cooling", "headings": [], "excerpts": ["We define the reset."]}
    assert build(prompt, prefix, context)["prompt"] == prefix
    assert build(prompt, prefix)["prompt"] == prefix


@pytest.mark.parametrize("context", [None, "bad", [], 3, {"title": [], "headings": "bad", "excerpts": [None, 8]}])
def test_malformed_optional_context_falls_back_to_nearby_text(prompt, context):
    prefix = "Recent.\n" * 1000 + "We compute "
    assert build(prompt, prefix, context)["prompt"] == build(prompt, prefix)["prompt"]


def test_oversized_snippets_are_ignored_and_excess_context_is_bounded(prompt):
    prefix = "Recent.\n" * 1000 + "We compute "
    context = {"title": "x" * 500, "headings": ["y" * 500], "excerpts": ["z" * 500]}
    assert build(prompt, prefix, context)["prompt"] == build(prompt, prefix)["prompt"]
    context = {
        "title": "Topic",
        "headings": ["## " + "h" * 150] * 20,
        "excerpts": [str(i) + "x" * 400 for i in range(20)],
    }
    result = build(prompt, prefix, context)["prompt"]
    assert len(result) <= 4000
    assert result.endswith("We compute ")
    assert len(result.split("<!-- Earlier sections omitted. -->")[0]) <= 1200


def test_header_never_discards_the_end_of_a_long_current_line(prompt):
    prefix = "x" * 6000 + "the cursor is here"
    c = {"title": "Topic", "headings": [], "excerpts": ["We define a variable."]}
    result = build(prompt, prefix, c)["prompt"]
    assert len(result) <= 4000 and result.endswith(prefix[-2800:])


def test_metadata_does_not_change_output_cleanup_at_the_real_cursor(prompt):
    prefix, suffix = "Energy $E = ", "$."
    assert prompt.clean("mc^2$. More", prefix, suffix, "line") == "mc^2"
