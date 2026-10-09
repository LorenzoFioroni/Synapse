import pytest

from backend.notes.vault import Conflict, InvalidPath, NoteExists, NoteMissing, Vault, content_hash, slugify


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "notes")
    v.ensure()
    return v


@pytest.mark.parametrize("bad", ["../x.md", "a/../../x.md", "/etc/x.md", "C:/x.md", ".trash/x.md",
                                 "a/.hidden/x.md", "x.txt", "", "  ", "a\x00.md", "a/b:c.md", "..\\x.md"])
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
