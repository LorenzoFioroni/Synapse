"""The full-text index and its background indexer (without real PDFs: extraction is faked)."""

import sqlite3

import pytest

from backend.pdf import fulltext
from backend.pdf.fulltext import FulltextIndex, Indexer


@pytest.fixture
def texts(monkeypatch):
    """file name -> text that "extracting" it gives (a missing name raises, like a broken PDF)."""
    out: dict[str, str] = {}

    def extract(path, max_pages=None):
        if path.name not in out:
            raise ValueError("broken PDF")
        return out[path.name], 1

    monkeypatch.setattr(fulltext, "extract_text", extract)
    return out


class Repo:
    def __init__(self, folder):
        self.folder = folder
        self.atts: list[dict] = []

    def add(self, att_id, name):
        (self.folder / name).write_bytes(b"%PDF")
        self.atts.append({"id": att_id, "paper_id": f"p{att_id}", "name": name, "url": name})

    def all_attachments(self):
        return self.atts

    def attachment_path(self, a):
        return self.folder / a["url"]


def test_errors_count_only_the_last_run(tmp_path, texts):
    repo = Repo(tmp_path)
    repo.add(1, "good.pdf")
    repo.add(2, "bad.pdf")
    texts["good.pdf"] = "fine"
    ix = Indexer(repo, FulltextIndex(tmp_path / "ft.db"))
    ix.sync(background=False)
    assert ix.status["errors"] == 1
    ix.sync(background=False)  # the broken file is tried again, and fails again
    assert ix.status["errors"] == 1
    texts["bad.pdf"] = "repaired"
    ix.sync(background=False)
    assert ix.status["errors"] == 0 and ix.index.indexed_ids() == {1, 2}


def no_fts5(monkeypatch):
    def fail(c):
        raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(FulltextIndex, "_create_fts", staticmethod(fail))


def indexed(tmp_path, texts):
    repo = Repo(tmp_path)
    repo.add(1, "a.pdf")
    texts["a.pdf"] = "Lindblad master equation"
    ix = Indexer(repo, FulltextIndex(tmp_path / "ft.db"))
    ix.sync(background=False)
    return repo


@pytest.mark.parametrize("fts_first", [True, False])
def test_a_change_of_fts5_availability_reindexes_everything(tmp_path, texts, monkeypatch, fts_first):
    if not fts_first:
        no_fts5(monkeypatch)
    repo = indexed(tmp_path, texts)
    assert FulltextIndex(tmp_path / "ft.db").match("lindblad").keys() == {"p1"}

    if fts_first:  # the next start has no FTS5 …
        no_fts5(monkeypatch)
    else:  # … or has it again
        monkeypatch.undo()
        monkeypatch.setattr(fulltext, "extract_text", lambda path, max_pages=None: (texts[path.name], 1))
    index = FulltextIndex(tmp_path / "ft.db")
    assert index.fts is not fts_first
    assert index.indexed_ids() == set()  # nothing counts as indexed in the table now in use
    Indexer(repo, index).sync(background=False)
    assert index.match("lindblad").keys() == {"p1"}
    assert FulltextIndex(tmp_path / "ft.db").indexed_ids() == {1}  # an unchanged start keeps it


def test_an_index_from_before_the_marker_is_checked_too(tmp_path, texts, monkeypatch):
    indexed(tmp_path, texts)  # with FTS5
    with sqlite3.connect(tmp_path / "ft.db") as c:
        c.execute("DROP TABLE meta")
    no_fts5(monkeypatch)
    assert FulltextIndex(tmp_path / "ft.db").indexed_ids() == set()


def test_like_fallback_takes_percent_and_underscore_literally(tmp_path, texts, monkeypatch):
    no_fts5(monkeypatch)
    repo = Repo(tmp_path)
    for i, text in enumerate(["a 100% yield", "a 1000 yield", "uses snake_case", "uses snakeXcase", "C:\\temp"], 1):
        repo.add(i, f"{i}.pdf")
        texts[f"{i}.pdf"] = text
    index = FulltextIndex(tmp_path / "ft.db")
    Indexer(repo, index).sync(background=False)
    assert not index.fts
    assert index.match("100%").keys() == {"p1"}
    assert index.match("snake_case").keys() == {"p3"}
    assert index.match("C:\\temp").keys() == {"p5"}
    assert "<mark>100%</mark>" in index.match("100%")["p1"]


def test_math_font_glyphs_are_turned_back_into_symbols():
    from backend.pdf.text import clean_text

    garbled = "The dynamics along ε¼0 is given by ðt ÞÞ and ðaÞ, ðbÞ, ðcÞ, so ðx þ yÞ."
    assert clean_text(garbled) == "The dynamics along ε=0 is given by (t )) and (a), (b), (c), so (x + y)."
    assert clean_text("Quarter ¼ and one ð alone") == "Quarter 1⁄4 and one ð alone"  # no pairs: left as it is
