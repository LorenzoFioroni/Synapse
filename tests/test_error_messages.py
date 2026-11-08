"""What an error answer may say: the message Synapse's own errors are raised with, never the text
of an unexpected exception (backend/api/__init__.py, ``message_of``)."""

import pytest

from backend.api import GENERIC_FAILURE, message_of
from backend.notes.vault import NoteExists, NoteMoved
from backend.online.http import LookupFailed
from backend.plugins import PluginError
from backend.plugins.resolvers import MetadataSource, lookup


def test_message_of_shows_only_a_single_written_message():
    assert message_of(ValueError("Enter a folder name.")) == "Enter a folder name."
    # an OSError's text names files on the server; an error without a message says nothing useful
    assert message_of(PermissionError(13, "Permission denied", "/srv/synapse/data/x")) == GENERIC_FAILURE
    assert message_of(RuntimeError()) == GENERIC_FAILURE
    assert message_of(RuntimeError("  ")) == GENERIC_FAILURE
    assert message_of(KeyError("a", "b")) == GENERIC_FAILURE


def test_note_errors_carry_their_paths():
    assert NoteExists("papers/a.md").path == "papers/a.md"
    moved = NoteMoved("old.md", "new.md")
    assert (moved.path, moved.to) == ("old.md", "new.md")


def test_note_conflict_answer_names_the_note(client):
    first = client.put("/api/notes/note", json={"path": "x.md", "text": "one", "create": True}).get_json()
    client.put("/api/notes/note", json={"path": "x.md", "text": "two", "hash": first["hash"]})
    r = client.put("/api/notes/note", json={"path": "x.md", "text": "three", "hash": first["hash"]})
    assert r.status_code == 409 and r.get_json()["conflict"] is True
    assert r.get_json()["error"] == "x.md was changed elsewhere"


def _run(pid, what, fn, *args):
    return fn(*args)


def test_a_failing_metadata_source_shows_its_type_not_its_text():
    def broken(query):
        raise KeyError("/srv/synapse/data/plugin-data/x/state.db")

    def readable(query):
        raise PluginError("Crossref did not answer.")

    sources = [("p", MetadataSource("a", "A", broken, frozenset({"doi"}), 1))]
    with pytest.raises(LookupFailed) as caught:
        lookup(sources, _run, {"kind": "doi", "value": "10.1/x"})
    assert str(caught.value) == "p: KeyError"
    sources.append(("q", MetadataSource("b", "B", readable, frozenset({"doi"}), 2)))
    with pytest.raises(LookupFailed) as caught:
        lookup(sources, _run, {"kind": "doi", "value": "10.1/x"})
    assert str(caught.value) == "Crossref did not answer."
