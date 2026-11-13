"""The MCP server: protocol handling and every library tool, in-process and over stdio."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.mcp import build_server
from backend.mcp.protocol import PROTOCOL_VERSIONS, Server, Tool, ToolError, serve_stdio
from backend.mcp.tools.common import parse_pages
from backend.pdf import png as pdfpng
from backend.pdf import regions as pdfregions
from backend.pdf import render as pdfrender

ROOT = Path(__file__).resolve().parent.parent


def make_stream_pdf(path: Path, streams: list[str]) -> Path:
    """A PDF with one page per content stream (Helvetica as /F1; no external deps)."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    font = 3 + 2 * len(streams)
    for st in streams:
        stream = st.encode("latin-1")
        page_no, content_no = len(objs) + 1, len(objs) + 2
        kids.append(page_no)
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_no} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(streams)} >>".encode()
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


def _text(x, y, s, size=11):
    return f"BT /F1 {size} Tf {x} {y} Td ({s}) Tj ET\n"


def make_pages_pdf(path: Path, pages: list[str]) -> Path:
    """A PDF with one line of text per page."""
    return make_stream_pdf(path, [_text(72, 720, t, 12) for t in pages])


def figure_page() -> str:
    """Body text, a bar chart (paths) with its caption below, a table (rules) with its caption above."""
    s = "".join(
        _text(72, y, f"Body text line {i} of the introduction, describing the model in words.")
        for i, y in enumerate(range(740, 630, -14))
    )
    s += "q 0 0 0 RG 1 w 120 430 380 170 re S Q\n"
    for k, hgt in enumerate((60, 110, 150, 90)):
        s += f"q 0.2 0.4 0.8 rg {160 + 80 * k} 431 40 {hgt} re f Q\n"
    s += _text(290, 412, "time t", 8) + _text(98, 510, "0.5", 8)
    s += _text(100, 385, "Figure 2: Phase diagram of the noisy circuit model.")
    s += _text(72, 360, "We see in Fig. 2 that the classical phase is wide.")
    s += _text(72, 340, "Fig. 2 shows the phases for several values of p.")
    s += _text(100, 305, "Table 1: Parameters of the simulations.")
    for y in (295, 275, 215):
        s += f"q 0 0 0 RG 0.5 w 100 {y} m 500 {y} l S Q\n"
    s += _text(110, 281, "N p depth") + _text(110, 260, "16 0.01 100") + _text(110, 240, "32 0.02 200")
    s += _text(110, 220, "64 0.05 400")
    s += "".join(
        _text(72, y, f"Conclusion line {i}: the master equation remains valid in this regime.")
        for i, y in enumerate(range(180, 80, -14))
    )
    return s


PAGES = [
    "Introduction to open quantum systems",
    "We derive the Lindblad master equation for the reduced density matrix",
    "Conclusion: tensor networks simulate the master equation efficiently",
] + [f"Appendix page {n} with filler text" for n in range(4, 13)]

PROJECT = """# Crispr project

The dynamics follow [@smith2021, p. 2] and earlier work [@doe2019].
A paper not in the library: [@missing2000].
See also [[other]].
"""


@pytest.fixture
def lib(svc):
    s = svc
    smith = s.papers.create(
        {
            "title": "Open quantum dynamics",
            "authors": "Smith, John; Doe, Jane",
            "year": 2021,
            "doi": "10.1000/smith",
            "abstract": "We study open systems.",
            "citation_key": "smith2021",
            "journal": "Phys. Rev. A",
        }
    )
    doe = s.papers.create(
        {
            "title": "Earlier results",
            "authors": "Doe, Jane",
            "year": 2019,
            "doi": "10.1000/doe",
            "citation_key": "doe2019",
        }
    )
    lee = s.papers.create(
        {"title": "Unrelated book", "authors": "Lee, Ann", "year": 2020, "citation_key": "lee2020", "item_type": "book"}
    )
    make_pages_pdf(s.config.pdf_dir / "smith.pdf", PAGES)
    s.papers.add_attachment(smith["id"], "Full Text PDF", "smith.pdf")
    folder = s.folders.create("Physics")
    sub = s.folders.create("Transport", folder["id"])
    s.folders.change_items([smith["id"], doe["id"]], add=[sub["id"]])
    s.tags.add([smith["id"]], ["quantum"])
    s.citations.store.save(
        smith["id"],
        "openalex",
        [
            {"doi": "10.1000/doe", "title": "Earlier results", "year": 2019},
            {"doi": "10.9999/ext", "title": "External foundation", "authors": "Old, Al", "year": 2010},
        ],
        doi="10.1000/smith",
    )
    s.citations.store.save(
        doe["id"], "crossref", [{"doi": "10.9999/ext", "title": "External foundation"}], doi="10.1000/doe"
    )
    s.note_service.create(path="projects/crispr.md", title="Crispr project", text=PROJECT)
    s.note_service.create(path="other.md", title="Other", text="# Other\n\nBack to [[crispr]].\n")
    pn = s.note_service.create(item_id=smith["id"])
    s.note_service.save(pn["path"], pn["text"] + "My summary: the master equation is derived carefully.\n", pn["hash"])
    fig = s.papers.create({"title": "Figures paper", "authors": "Fig, Ann", "year": 2024, "citation_key": "fig2024"})
    make_stream_pdf(s.config.pdf_dir / "fig.pdf", [_text(72, 720, "Title page of the figures paper"), figure_page()])
    s.papers.add_attachment(fig["id"], "Full Text PDF", "fig.pdf")
    s.indexer.sync(background=False)
    return {"smith": smith, "doe": doe, "lee": lee, "server": build_server(s)}


def call(server: Server, name: str, **args):
    reply = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    )
    assert "result" in reply, reply
    res = reply["result"]
    texts = [c["text"] if c["type"] == "text" else f"<{c['type']}>" for c in res["content"]]
    return res["isError"], texts


def call_json(server, name, **args):
    err, texts = call(server, name, **args)
    assert not err, texts
    return json.loads(texts[0])


# ======================================================================== protocol
def test_initialize_and_list(lib):
    server = lib["server"]
    r = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
        }
    )
    res = r["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["capabilities"] == {"tools": {"listChanged": False}}
    assert res["serverInfo"]["name"] == "synapse"
    assert "[@key, p. N]" in res["instructions"]
    r = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert r["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}
    tools = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == [
        "search_library",
        "list_collections",
        "get_note",
        "view_image",
        "get_paper",
        "search_in_paper",
        "read_paper",
        "view_page",
        "snapshot",
        "get_references",
        "get_citation_graph",
        "write_note",
        "edit_note",
        "undo_note_edit",
        "list_tasks",
        "get_task",
        "create_task",
        "update_task",
        "delete_task",
    ]
    writes = ("write_note", "snapshot", "edit_note", "undo_note_edit", "create_task", "update_task", "delete_task")
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"]
        assert t["annotations"]["readOnlyHint"] == (t["name"] not in writes)
    assert "KaTeX" in res["instructions"] and "by file name" in res["instructions"]


def test_protocol_errors(lib):
    server = lib["server"]
    assert server.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})["error"]["code"] == -32601
    assert server.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope"}})
    assert r["error"]["code"] == -32602
    err, texts = call(server, "get_paper", paper="smith2021", bogus=1)
    assert err and "Unknown argument" in texts[0]
    err, texts = call(server, "get_paper")
    assert err and "Missing required" in texts[0]
    err, texts = call(server, "get_references", paper="smith2021", direction="sideways")
    assert err and "one of" in texts[0]
    err, texts = call(server, "get_paper", paper="nobody1900")
    assert err and "search_library" in texts[0]
    batch = server.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "x"}])
    assert batch == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_tool_crash_is_a_tool_error():
    def boom():
        raise RuntimeError("kaputt")

    server = Server("t", "0")
    server.add(Tool("boom", "Boom", "d", {"type": "object", "properties": {}}, boom))
    err, texts = call(server, "boom")
    assert err and "RuntimeError" in texts[0] and "kaputt" not in texts[0]  # the details stay in the log


def test_stdio_loop_keeps_stdout_clean():
    import io

    def noisy():
        print("stray output")
        return "ok"

    server = Server("t", "0")
    server.add(Tool("noisy", "Noisy", "d", {"type": "object", "properties": {}}, noisy))
    lines = (
        "\n".join(
            json.dumps(m)
            for m in [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "noisy"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            ]
        )
        + "\nnot json\n"
    )
    out = io.StringIO()
    serve_stdio(server, io.StringIO(lines), out)
    replies = [json.loads(l) for l in out.getvalue().splitlines()]
    assert replies[0]["result"]["content"][0]["text"] == "ok"
    assert replies[1]["error"]["code"] == -32700
    assert len(replies) == 2


def test_parse_pages():
    assert parse_pages("3", 10) == [3]
    assert parse_pages("2-4, 7", 10) == [2, 3, 4, 7]
    assert parse_pages("9-", 10) == [9, 10]
    assert parse_pages("8-20", 10) == [8, 9, 10]
    for bad in ("0", "5-3", "x", "11", "-"):
        with pytest.raises(ToolError):
            parse_pages(bad, 10)


# ======================================================================== tools
def test_search_library(lib):
    server = lib["server"]
    r = call_json(server, "search_library", query="lindblad")
    assert [i["key"] for i in r["items"]] == ["smith2021"]
    assert "«" in r["items"][0]["match"]
    r = call_json(server, "search_library", query="lindblad", fulltext=False, scope="items")
    assert r["items"] == [] and "notes" not in r
    r = call_json(server, "search_library", query='folder:"Physics/Transport"', scope="items")
    assert {i["key"] for i in r["items"]} == {"smith2021", "doe2019"}
    r = call_json(server, "search_library", query="earlier work", scope="notes")
    assert [n["path"] for n in r["notes"]] == ["projects/crispr.md"]
    # through the notes: the paper note says "summary"
    r = call_json(server, "search_library", query="summary", scope="items", fulltext=False)
    assert [i["key"] for i in r["items"]] == ["smith2021"]


def test_list_collections(lib):
    r = call_json(lib["server"], "list_collections")
    assert {"path": "Physics/Transport", "items": 2} in [{k: f[k] for k in ("path", "items")} for f in r["folders"]]
    assert r["tags"] == [{"name": "quantum", "items": 1}]
    assert r["library"]["items"] == 4 and r["library"]["with_pdf"] == 2 and r["library"]["with_references"] == 2
    assert {"path": "projects", "notes": 1} in r["note_folders"]


def test_get_note(lib):
    server = lib["server"]
    for ref in ("projects/crispr.md", "projects/crispr", "crispr", "Crispr project", "[[crispr]]"):
        err, texts = call(server, "get_note", note=ref)
        assert not err, (ref, texts)
    summary, text = json.loads(texts[0]), texts[1]
    assert "[@smith2021, p. 2]" in text
    cited = {c["key"]: c for c in summary["cited_papers"]}
    assert cited["smith2021"]["paper"]["title"] == "Open quantum dynamics"
    assert cited["smith2021"]["citations"][0]["page"] == 2
    assert cited["missing2000"]["not_in_library"] is True
    assert summary["links_to_notes"][0]["resolves_to"] == "other.md"
    assert summary["linked_from"][0]["path"] == "other.md"
    err, texts = call(server, "get_note", note="no such note")
    assert err


def test_get_paper(lib):
    err, texts = call(lib["server"], "get_paper", paper="[@smith2021, p. 3]")
    assert not err
    r = json.loads(texts[0])
    assert r["key"] == "smith2021" and r["doi"] == "10.1000/smith" and r["abstract"] == "We study open systems."
    assert r["pdfs"][0]["pages"] == 12
    assert r["folders"] == ["Physics/Transport"] and r["tags"] == ["quantum"]
    assert r["paper_note"] == "papers/smith2021.md"
    assert "master equation is derived" in texts[1]
    assert [c["path"] for c in r["cited_in_notes"]] == ["projects/crispr.md"]
    assert r["references"]["stored"] == 2 and r["references"]["in_library"] == 1
    doe = call_json(lib["server"], "get_paper", paper=lib["doe"]["id"])  # by item id; no PDF, no note
    assert doe["key"] == "doe2019" and doe["cited_by_in_library"] == ["smith2021"] and "pdfs" not in doe


def test_search_in_paper(lib):
    server = lib["server"]
    r = call_json(server, "search_in_paper", paper="smith2021", query='"master equation" lindblad')
    assert [x["page"] for x in r["results"]][:2] == [2, 3]
    assert "Lindblad master equation" in r["results"][0]["snippets"][0]
    assert r["pages_total"] == 12
    r = call_json(server, "search_in_paper", paper="smith2021", query="LINDBLÁD")  # case and accents ignored
    assert [x["page"] for x in r["results"]] == [2]
    r = call_json(server, "search_in_paper", paper="smith2021", query="graphene")
    assert r["results"] == [] and r["hint"]
    err, texts = call(server, "search_in_paper", paper="doe2019", query="x")
    assert err and "no PDF" in texts[0] and "10.1000/doe" in texts[0]


def test_read_paper(lib):
    server = lib["server"]
    err, texts = call(server, "read_paper", paper="smith2021", pages="2")
    assert not err and "=== Page 2 ===" in texts[0] and "Lindblad master equation" in texts[0]
    assert "=== Page 1 ===" not in texts[0]
    err, texts = call(server, "read_paper", paper="smith2021", pages="1-")
    assert "=== Page 10 ===" in texts[0] and "=== Page 11 ===" not in texts[0] and "pages 11-12" in texts[0]
    err, texts = call(server, "read_paper", paper="smith2021", pages="40")
    assert err and "12 pages" in texts[0]
    err, texts = call(server, "read_paper", paper="smith2021", pages="1", attachment=999)
    assert err


def test_get_references(lib):
    server = lib["server"]
    r = call_json(server, "get_references", paper="smith2021")
    assert r["references_total"] == 2 and r["references_in_library"] == 1
    assert r["references"][0]["in_library"] and r["references"][0]["key"] == "doe2019"
    assert r["references"][1] == {
        "in_library": False,
        "title": "External foundation",
        "authors": "Old, Al",
        "year": 2010,
        "doi": "10.9999/ext",
    }
    r = call_json(server, "get_references", paper="smith2021", in_library_only=True)
    assert len(r["references"]) == 1
    r = call_json(server, "get_references", paper="doe2019", direction="cited_by")
    assert [c["key"] for c in r["cited_by_in_library"]] == ["smith2021"] and "references" not in r
    r = call_json(server, "get_references", paper="lee2020", direction="both")
    assert "no DOI" in r["references_note"]


def test_get_citation_graph(lib):
    server = lib["server"]
    r = call_json(server, "get_citation_graph")
    missing = r["most_cited_not_in_library"]
    assert missing[0]["doi"] == "10.9999/ext" and sorted(missing[0]["cited_by"]) == ["doe2019", "smith2021"]
    assert r["most_cited_in_library"][0]["key"] == "doe2019"
    r = call_json(server, "get_citation_graph", papers=["smith2021"], include_edges=True)
    nodes = {n["node"]: n for n in r["nodes"]}
    assert nodes["smith2021"]["distance"] == 0 and nodes["doe2019"]["distance"] == 1
    assert "smith2021 -> doe2019" in r["edges"] and "doe2019 -> doi:10.9999/ext" in r["edges"]
    assert r["cited_by_several"][0]["node"] == "doi:10.9999/ext" and r["cited_by_several"][0]["cited_by_count"] == 2
    r = call_json(server, "get_citation_graph", papers=["lee2020"], depth=2)
    assert r["seeds_without_reference_list"] == ["lee2020"]


def test_get_citation_graph_is_bounded(lib):
    r = call_json(lib["server"], "get_citation_graph", papers=["smith2021"])
    assert "edges" not in r and r["edges_total"] == 3  # the list is opt-in; the count is not
    r = call_json(lib["server"], "get_citation_graph", papers=["smith2021"], limit=2, include_edges=True)
    assert [n["node"] for n in r["nodes"]] == ["smith2021", "doe2019"]  # nearest first, seeds always
    assert r["edges"] == ["smith2021 -> doe2019"] and "showing 2 of 3 nodes" in r["truncated"]
    # shared foundations come from the whole neighbourhood, not from the listed nodes
    assert r["cited_by_several"][0]["node"] == "doi:10.9999/ext"


def test_get_note_section(lib, svc):
    server = lib["server"]
    text = (
        "# Long note\n\nIntro cites [@doe2019].\n\n## Method\n\nUses [@smith2021, p. 2].\n\n"
        "### Detail\n\nMore [[other]].\n\n## Results\n\nNothing cited here.\n"
    )
    svc.note_service.create(path="long.md", title="Long note", text=text)
    err, texts = call(server, "get_note", note="long", section="## Method")
    assert not err, texts
    summary = json.loads(texts[0])
    assert summary["section"] == "## Method" and summary["outline"][1] == "## Method"
    assert [c["key"] for c in summary["cited_papers"]] == ["smith2021"]
    assert "Uses [@smith2021, p. 2]" in texts[1] and "### Detail" in texts[1]  # sub-sections belong to it
    assert "Intro" not in texts[1] and "Results" not in texts[1]
    assert summary["links_to_notes"][0]["resolves_to"] == "other.md"  # the link is in "Detail"
    summary = json.loads(call(server, "get_note", note="long", section="results")[1][0])
    assert summary["cited_papers"] == []  # present, and empty
    err, texts = call(server, "get_note", note="long", section="Nope")
    assert err and "matches no heading" in texts[0] and "## Method" in texts[0]
    whole = json.loads(call(server, "get_note", note="long")[1][0])
    assert {c["key"] for c in whole["cited_papers"]} == {"doe2019", "smith2021"}
    assert set(whole["cited_papers"][0]["paper"]) <= {"title", "year", "has_pdf"}  # the rest is in get_paper


def test_search_library_paging_and_dates(lib):
    server = lib["server"]
    r = call_json(server, "search_library", query="has:abstract OR author:doe", scope="items")
    assert "OR and AND are not operators" in r["warnings"][0]
    r = call_json(server, "search_library", query="sort:added", scope="items")
    assert r["items"] == [] and "sort:" in r["warnings"][0] and "Filters:" in r["warnings"][0]
    r = call_json(server, "search_library", query="added:7d", scope="items", sort="added")
    assert r["items_total"] == 4 and "warnings" not in r and r["items"][0]["added"]
    assert call_json(server, "search_library", query="added:<2020", scope="items")["items"] == []
    r = call_json(server, "search_library", query="year:2020-", scope="items", sort="year", limit=2)
    assert [i["key"] for i in r["items"]] == ["fig2024", "smith2021"] and r["next_offset"] == 2
    r = call_json(server, "search_library", query="year:2020-", scope="items", sort="year", limit=2, offset=2)
    assert [i["key"] for i in r["items"]] == ["lee2020"] and "next_offset" not in r
    r = call_json(server, "search_library", query="open", scope="items", abstracts=True)
    assert r["items"][0]["abstract"] == "We study open systems."
    r = call_json(server, "list_collections")
    assert r["item_types"]["Book"] == 1 and "added:" in r["search_syntax"]


def test_write_note(lib, svc):
    server = lib["server"]
    r = call_json(
        server,
        "write_note",
        title="Answer: dynamics",
        text="Based on [@smith2021, p. 2], with $t \\lesssim N$.",
        tags=["qa"],
    )
    assert r["saved"].startswith("ai/") and r["citations"] == 1 and r["link"].startswith("[[")
    text = svc.vault.read(r["saved"])[0]
    assert "author: ai" in text and "# Answer: dynamics" in text and "qa" in text
    assert svc.notes.note(r["saved"])["author"] == "ai"
    again = call_json(server, "write_note", title="Answer: dynamics", text="Second.")
    assert again["saved"] != r["saved"] and svc.vault.read(r["saved"])[0] == text  # never overwrites
    sneaky = call_json(server, "write_note", title="x", text="y", folder="../../etc")
    assert sneaky["saved"].startswith("ai/") and ".." not in sneaky["saved"]


def write_error(server, text):
    err, texts = call(server, "write_note", title="Checked", text=text)
    assert err, texts
    assert "Nothing was saved" in texts[0]
    return texts[0]


def test_write_note_refuses_broken_notes(lib, svc):
    server = lib["server"]
    before = sorted(p.name for p in (svc.vault.root).rglob("*.md"))
    e = write_error(server, "See [@smith2012] and [@ghost1999].")
    assert "Did you mean @smith2021" in e and "ghost1999" in e and "plain text" in e
    e = write_error(server, "Too far: [@smith2021, p. 40].")
    assert "only 12 pages" in e
    e = write_error(server, "See [[no such note]].")
    assert "does not match any note" in e
    e = write_error(server, "See [[crispr#Methods]].")
    assert "no such heading" in e and "Crispr project" in e
    e = write_error(server, "![plot](attachments/missing.png)")
    assert "does not exist" in e
    e = write_error(server, "The limit is t ≲ N − 1.6 and ⟨τ⟩ ≈ 1.607.")
    assert "KaTeX" in e and "line 10:" in e
    assert sorted(p.name for p in (svc.vault.root).rglob("*.md")) == before  # nothing written


def test_write_note_fixes_title_links(lib, svc):
    r = call_json(
        lib["server"],
        "write_note",
        title="Linked",
        text="See [[Crispr project]] and [[Crispr project#Crispr project|the project]].",
    )
    text = svc.vault.read(r["saved"])[0]
    assert "[[crispr|Crispr project]]" in text and "[[crispr#Crispr project|the project]]" in text
    assert len(r["links_fixed"]) == 2
    assert svc.notes.outgoing(r["saved"])[0]["resolved"] == "projects/crispr.md"


def test_edit_note(lib, svc):
    server = lib["server"]
    path = "projects/crispr.md"
    before, h0 = svc.vault.read(path)
    r = call_json(
        server,
        "edit_note",
        note="Crispr project",
        summary="Pointed to Lee",
        edits=[{"old": "earlier work [@doe2019]", "new": "earlier work [@doe2019; @lee2020]"}],
    )
    assert r["saved"] == path and r["version"]
    assert "-The dynamics follow" in r["changes"] and "+The dynamics follow" in r["changes"]
    after, h1 = svc.vault.read(path)
    assert after == before.replace("[@doe2019]", "[@doe2019; @lee2020]")
    assert "lee2020" in [l["target"] for l in svc.notes.outgoing(path)]  # index updated
    # the change is its own version in the history (after the user's own edits), for the app's undo
    hist = svc.note_service.history_of(path)
    assert hist["hash"] == h1 and [e["by"] for e in hist["entries"]] == ["ai", "human"]
    e = hist["entries"][0]
    assert e["current"] and e["by"] == "ai" and e["summary"] == "Pointed to Lee" and "before" not in e
    assert e["before_hash"] == h0 and e["after_hash"] == h1 and e["id"] == r["version"]
    assert svc.note_service.history_version(e["id"], path)["before"] == before
    # append, and several edits in one call; the old [@missing2000] in the note does not block them
    r = call_json(
        server,
        "edit_note",
        note=path,
        append="## Next\n\nRead [@smith2021, p. 3].",
        edits=[{"old": "See also [[other]].", "new": "See also [[other]] and [[crispr]]."}],
    )
    text = svc.vault.read(path)[0]
    assert text.endswith("\n\n## Next\n\nRead [@smith2021, p. 3].\n") and "[[other]] and [[crispr]]" in text
    assert [e["by"] for e in svc.note_service.history_of(path)["entries"]] == ["ai", "ai", "human"]
    # whole text: front matter kept when left out
    r = call_json(server, "edit_note", note=path, text="# Crispr project\n\nShort now.\n")
    text = svc.vault.read(path)[0]
    assert text.startswith("---\n") and "id: " in text and text.endswith("# Crispr project\n\nShort now.\n")
    # refusals: nothing saved
    unchanged = svc.vault.read(path)
    for args, msg in [
        (dict(edits=[{"old": "not in the note", "new": "x"}]), "occurs 0 times"),
        (dict(edits=[{"old": "", "new": "x"}]), "old is empty"),
        (dict(edits=[{"old": "Short now.", "new": "Short now."}]), "changes nothing"),
        (dict(append="Broken [@ghost1999]."), "Nothing was saved"),
        (dict(append="t ≈ 1.6 outside math"), "KaTeX"),
        (dict(), "Nothing to change"),
        (dict(text="x", append="y"), "not both"),
    ]:
        err, texts = call(server, "edit_note", note=path, **args)
        assert err and msg in texts[0], (args, texts)
    assert svc.vault.read(path) == unchanged
    err, texts = call(server, "edit_note", note="crispr", edits=[{"old": "o", "new": "x"}])
    assert err and "occurs" in texts[0] and "times" in texts[0]
    # edits sent as JSON text by some clients
    r = call_json(server, "edit_note", note=path, edits=json.dumps([{"old": "Short now.", "new": "Short."}]))
    assert svc.vault.read(path)[0].endswith("Short.\n")


def test_undo_note_edit_and_history_follows_moves(lib, svc):
    server = lib["server"]
    path = "other.md"
    original = svc.vault.read(path)[0]
    call_json(server, "edit_note", note=path, append="Added by Claude.", summary="Added a line")
    r = call_json(server, "undo_note_edit", note="other")
    assert r["undid"] == "Added a line" and svc.vault.read(path)[0] == original
    entries = [e for e in svc.note_service.history_of(path)["entries"] if e["by"] == "ai"]
    assert [e["summary"] for e in entries] == ["Undid: Added a line", "Added a line"]
    # changed by the user since, elsewhere in the note: the AI's change is taken out, the user's kept
    call_json(server, "edit_note", note=path, append="Second line.", summary="Second")
    text, h = svc.vault.read(path)
    svc.note_service.save(path, "Typed at the top.\n\n" + text, h)
    r = call_json(server, "undo_note_edit", note=path)
    now = svc.vault.read(path)[0]
    assert r["undid"] == "Second" and r["kept_later_edits"] and "Second line." not in now
    assert now.startswith("Typed at the top.") and now.endswith(original.rstrip("\n").split("\n")[-1] + "\n")
    # … but not when the user changed the same lines
    call_json(server, "edit_note", note=path, append="Third line.", summary="Third")
    text, h = svc.vault.read(path)
    svc.note_service.save(path, text.replace("Third line.", "Third line, reworded by the user."), h)
    err, texts = call(server, "undo_note_edit", note=path)
    assert err and "changed after" in texts[0] and "reworded by the user" in svc.vault.read(path)[0]
    err, texts = call(server, "undo_note_edit", note="crispr")
    assert err and "No AI app has edited" in texts[0]
    # renaming or moving keeps the history with the note
    svc.note_service.checkpoint(path)  # the user's last edit, waiting until now
    n = len(svc.note_service.history_of(path)["entries"])
    svc.note_service.move(path, "moved/other2.md")
    moved = svc.note_service.history_of("moved/other2.md")["entries"]
    assert len(moved) == n + 1 and moved[0]["kind"] == "rename" and moved[0]["from_path"] == path
    svc.note_service.move_folder("moved", "elsewhere")
    assert len(svc.note_service.history_of("elsewhere/other2.md")["entries"]) == n + 2
    assert not (svc.vault.root / ".git").exists()  # the history lives next to the notes, not in them


def test_history_api(lib, svc, client):
    call_json(lib["server"], "edit_note", note="other.md", append="More.", summary="More")
    r = client.get("/api/notes/history?path=other.md").get_json()
    assert r["path"] == "other.md" and r["entries"][0]["current"] and r["entries"][0]["summary"] == "More"
    e = r["entries"][0]
    v = client.get(f"/api/notes/history/version?id={e['id']}&path=other.md").get_json()
    assert v["before"].endswith("Back to [[crispr]].\n") and v["after"].endswith("More.\n")
    assert client.get("/api/notes/history/version?id=../../x&path=other.md").status_code == 404
    assert client.get(f"/api/notes/history/version?id={e['id']}").status_code == 404
    assert client.get("/api/notes/history?path=../x.md").status_code == 400


def test_write_note_checks_quotes(lib, svc):
    server = lib["server"]
    ok = call_json(
        server,
        "write_note",
        title="Quoted",
        text="> We derive the Lindblad master equation [@smith2021, p. 2]\n\nMath as $\\rho$, Unicode only in quotes:\n\n"
        "> We derive the Lindblad master equation for the reduced density matrix ρ ≈ ρ' [@smith2021, p. 2]\n",
    )
    assert ok["saved"]
    e = write_error(server, "> We derive the Lindblad master equation [@smith2021, p. 3]")
    assert "on page 2, not 3" in e
    e = write_error(server, "> Words the paper never says anywhere at all [@smith2021, p. 2]")
    assert "not found on page 2" in e
    e = write_error(server, "> We derive the Lindblad master equation [@smith2021]")
    assert "needs a page" in e
    e = write_error(server, "> [!note] Summary\n> My own summary of the derivation [@smith2021, p. 2]")
    assert "callout" in e
    # a citation of a paper without PDF in a quote cannot be checked, and is accepted
    assert call_json(server, "write_note", title="No pdf", text="> Something earlier [@doe2019, p. 1]")["saved"]


def test_note_links_in_results(lib):
    server = lib["server"]
    r = call_json(server, "search_library", query="earlier work", scope="notes")
    assert r["notes"][0]["link"] == "[[crispr|Crispr project]]"
    p = call_json(server, "get_paper", paper="smith2021")
    assert p["paper_note_link"] == "[[smith2021|Open quantum dynamics]]"
    assert p["cited_in_notes"][0]["link"] == "[[crispr|Crispr project]]"
    err, texts = call(server, "get_note", note="crispr")
    summary = json.loads(texts[0])
    assert summary["link"] == "[[crispr|Crispr project]]" and summary["linked_from"][0]["link"] == "[[other]]"


def test_notes_edited_elsewhere_are_seen(lib, svc):
    server = lib["server"]
    (svc.vault.root / "fresh.md").write_text("# Fresh\n\nCites [@doe2019].\n")
    import time

    time.sleep(0.01)
    server.tools["get_note"].fn.__self__._last_sync = 0  # skip the 2 s throttle
    r = call_json(server, "search_library", query="fresh", scope="notes")
    assert [n["path"] for n in r["notes"]] == ["fresh.md"]


# ======================================================================== pages as images
needs_pdfium = pytest.mark.skipif(not pdfrender.available(), reason="pypdfium2 not installed")


def test_png_region_roundtrip():
    png = pdfpng.png_encode(
        bytes([255, 0, 0] * 4), 2, 2, {pdfpng.REGION_KEYWORD: '{"page":3,"rect":[0.1,0.2,0.3,0.4]}'}
    )
    assert png.startswith(b"\x89PNG") and pdfpng.read_region(png) == {"page": 3, "rect": [0.1, 0.2, 0.3, 0.4]}
    assert pdfpng.read_region(pdfpng.png_encode(b"\0\0\0", 1, 1)) is None


@needs_pdfium
def test_figure_regions(lib, svc):
    regions = pdfregions.figure_regions(svc.config.pdf_dir / "fig.pdf", 2)
    assert [r["label"] for r in regions] == ["Figure 2", "Table 1"]  # "Fig. 2 shows…" is running text
    x, y, w, h = regions[0]["rect"]
    # the chart (x 98…500 pt incl. its "0.5" label, y 412…600 pt from the bottom) without the caption
    assert abs(x - 98 / 612) < 0.02 and abs(x + w - 500 / 612) < 0.02
    assert abs(y - (792 - 600) / 792) < 0.02 and abs(y + h - (792 - 412) / 792) < 0.02
    tx, ty, tw, th = regions[1]["rect"]
    assert abs(ty - (792 - 295) / 792) < 0.02 and abs(ty + th - (792 - 215) / 792) < 0.02


@needs_pdfium
def test_view_page(lib):
    reply = lib["server"].handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "view_page", "arguments": {"paper": "fig2024", "page": 2}},
        }
    )
    content = reply["result"]["content"]
    assert content[0]["type"] == "image" and content[0]["mimeType"] == "image/png"
    assert base64.b64decode(content[0]["data"]).startswith(b"\x89PNG")
    info = json.loads(content[1]["text"])
    assert [f["label"] for f in info["figures"]] == ["Figure 2", "Table 1"] and info["figures"][0]["box"] == 1
    err, texts = call(lib["server"], "view_page", paper="fig2024", page=9)
    assert err and "2 pages" in texts[0]


@needs_pdfium
def test_snapshot_and_use_in_note(lib, svc):
    server = lib["server"]
    reply = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "snapshot", "arguments": {"paper": "fig2024", "page": 2, "figure": "Fig. 2"}},
        }
    )
    content = reply["result"]["content"]
    assert content[0]["type"] == "image"
    info = json.loads(content[1]["text"])
    saved = svc.vault.root / info["saved"]
    assert info["saved"].startswith("attachments/") and saved.is_file()
    region = pdfpng.read_region(saved.read_bytes())
    assert region["page"] == 2 and region["rect"] == info["region"]["rect"]
    assert info["markdown"] == f"![fig2024 p. 2]({info['saved']})\n*Figure 2: [@fig2024, p. 2]*"
    note = call_json(
        server,
        "write_note",
        title="With figure",
        text="The chart:\n\n" + info["markdown"].replace("*Figure 2: ", "*Figure 2: phase diagram "),
    )
    assert note["saved"]
    err, texts = call(server, "snapshot", paper="fig2024", page=2, rect=[0.1, 0.6, 0.7, 0.15])
    assert not err and pdfpng.read_region((svc.vault.root / json.loads(texts[1])["saved"]).read_bytes())["rect"] == [
        0.1,
        0.6,
        0.7,
        0.15,
    ]
    err, texts = call(server, "snapshot", paper="fig2024", page=2, figure="Figure 7")
    assert err and "Figure 2, Table 1" in texts[0]
    err, texts = call(server, "snapshot", paper="fig2024", page=2)
    assert err and "either figure" in texts[0]
    err, texts = call(server, "snapshot", paper="doe2019", page=1, rect=[0, 0, 1, 1])
    assert err and "no PDF" in texts[0]


def _blocks(server, **args):
    reply = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "view_image", "arguments": args}}
    )
    return reply["result"]["isError"], reply["result"]["content"]


def test_view_image(lib, svc):
    server = lib["server"]
    small = pdfpng.png_encode(bytes([200, 30, 30]) * (40 * 20), 40, 20)
    pasted = svc.note_service.add_image("pasted.png", small)["path"]
    snap = json.loads(call(server, "snapshot", paper="fig2024", page=2, figure="Figure 2")[1][1])
    text = (
        f"# Pictures\n\nA pasted one:\n\n![my sketch]({pasted})\n\nFrom the paper:\n\n{snap['markdown']}\n\n"
        "![web](https://example.org/x.png)\n"
    )
    svc.note_service.create(path="pics.md", title="Pictures", text=text)

    # get_note lists the images and points at view_image
    summary = call_json(server, "get_note", note="pics")
    assert [i["path"] for i in summary["images"]] == [pasted, snap["saved"], "https://example.org/x.png"]
    assert (
        summary["images"][0]["alt"] == "my sketch" and summary["images"][1]["caption"] == "Figure 2: [@fig2024, p. 2]"
    )
    assert "view_image" in summary["images_hint"]
    assert "math_macros" not in summary
    svc.settings.update({"notes_math_macros": "\\newcommand{\\R}{\\mathbb{R}}"})
    assert call_json(server, "get_note", note="pics")["math_macros"] == "\\newcommand{\\R}{\\mathbb{R}}"

    # one image by path, as written in the note (and as a client may mangle it)
    for given in (pasted, "./" + pasted, "/Users/someone/Synapse/data/notes/" + pasted, pasted.split("/", 1)[1]):
        err, content = _blocks(server, path=given)
        assert not err, content
        meta = json.loads(content[0]["text"])
        assert meta["path"] == pasted and meta["size"] == "40x20"
        assert content[1]["type"] == "image" and base64.b64decode(content[1]["data"]) == small

    # all images of a note; the snapshot says where it came from; the web image is skipped
    err, content = _blocks(server, note="Pictures")
    assert not err
    assert [c["type"] for c in content] == ["text", "image", "text", "image", "text"]
    second = json.loads(content[2]["text"])
    assert second["caption"].startswith("Figure 2") and second["snapshot_of"]["page"] == 2
    assert "on the web" in content[4]["text"]
    err, content = _blocks(server, note="Pictures", path=snap["saved"])
    assert not err and len(content) == 2 and json.loads(content[0]["text"])["line"] == summary["images"][1]["line"]

    # refusals
    for args, words in (
        ({"path": "attachments/nope.png"}, "No image"),
        ({"path": "https://x.org/a.png"}, "on the web"),
        ({"path": "../../etc/passwd"}, "No image"),
        ({}, "Give path"),
        ({"note": "other"}, "shows no images"),
    ):
        err, content = _blocks(server, **args)
        assert err and words in content[0]["text"], (args, content)
    (svc.vault.root / "attachments" / "fake.png").write_bytes(b"not an image")
    err, content = _blocks(server, path="attachments/fake.png")
    assert err and "not a PNG" in content[0]["text"]


def test_images_in_a_gallery(lib, svc):
    server = lib["server"]
    small = pdfpng.png_encode(bytes([30, 200, 30]) * (8 * 8), 8, 8)
    a = svc.note_service.add_image("a.png", small)["path"]
    b = svc.note_service.add_image("b.png", small)["path"]
    text = (
        f"# Panels\n\n```gallery|600\n![first]({a})\n*Clean, [@fig2024, p. 2].*\n![]({b}) With speckle.\n\n"
        f"Both together.\n```\n\n![]({a})\n*Alone.*\n"
    )
    svc.note_service.create(path="panels.md", title="Panels", text=text)
    summary = call_json(server, "get_note", note="panels")
    imgs = summary["images"]
    assert [i["path"] for i in imgs] == [a, b, a]
    assert imgs[0]["caption"] == "Clean, [@fig2024, p. 2]." and imgs[1]["caption"] == "With speckle."
    assert imgs[0]["gallery"] == {"line": imgs[0]["line"] - 1, "panel": "(a)", "caption": "Both together."}
    assert imgs[1]["gallery"]["panel"] == "(b)"
    assert "gallery" not in imgs[2] and imgs[2]["caption"] == "Alone."
    # the citation in a panel's caption counts
    assert any(c["key"] == "fig2024" for c in summary["cited_papers"])
    err, content = _blocks(server, note="Panels")
    assert not err and json.loads(content[2]["text"])["gallery"]["panel"] == "(b)"


def test_view_image_scales_big_images(lib, svc, monkeypatch):
    PIL = pytest.importorskip("PIL.Image")
    import io

    buf = io.BytesIO()
    PIL.new("RGB", (4000, 1000), (10, 120, 200)).save(buf, "PNG")
    path = svc.note_service.add_image("big.png", buf.getvalue())["path"]
    err, content = _blocks(lib["server"], path=path)
    meta = json.loads(content[0]["text"])
    assert not err and meta["size"] == "4000x1000" and meta["sent_as"].startswith("1568x392")
    assert PIL.open(io.BytesIO(base64.b64decode(content[1]["data"]))).size == (1568, 392)

    # without Pillow: small images are sent as they are, huge ones refused with a hint
    monkeypatch.setitem(sys.modules, "PIL", None)
    err, content = _blocks(lib["server"], path=path)
    assert not err and json.loads(content[0]["text"])["size"] == "4000x1000"
    from backend.mcp.tools import images as images_mod

    monkeypatch.setattr(images_mod, "IMAGE_MAX_BYTES", 100)
    err, content = _blocks(lib["server"], path=path)
    assert err and "pip install pillow" in content[0]["text"]


EQ_PDF = ROOT / "tests" / "fixtures" / "equations.pdf"  # pdflatex of equations.tex next to it


def _overlap(a, b) -> float:
    return max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))


@needs_pdfium
def test_equation_regions():
    one = {e["number"]: e["rect"] for e in pdfregions.equation_regions(EQ_PDF, 1)}
    # two columns; "Eq. (1)", "see (1)." and "Equation (5) closes…" in the text are not equations
    assert sorted(one) == ["1", "2", "3", "4", "5", "6"]
    for n in "12345":
        x, y, w, h = one[n]
        assert x > 0.08 and x + w < 0.52, n  # left column only
    x, y, w, h = one["6"]
    assert x > 0.55 and x + w < 0.93 and h > 0.03  # right column, both lines of the split equation
    assert one["1"][3] > 0.035  # the fraction dρ/dt: both lines of it
    assert _overlap(one["2"], one["3"]) < 0.005  # align: two numbered lines, each its own region
    two = {e["number"]: e["rect"] for e in pdfregions.equation_regions(EQ_PDF, 2)}
    assert sorted(two) == ["2.1", "2.2", "2.3a", "2.3b", "2.4", "2.5", "A1"]
    assert two["2.2"][0] < 0.25 and two["2.2"][0] + two["2.2"][2] > 0.9  # wide: from "Z =" to the number
    assert two["2.5"][3] > 0.045  # the whole 3×3 matrix
    assert _overlap(two["2.4"], two["2.5"]) < 0.005
    for rect in list(one.values()) + list(two.values()):
        assert rect[2] > 0.03 and rect[3] < 0.1


def test_parse_label():
    p = pdfregions.parse_label
    assert p("Eq. (3)") == p("equation 3") == p("(3)") == p("eq3") == ("equation", "3")
    assert p("Eqs. (2.5)") == ("equation", "2.5") and p("(A1)") == ("equation", "A1")
    assert p("Fig. 2") == p("Figure 2") == ("figure", "2") and p("Table I") == ("table", "I")
    assert p("FIG. 3b") == ("figure", "3b") and p("3") is None and p("the model") is None


@needs_pdfium
def test_snapshot_equation_and_figure_by_number(lib, svc):
    import shutil

    shutil.copy(EQ_PDF, svc.config.pdf_dir / "eq.pdf")
    eq = svc.papers.create({"title": "Equations paper", "authors": "Eq, Al", "year": 2025, "citation_key": "eq2025"})
    svc.papers.add_attachment(eq["id"], "Full Text PDF", "eq.pdf")
    server = build_server(svc)
    reply = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "snapshot", "arguments": {"paper": "eq2025", "equation": "A1"}},
        }
    )
    info = json.loads(reply["result"]["content"][1]["text"])
    assert info["region"]["page"] == 2 and info["label"] == "Equation (A1)"  # page found without being told
    assert info["region"]["rect"] == next(
        e["rect"] for e in pdfregions.equation_regions(EQ_PDF, 2) if e["number"] == "A1"
    )
    assert info["markdown"].endswith("\n*Equation (A1): [@eq2025, p. 2]*")
    region = pdfpng.read_region((svc.vault.root / info["saved"]).read_bytes())
    assert region["page"] == 2
    for spelled in ("(3)", "Eq. 3", "3"):
        r = json.loads(call(server, "snapshot", paper="eq2025", equation=spelled)[1][1])
        assert r["label"] == "Equation (3)" and r["region"]["page"] == 1
    r = json.loads(call(server, "snapshot", paper="eq2025", figure="eq. (2.4)")[1][1])
    assert r["label"] == "Equation (2.4)"
    r = json.loads(call(server, "snapshot", paper="eq2025", figure="Fig. 1")[1][1])
    assert r["region"]["page"] == 1 and r["markdown"].endswith("*Figure 1: [@eq2025, p. 1]*")
    err, texts = call(server, "snapshot", paper="eq2025", page=2, equation="3")
    assert err and "No Equation (3) on page 2" in texts[0] and "Equation (2.1)" in texts[0]
    err, texts = call(server, "snapshot", paper="eq2025", equation="99")
    assert err and "not found" in texts[0]
    err, texts = call(server, "snapshot", paper="eq2025", rect=[0.1, 0.1, 0.2, 0.2])
    assert err and "page" in texts[0]
    reply = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "view_page", "arguments": {"paper": "eq2025", "page": 1}},
        }
    )
    v = json.loads(reply["result"]["content"][1]["text"])
    assert [e["label"] for e in v["equations"]][:2] == ["Equation (1)", "Equation (6)"]
    assert v["figures"][0]["box"] == 1 and v["equations"][0]["box"] == 2


def test_page_tools_without_pdfium(lib, monkeypatch):
    monkeypatch.setattr(pdfrender, "available", lambda: False)
    err, texts = call(lib["server"], "view_page", paper="fig2024", page=1)
    assert err and "pypdfium2" in texts[0]


# ======================================================================== the script
def test_stdio_server_end_to_end(lib, svc):
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "read_paper", "arguments": {"paper": "smith2021", "pages": "3"}},
        },
    ]
    p = subprocess.run(
        [sys.executable, str(ROOT / "mcp_server.py"), "--data", str(svc.config.data_dir)],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 0, p.stderr
    replies = [json.loads(l) for l in p.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert len(replies[1]["result"]["tools"]) == 19
    assert "tensor networks" in replies[2]["result"]["content"][0]["text"]


def test_script_config_and_missing_library(tmp_path):
    p = subprocess.run(
        [sys.executable, str(ROOT / "mcp_server.py"), "--config"], capture_output=True, text=True, timeout=30
    )
    assert p.returncode == 0 and '"mcpServers"' in p.stdout and "claude mcp add" in p.stdout
    assert str(ROOT / "mcp_server.py") in p.stdout
    p = subprocess.run(
        [sys.executable, str(ROOT / "mcp_server.py"), "--data", str(tmp_path / "nothing")],
        capture_output=True,
        text=True,
        timeout=30,
        input="",
    )
    assert p.returncode == 1 and "No library found" in p.stderr and p.stdout == ""
    assert not (tmp_path / "nothing").exists()


# ======================================================================== tasks
from backend.tasks.service import token  # noqa: E402


def test_task_tools(lib, svc):
    server = lib["server"]
    made = call_json(server, "create_task", title="Plot $\\chi$ vs $\\varepsilon$", due="2026-10-27", note="crispr")
    tid = made["created"]
    assert made["line"] == token(tid) and made["in_note"]["path"] == "projects/crispr.md"
    t = svc.task_store.get(tid)
    assert t["created_by"] == "ai" and t["due"] == "2026-10-27"
    # the note's change is Claude's, so the app can show it and undo it
    assert svc.note_service.history_of("projects/crispr.md")["entries"][0]["by"] == "ai"
    sub = call_json(server, "create_task", title="Check convergence", parent=tid, details="Up to $N=100$.")["created"]
    listed = call_json(server, "list_tasks")
    row = next(x for x in listed["tasks"] if x["id"] == tid)
    assert row["subtasks"] == "0/1 done" and row["in_notes"][0]["note"] == "projects/crispr.md"
    assert [x["id"] for x in call_json(server, "list_tasks", query="has:body")["tasks"]] == [sub]
    assert [x["id"] for x in call_json(server, "list_tasks", query="due:<2026-11-01")["tasks"]] == [tid]
    got = call_json(server, "get_task", task=token(tid))
    assert got["subtasks"][0]["title"] == "Check convergence" and got["in_notes"]
    up = call_json(server, "update_task", task=tid, status="done", with_subtasks=True, due="")
    assert up["status"] == "done" and up["also_changed"] == [sub]
    assert svc.task_store.get(tid)["due"] is None
    assert call_json(server, "list_tasks")["count"] == 0
    assert call_json(server, "list_tasks", include_done=True)["count"] == 2
    summary = json.loads(call(server, "get_note", note="crispr")[1][0])
    assert summary["tasks"][0]["id"] == tid and summary["tasks"][0]["status"] == "done"
    err, texts = call(server, "update_task", task=tid, due="soon")
    assert err and "not a date" in texts[0]
    assert call_json(server, "delete_task", task=tid)["deleted"] == [tid, sub]
    err, texts = call(server, "get_task", task="nope2345")
    assert err


def test_notes_cannot_show_tasks_that_do_not_exist(lib, svc):
    server = lib["server"]
    err, texts = call(server, "edit_note", note="crispr", append="\n{{task:zzzz9999}}\n")
    assert err and "is not a task" in texts[0]
    tid = svc.task_store.create("Real")["id"]
    err, texts = call(server, "edit_note", note="crispr", append=f"\n{token(tid)}\n")
    assert not err, texts


def test_unexpected_errors_do_not_reach_the_client(monkeypatch):
    from backend.mcp.protocol import Server, Tool

    def broken():
        raise OSError("/home/someone/private/library.sqlite: disk I/O error")

    server = Server("test", "1")
    server.add(Tool("broken", "Broken", "Always fails.", {"type": "object", "properties": {}}, broken))
    err, texts = call(server, "broken")
    assert err and "OSError" in texts[0] and "private" not in texts[0]

    def failing_ping(self, params):
        raise RuntimeError("token=private-secret")

    monkeypatch.setattr(Server, "_m_ping", failing_ping)
    reply = server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert "RuntimeError" in reply["error"]["message"] and "private-secret" not in reply["error"]["message"]
