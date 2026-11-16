"""The conversations of Ask AI (backend/companion/store.py, ai.db)."""

import sqlite3

import pytest

from backend.companion import store as store_module
from backend.companion.store import Conversations, clean_context


@pytest.fixture
def conversations(tmp_path):
    c = Conversations(tmp_path / "ai.db")
    yield c
    c.close()


def reply(conv="c1", job="j1", provider="claude", companion=("pc", "My PC")):
    return {
        "conversation": conv,
        "job": job,
        "provider": provider,
        "companion": {"id": companion[0], "name": companion[1]},
    }


def ask(c, conv="c1", job="j1", question="What is this?", **kwargs):
    c.record_ask(
        reply(conv, job), question, context=kwargs.get("context"), model=kwargs.get("model"), effort=None, job=job
    )


def answer(c, conv="c1", job="j1", text="It is a note.", state="done", **kwargs):
    c.record_answer(
        {
            "job": job,
            "conversation": conv,
            "state": state,
            "text": text,
            "tools": kwargs.get("tools", []),
            "usage": kwargs.get("usage"),
            "error": kwargs.get("error"),
        }
    )


def test_a_conversation_records_both_sides_and_its_pin(conversations):
    ask(conversations, model="sonnet", context={"kind": "note", "path": "a.md", "selection": "x" * 1000})
    answer(conversations, tools=["get_note"], usage={"input_tokens": 3, "output_tokens": 4})
    conv = conversations.get("c1")
    assert conv["title"] == "What is this?" and conv["provider"] == "claude" and conv["model"] == "sonnet"
    assert conv["companion"] == {"id": "pc", "name": "My PC"}
    assert conv["context"] == {"kind": "note", "path": "a.md", "selection": "x" * store_module.CONTEXT_TEXT}
    user, assistant = conv["messages"]
    assert (user["role"], user["text"]) == ("user", "What is this?")
    assert assistant["text"] == "It is a note." and assistant["tools"] == ["get_note"]
    assert assistant["usage"] == {"input_tokens": 3, "output_tokens": 4} and assistant["state"] == "done"
    assert "error" not in assistant


def test_a_follow_up_keeps_provider_and_computer_but_follows_model_and_effort(conversations):
    ask(conversations, model="sonnet")
    conversations.record_ask(
        reply(provider="codex", companion=("other", "Other")),
        "More",
        context=None,
        model="opus",
        effort="low",
        job="j2",
    )
    conv = conversations.get("c1")
    assert (conv["provider"], conv["companion"]["id"]) == ("claude", "pc")
    assert (conv["model"], conv["effort"], conv["title"]) == ("opus", "low", "What is this?")
    assert [m["text"] for m in conv["messages"]] == ["What is this?", "More"]


def test_answers_of_a_cancelled_or_failed_job_are_kept_with_what_arrived(conversations):
    ask(conversations)
    answer(conversations, text="part", state="cancelled")
    ask(conversations, job="j2", question="again")
    answer(conversations, job="j2", text="", state="error", error="Not logged in")
    states = [(m.get("state"), m.get("error")) for m in conversations.get("c1")["messages"] if m["role"] == "assistant"]
    assert states == [("cancelled", None), ("error", "Not logged in")]
    answer(conversations, conv="unknown")  # an answer to a conversation that is gone is dropped
    assert conversations.get("unknown") is None


def test_the_newest_conversations_are_kept(conversations, monkeypatch):
    monkeypatch.setattr(store_module, "MAX_CONVERSATIONS", 3)
    for i in range(5):
        ask(conversations, conv=f"c{i}", job=f"j{i}", question=f"q{i}")
    assert [c["id"] for c in conversations.list()] == ["c4", "c3", "c2"]
    assert conversations.get("c0") is None
    with sqlite3.connect(conversations.path) as db:  # the messages of dropped ones are gone too
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3


def test_messages_per_conversation_are_bounded(conversations, monkeypatch):
    monkeypatch.setattr(store_module, "MAX_MESSAGES", 4)
    for i in range(5):
        ask(conversations, job=f"j{i}", question=f"q{i}")
        answer(conversations, job=f"j{i}", text=f"a{i}")
    assert [m["text"] for m in conversations.get("c1")["messages"]] == ["q3", "a3", "q4", "a4"]


def test_conversations_survive_a_restart(tmp_path):
    first = Conversations(tmp_path / "ai.db")
    ask(first)
    answer(first)
    first.close()
    again = Conversations(tmp_path / "ai.db")
    try:
        assert [m["role"] for m in again.get("c1")["messages"]] == ["user", "assistant"]
        assert again.list()[0]["id"] == "c1"
    finally:
        again.close()


def test_list_is_newest_first_searchable_and_limited(conversations):
    for i, text in enumerate(["alpha topic", "beta 100% sure", "gamma_x"]):
        ask(conversations, conv=f"c{i}", job=f"j{i}", question=text)
    answer(conversations, conv="c0", job="j0", text="mentions zebra here")
    assert [c["id"] for c in conversations.list()] == ["c0", "c2", "c1"]  # the answer made c0 the newest
    assert [c["id"] for c in conversations.list(limit=2)] == ["c0", "c2"]
    assert [c["id"] for c in conversations.list(query="zebra")] == ["c0"]
    assert [c["id"] for c in conversations.list(query="BETA")] == ["c1"]
    assert [c["id"] for c in conversations.list(query="100%")] == ["c1"]  # % and _ are plain characters
    assert [c["id"] for c in conversations.list(query="_x")] == ["c2"]
    assert conversations.list(query="nothing like it") == []


def test_rename_and_delete(conversations):
    ask(conversations)
    assert conversations.rename("c1", "  A   better title ") and conversations.get("c1")["title"] == "A better title"
    assert conversations.rename("c1", "x" * 500) and len(conversations.get("c1")["title"]) == store_module.TITLE_MAX
    with pytest.raises(ValueError):
        conversations.rename("c1", "   ")
    assert not conversations.rename("nope", "x")
    answer(conversations)
    assert conversations.delete("c1") and conversations.get("c1") is None and not conversations.delete("c1")
    with sqlite3.connect(conversations.path) as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_only_where_a_question_came_from_is_kept():
    assert clean_context(None) is None and clean_context("note") is None and clean_context({"kind": "x"}) is None
    assert clean_context(
        {"kind": "paper", "item": "i1", "page": 3, "attachment": 9, "quote": "q" * 999, "extra": 1}
    ) == {
        "kind": "paper",
        "item": "i1",
        "page": 3,
        "attachment": 9,
        "quote": "q" * store_module.CONTEXT_TEXT,
    }
