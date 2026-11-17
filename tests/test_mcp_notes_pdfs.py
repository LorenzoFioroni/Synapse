# ruff: noqa: F811  (the fixtures of test_mcp.py are imported, then used by name)
"""The MCP tools on PDFs kept in the notes folders (named by path), and notes that link to them."""

from __future__ import annotations

import pytest

from backend.pdf import render as pdfrender
from tests.test_mcp import PAGES, call, call_json, editing, lib, make_pages_pdf, write_error  # noqa: F401


@pytest.fixture
def pdf(lib, svc):
    """refs/paper.pdf: the same pages as the library's smith2021 PDF."""
    (svc.vault.root / "refs").mkdir()
    make_pages_pdf(svc.vault.root / "refs" / "paper.pdf", PAGES)
    svc.note_pdfs.sync()
    svc.notes.sync_files()
    svc.indexer.sync(background=False)
    return svc.note_pdfs.by_path("refs/paper.pdf")


def test_read_search_outline_and_pages_of_a_notes_pdf(lib, pdf):
    server = lib["server"]
    err, texts = call(server, "read_paper", paper="refs/paper.pdf", pages="2")
    assert not err and "Lindblad master equation" in texts[0] and "[[refs/paper.pdf#p=N]]" in texts[0]
    assert "[@" not in texts[0].split("\n")[0]
    # by file name, and with the link syntax the app uses
    assert not call(server, "read_paper", paper="paper.pdf", pages="1")[0]
    assert not call(server, "read_paper", paper="[[refs/paper.pdf#p=3]]", pages="3")[0]
    r = call_json(server, "search_in_paper", paper="refs/paper.pdf", query="tensor networks")
    assert r["paper"] == "refs/paper.pdf" and r["pages_total"] == len(PAGES) and r["results"][0]["page"] == 3
    assert "attachment" not in r
    o = call_json(server, "get_paper_outline", paper="refs/paper.pdf")
    assert o["paper"] == "refs/paper.pdf" and o["pages_total"] == len(PAGES)
    err, texts = call(server, "read_paper", paper="refs/nothing.pdf")
    assert err and "No PDF 'refs/nothing.pdf' in the notes folders" in texts[0]
    err, texts = call(server, "read_paper", paper="refs/paper.pdf", attachment=3)
    assert err and "attachment is for the PDFs of a library item" in texts[0]
    # a library item still works exactly as before
    assert "Cite as [@smith2021, p. N]" in call(server, "read_paper", paper="smith2021", pages="2")[1][0]


@pytest.mark.skipif(not pdfrender.available(), reason="pypdfium2 not installed")
def test_view_page_of_a_notes_pdf(lib, pdf):
    err, texts = call(lib["server"], "view_page", paper="refs/paper.pdf", page=2)
    assert not err and texts[0] == "<image>" and '"paper": "refs/paper.pdf"' in texts[1]
    err, texts = call(lib["server"], "view_page", paper="refs/paper.pdf", page=99)
    assert err and f"Page 99 does not exist: the PDF has {len(PAGES)} pages." in texts[0]


@pytest.mark.skipif(not pdfrender.available(), reason="pypdfium2 not installed")
def test_highlights_on_a_notes_pdf(editing, svc, pdf):
    assert "No highlights" in call_json(editing, "get_annotations", paper="refs/paper.pdf")["note"]
    r = call_json(editing, "add_highlight", paper="refs/paper.pdf", page=2, quote="the Lindblad master equation")
    assert r["highlighted"]["paper"] == "refs/paper.pdf"
    (ann,) = svc.annotations.for_note_pdf(pdf["id"])
    assert ann["page"] == 2 and ann["type"] == "highlight" and svc.annotations.for_item(editing_item(svc)) == []
    r = call_json(editing, "get_annotations", paper="refs/paper.pdf")
    assert [a["id"] for a in r["annotations"]] == [ann["id"]] and "[[refs/paper.pdf#p=N]]" in r["cite"]
    err, texts = call(editing, "add_highlight", paper="refs/paper.pdf", page=3, quote="the Lindblad master equation")
    assert err and "not on page 3" in texts[0]


def editing_item(svc):
    return next(p["id"] for p in svc.papers.list() if p.get("citation_key") == "smith2021")


def test_the_highlight_tool_is_still_gated_by_the_library_setting(lib, svc, pdf):
    names = [
        t["name"] for t in lib["server"].handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    ]
    assert "get_annotations" in names and "add_highlight" not in names


def test_get_note_lists_linked_pdfs(lib, svc, pdf):
    svc.note_service.create(
        path="reader.md", title="Reader", text="See [[refs/paper.pdf#p=2]], [[paper.pdf#p=3]] and [[gone.pdf]].\n"
    )
    r = call_json(lib["server"], "get_note", note="reader.md")
    got = {e["link"]: e for e in r["linked_pdfs"]}
    assert got["refs/paper.pdf"]["pages"] == [2] and got["paper.pdf"]["pdf"] == "refs/paper.pdf"
    assert got["gone.pdf"]["broken"] is True


def test_write_note_checks_pdf_links_and_quotes(lib, svc, pdf):
    server = lib["server"]
    ok = call_json(server, "write_note", title="Good", text="> the Lindblad master equation [[refs/paper.pdf#p=2]]\n")
    assert ok["saved"]
    assert "not a PDF in the notes folders" in write_error(server, "See [[refs/missing.pdf#p=1]].")
    assert "only 12 pages" in write_error(server, "See [[refs/paper.pdf#p=99]].")
    assert "names a page as #p=4" in write_error(server, "See [[refs/paper.pdf#Intro]].")
    assert "were not found on page 3" in write_error(server, "> something never written there [[refs/paper.pdf#p=3]]\n")
    assert "these words are on page 2, not 3" in write_error(
        server, "> the Lindblad master equation for the reduced [[refs/paper.pdf#p=3]]\n"
    )
    assert "needs a page" in write_error(server, "> the Lindblad master equation [[refs/paper.pdf]]\n")
