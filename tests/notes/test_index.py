import os
import random

import pytest

from backend.notes import NotesIndex, Vault


@pytest.fixture
def env(tmp_path):
    v = Vault(tmp_path / "notes")
    v.ensure()
    return v, NotesIndex(tmp_path / "idx.db", v)


def put(v, idx, rel, text):
    h = v.current_hash(rel)
    v.write(rel, text, expected_hash=h)
    idx.update(rel)


def test_links_backlinks_citations(env):
    v, idx = env
    put(v, idx, "papers/smith2021.md", "---\npaper: smith2021\n---\n# Smith notes\nSee [[Review]].")
    put(v, idx, "Review.md", "# Review\nUses [@smith2021, p. 3] and [[smith2021]] and [[Missing]].")
    put(v, idx, "other/Review.md", "duplicate name")
    out = idx.outgoing("Review.md")
    assert [(l["kind"], l["target"]) for l in out] == [
        ("cite", "smith2021"),
        ("note", "smith2021"),
        ("note", "Missing"),
    ]
    assert out[1]["resolved"] == "papers/smith2021.md"
    assert out[2]["resolved"] is None
    # [[Review]] is ambiguous; the shortest path wins, so the backlink goes to Review.md only
    assert [b["path"] for b in idx.backlinks("Review.md")] == ["papers/smith2021.md"]
    assert idx.backlinks("other/Review.md") == []
    assert [c["page"] for c in idx.citing("smith2021")] == [3]
    assert [n["path"] for n in idx.paper_notes("smith2021")] == ["papers/smith2021.md"]
    assert idx.note("papers/smith2021.md")["title"] == "Smith notes"
    assert idx.note("papers/smith2021.md")["type"] == "paper"


def test_folder_links(env):
    v, idx = env
    put(v, idx, "a/b/c.md", "x")
    put(v, idx, "z.md", "[[b/c]] [[a/b/c.md]] [[C]]")
    assert [l["resolved"] for l in idx.outgoing("z.md")] == ["a/b/c.md"] * 3
    assert len(idx.backlinks("a/b/c.md")) == 3


def test_sync_picks_up_outside_edits_and_deletions(env):
    v, idx = env
    put(v, idx, "a.md", "[@k1]")
    (v.root / "b.md").write_text("[@k2]")
    (v.root / "a.md").unlink()
    assert idx.sync() == {"added": 1, "changed": 0, "removed": 1}
    assert idx.cited_keys() == ["k2"]


def test_search(env):
    v, idx = env
    put(v, idx, "a.md", "# Entanglement\nArea laws for entropy")
    put(v, idx, "b.md", "nothing")
    assert [r["path"] for r in idx.search("entang")] == ["a.md"]
    assert idx.search('"; DROP') == []


def test_schema_change_rebuilds(env, tmp_path):
    v, idx = env
    put(v, idx, "a.md", "x")
    import sqlite3

    c = sqlite3.connect(idx.db_path)
    c.execute("UPDATE meta SET value = '0' WHERE key = 'schema'")
    c.commit()
    c.close()
    idx2 = NotesIndex(idx.db_path, v)
    assert idx2.stats()["notes"] == 0
    idx2.sync()
    assert idx2.stats()["notes"] == 1


WORDS = ["[[A]]", "[[B|b]]", "[@k1]", "[@k2, p. 3]", "#tag", "`[@code]`", "$x$", "plain", "\n", "\n\n", "# H\n"]


def test_incremental_equals_rebuild(env, tmp_path):
    """Random operations, indexed incrementally, must equal a full rebuild from the files."""
    v, idx = env
    rnd = random.Random(1234)
    names = ["A.md", "B.md", "dir/C.md", "dir/sub/A.md", "D.md"]
    for _ in range(300):
        op = rnd.random()
        name = rnd.choice(names)
        if op < 0.6:
            put(v, idx, name, " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(0, 12))))
        elif op < 0.8 and v.exists(name):
            v.trash(name)
            idx.remove(name)
        elif v.exists(name):
            dst = rnd.choice(names)
            if not v.exists(dst):
                v.move(name, dst)
                idx.remove(name)
                idx.update(dst)
    incremental = idx.snapshot()
    fresh = NotesIndex(tmp_path / "fresh.db", v)
    fresh.sync()
    assert fresh.snapshot() == incremental
    assert idx.rebuild() and idx.snapshot() == incremental


def test_search_snippets_are_escaped(env):
    v, idx = env
    put(v, idx, "x.md", "danger <script>alert(1)</script> entanglement here")
    snip = idx.search("entanglement")[0]["snippet"]
    assert "<script>" not in snip and "&lt;script&gt;" in snip and "<mark>entanglement</mark>" in snip


def test_a_change_while_indexing_is_picked_up_by_the_next_sync(env, monkeypatch):
    v, idx = env
    put(v, idx, "a.md", "[@old]")
    read = v.read

    def read_then_change(rel):  # another program saves the note just after it was read
        out = read(rel)
        (v.root / rel).write_text("[@newer]")
        os.utime(v.root / rel, ns=(10**18, 10**18))
        return out

    (v.root / "a.md").write_text("[@new]")
    os.utime(v.root / "a.md", ns=(9 * 10**17, 9 * 10**17))
    monkeypatch.setattr(v, "read", read_then_change)
    idx.sync()
    monkeypatch.setattr(v, "read", read)
    assert idx.cited_keys() == ["new"]
    idx.sync()
    assert idx.cited_keys() == ["newer"]
