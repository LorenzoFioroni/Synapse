"""The full-text index and its background indexer (without real PDFs: extraction is faked)."""

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
