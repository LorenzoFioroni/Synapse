import pytest

from backend.notes.vault import Conflict, InvalidPath, NoteExists, NoteMissing, Vault, content_hash, slugify


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "notes")
    v.ensure()
    return v


@pytest.mark.parametrize(
    "bad",
    [
        "../x.md",
        "a/../../x.md",
        "/etc/x.md",
        "C:/x.md",
        ".trash/x.md",
        "a/.hidden/x.md",
        "x.txt",
        "",
        "  ",
        "a\x00.md",
        "a/b:c.md",
        "..\\x.md",
    ],
)
def test_rejects_unsafe_paths(vault, bad):
    with pytest.raises(InvalidPath):
        vault.clean_rel(bad)


def test_symlink_escape_is_rejected(vault, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault.root / "link").symlink_to(outside)
    with pytest.raises(InvalidPath):
        vault.clean_rel("link/x.md")


def test_normalises(vault):
    assert vault.clean_rel("./a//b/./c.md") == "a/b/c.md"
    assert vault.clean_rel("a\\b.md") == "a/b.md"


def test_create_save_conflict(vault):
    h1 = vault.write("a.md", "one", expected_hash=None)
    with pytest.raises(NoteExists):
        vault.write("a.md", "again", expected_hash=None)
    h2 = vault.write("a.md", "two", expected_hash=h1)
    with pytest.raises(Conflict) as e:
        vault.write("a.md", "stale", expected_hash=h1)
    assert e.value.current_hash == h2
    assert vault.read("a.md") == ("two", h2)
    assert h2 == content_hash("two")
    assert not list(vault.root.glob(".*.tmp"))


def test_trash_and_restore(vault):
    vault.write("dir/a.md", "x", expected_hash=None)
    tid = vault.trash("dir/a.md")
    assert not vault.exists("dir/a.md")
    assert vault.trashed() == [{"trash_id": tid, "path": "dir/a.md"}]
    assert [r for r, _ in vault.iter_notes()] == []
    vault.write("dir/a.md", "new", expected_hash=None)
    with pytest.raises(NoteExists):
        vault.restore(tid, "dir/a.md")
    vault.trash("dir/a.md")
    assert vault.restore(tid, "dir/a.md") == "dir/a.md"
    assert vault.read("dir/a.md")[0] == "x"
    with pytest.raises(InvalidPath):
        vault.restore("../..", "dir/a.md")


def test_move(vault):
    vault.write("a.md", "x", expected_hash=None)
    vault.write("b.md", "y", expected_hash=None)
    with pytest.raises(NoteExists):
        vault.move("a.md", "b.md")
    assert vault.move("a.md", "sub/c.md") == "sub/c.md"
    with pytest.raises(NoteMissing):
        vault.move("a.md", "d.md")


def test_iter_skips_hidden_and_attachments(vault):
    vault.write("n.md", "x", expected_hash=None)
    (vault.root / "attachments" / "img.md").write_text("not a note")
    (vault.root / ".obsidian").mkdir()
    (vault.root / ".obsidian" / "x.md").write_text("no")
    assert [r for r, _ in vault.iter_notes()] == ["n.md"]


def test_attachment_names_never_overwrite(vault):
    a = vault.write_bytes("attachments/p.png", b"1")
    b = vault.write_bytes("attachments/p.png", b"2")
    assert (a, b) == ("attachments/p.png", "attachments/p-2.png")


def test_slugify():
    assert slugify('A: "title" / with?*') == "A title - with"
    assert slugify("  ") == "untitled"
    assert slugify("cui:2015var") == "cui2015var"


def test_trash_batch_and_restore_together(vault):
    for rel in ["a/one.md", "a/sub/two.md", "b/three.md", "four.md"]:
        vault.write(rel, rel, expected_hash=None)
    (vault.root / "a" / "sub" / "fig.txt").write_text("kept with the folder")
    (vault.root / "empty").mkdir()
    # a note inside a deleted folder is not deleted twice; a child folder is covered by its parent
    tid, moved = vault.trash_batch(["four.md", "a/one.md"], ["a", "a/sub", "empty"])
    assert moved == ["a/one.md", "a/sub/two.md", "four.md"]
    assert not (vault.root / "a").exists() and not (vault.root / "empty").exists()
    assert vault.exists("b/three.md")
    vault.write("four.md", "a new four", expected_hash=None)  # the place is taken again
    restored, skipped = vault.restore_batch(tid)
    assert restored == ["a/one.md", "a/sub/two.md"] and skipped == ["four.md"]
    assert (vault.root / "a" / "sub" / "fig.txt").read_text() == "kept with the folder"
    assert (vault.root / "empty").is_dir()
    assert vault.read("four.md")[0] == "a new four"  # never overwritten
    assert [t["path"] for t in vault.trashed()] == ["four.md"]  # the skipped one stays in the trash


def test_trash_batch_validates_everything_first(vault):
    vault.write("x.md", "x", expected_hash=None)
    with pytest.raises(NoteMissing):
        vault.trash_batch(["x.md", "missing.md"], [])
    assert vault.exists("x.md")  # nothing moved
    for bad in (["attachments"], [".trash"], ["../x"]):
        with pytest.raises((InvalidPath, NoteMissing)):
            vault.trash_batch([], bad)
    with pytest.raises(InvalidPath):
        vault.trash_batch([], [])


def test_move_folder(vault):
    vault.write("proj/a.md", "a", expected_hash=None)
    vault.write("proj/deep/b.md", "b", expected_hash=None)
    vault.write("other/c.md", "c", expected_hash=None)
    assert vault.move_folder("proj", "archive/proj2") == [
        ("proj/a.md", "archive/proj2/a.md"),
        ("proj/deep/b.md", "archive/proj2/deep/b.md"),
    ]
    assert vault.read("archive/proj2/deep/b.md")[0] == "b" and not (vault.root / "proj").exists()
    with pytest.raises(NoteExists):
        vault.move_folder("archive/proj2", "other")
    with pytest.raises(InvalidPath):
        vault.move_folder("archive", "archive/proj2/inside")
    with pytest.raises(InvalidPath):
        vault.move_folder("other", "attachments/x")
    with pytest.raises(NoteMissing):
        vault.move_folder("nope", "x")


def test_symlinks_and_collisions_do_not_break_folder_operations(vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("not ours")
    vault.write("A/n.md", "n", expected_hash=None)
    (vault.root / "A" / "link.md").symlink_to(outside)
    assert vault.notes_in("A") == ["A/n.md"]  # like iter_notes: links are not notes
    assert vault.move_folder("A", "B") == [("A/n.md", "B/n.md")]
    tid, moved = vault.trash_batch([], ["B"])
    assert moved == ["B/n.md"]
    restored, skipped = vault.restore_batch(tid)
    assert restored == ["B/n.md"] and skipped == [] and (vault.root / "B" / "link.md").is_symlink()
    assert outside.read_text() == "not ours"

    vault.write("C/D/y.md", "y", expected_hash=None)
    tid, _ = vault.trash_batch([], ["C"])
    (vault.root / "C").mkdir()
    (vault.root / "C" / "D").write_text("a file where the folder was")
    restored, skipped = vault.restore_batch(tid)  # no crash: that part stays in the trash
    assert restored == [] and skipped == ["C/D/y.md"]
    assert [t["path"] for t in vault.trashed()] == ["C/D/y.md"]


def test_reserved_folder_any_case(vault):
    with pytest.raises(InvalidPath):
        vault.make_folder("Attachments/x")
    vault.write("x/a.md", "a", expected_hash=None)
    with pytest.raises(InvalidPath):
        vault.move_folder("x", "ATTACHMENTS")
