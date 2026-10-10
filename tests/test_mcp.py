"""The MCP server: protocol handling and every library tool, in-process and over stdio."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend import pdfrender

from backend.mcp import build_server
from backend.mcp.protocol import PROTOCOL_VERSIONS, Server, Tool, serve_stdio
from backend.mcp.tools import parse_pages, ToolError

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
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_no} 0 R "
                    f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode())
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
    s = "".join(_text(72, y, f"Body text line {i} of the introduction, describing the model in words.")
                for i, y in enumerate(range(740, 630, -14)))
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
    s += "".join(_text(72, y, f"Conclusion line {i}: the master equation remains valid in this regime.")
                 for i, y in enumerate(range(180, 80, -14)))
    return s


PAGES = ["Introduction to open quantum systems",
         "We derive the Lindblad master equation for the reduced density matrix",
         "Conclusion: tensor networks simulate the master equation efficiently"] + \
        [f"Appendix page {n} with filler text" for n in range(4, 13)]

PROJECT = """# Crispr project

The dynamics follow [@smith2021, p. 2] and earlier work [@doe2019].
A paper not in the library: [@missing2000].
See also [[other]].
"""


@pytest.fixture
def lib(svc):
    s = svc
    smith = s.papers.create({"title": "Open quantum dynamics", "authors": "Smith, John; Doe, Jane", "year": 2021,
                             "doi": "10.1000/smith", "abstract": "We study open systems.", "citation_key": "smith2021",
                             "journal": "Phys. Rev. A"})
    doe = s.papers.create({"title": "Earlier results", "authors": "Doe, Jane", "year": 2019, "doi": "10.1000/doe",
                           "citation_key": "doe2019"})
    lee = s.papers.create({"title": "Unrelated book", "authors": "Lee, Ann", "year": 2020, "citation_key": "lee2020",
                           "item_type": "book"})
    make_pages_pdf(s.config.pdf_dir / "smith.pdf", PAGES)
    s.papers.add_attachment(smith["id"], "Full Text PDF", "smith.pdf")
    folder = s.folders.create("Physics")
    sub = s.folders.create("Transport", folder["id"])
    s.folders.change_items([smith["id"], doe["id"]], add=[sub["id"]])
    s.tags.add([smith["id"]], ["quantum"])
    s.citations.store.save(smith["id"], "openalex", [
        {"doi": "10.1000/doe", "title": "Earlier results", "year": 2019},
        {"doi": "10.9999/ext", "title": "External foundation", "authors": "Old, Al", "year": 2010},
    ], doi="10.1000/smith")
    s.citations.store.save(doe["id"], "crossref", [{"doi": "10.9999/ext", "title": "External foundation"}],
                           doi="10.1000/doe")
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
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
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
    r = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}})
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
    assert names == ["search_library", "list_collections", "get_note", "get_paper", "search_in_paper",
                     "read_paper", "view_page", "snapshot", "get_references", "get_citation_graph", "write_note"]
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"]
        assert t["annotations"]["readOnlyHint"] == (t["name"] not in ("write_note", "snapshot"))
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
    assert err and "kaputt" in texts[0]


def test_stdio_loop_keeps_stdout_clean():
    import io

    def noisy():
        print("stray output")
        return "ok"
    server = Server("t", "0")
    server.add(Tool("noisy", "Noisy", "d", {"type": "object", "properties": {}}, noisy))
    lines = "\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "noisy"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"}]) + "\nnot json\n"
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
    doe = call_json(lib["server"], "get_paper", paper=lib["doe"]["id"])   # by item id; no PDF, no note
    assert doe["key"] == "doe2019" and doe["cited_by_in_library"] == ["smith2021"] and "pdfs" not in doe


def test_search_in_paper(lib):
    server = lib["server"]
    r = call_json(server, "search_in_paper", paper="smith2021", query='"master equation" lindblad')
    assert [x["page"] for x in r["results"]][:2] == [2, 3]
    assert "Lindblad master equation" in r["results"][0]["snippets"][0]
    assert r["pages_total"] == 12
    r = call_json(server, "search_in_paper", paper="smith2021", query="LINDBLÁD")   # case and accents ignored
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
    assert r["references"][1] == {"in_library": False, "title": "External foundation", "authors": "Old, Al",
                                  "year": 2010, "doi": "10.9999/ext"}
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
    r = call_json(server, "get_citation_graph", papers=["smith2021"])
    nodes = {n["node"]: n for n in r["nodes"]}
    assert nodes["smith2021"]["distance"] == 0 and nodes["doe2019"]["distance"] == 1
    assert "smith2021 -> doe2019" in r["edges"] and "doe2019 -> doi:10.9999/ext" in r["edges"]
    assert r["cited_by_several"][0]["node"] == "doi:10.9999/ext"
    r = call_json(server, "get_citation_graph", papers=["lee2020"], depth=2)
    assert r["seeds_without_reference_list"] == ["lee2020"]


def test_write_note(lib, svc):
    server = lib["server"]
    r = call_json(server, "write_note", title="Answer: dynamics", text="Based on [@smith2021, p. 2], with $t \\lesssim N$.",
                  tags=["qa"])
    assert r["saved"].startswith("ai/") and r["citations"] == 1 and r["link"].startswith("[[")
    text = svc.vault.read(r["saved"])[0]
    assert "author: ai" in text and "# Answer: dynamics" in text and "qa" in text
    assert svc.notes.note(r["saved"])["author"] == "ai"
    again = call_json(server, "write_note", title="Answer: dynamics", text="Second.")
    assert again["saved"] != r["saved"] and svc.vault.read(r["saved"])[0] == text   # never overwrites
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
    assert sorted(p.name for p in (svc.vault.root).rglob("*.md")) == before   # nothing written


def test_write_note_fixes_title_links(lib, svc):
    r = call_json(lib["server"], "write_note", title="Linked", text="See [[Crispr project]] and [[Crispr project#Crispr project|the project]].")
    text = svc.vault.read(r["saved"])[0]
    assert "[[crispr|Crispr project]]" in text and "[[crispr#Crispr project|the project]]" in text
    assert len(r["links_fixed"]) == 2
    assert svc.notes.outgoing(r["saved"])[0]["resolved"] == "projects/crispr.md"


def test_write_note_checks_quotes(lib, svc):
    server = lib["server"]
    ok = call_json(server, "write_note", title="Quoted",
                   text="> We derive the Lindblad master equation [@smith2021, p. 2]\n\nMath as $\\rho$, Unicode only in quotes:\n\n"
                        "> We derive the Lindblad master equation for the reduced density matrix ρ ≈ ρ' [@smith2021, p. 2]\n")
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
    server.tools["get_note"].fn.__self__._last_sync = 0   # skip the 2 s throttle
    r = call_json(server, "search_library", query="fresh", scope="notes")
    assert [n["path"] for n in r["notes"]] == ["fresh.md"]


# ======================================================================== pages as images
needs_pdfium = pytest.mark.skipif(not pdfrender.available(), reason="pypdfium2 not installed")


def test_png_region_roundtrip():
    png = pdfrender.png_encode(bytes([255, 0, 0] * 4), 2, 2, {pdfrender.REGION_KEYWORD: '{"page":3,"rect":[0.1,0.2,0.3,0.4]}'})
    assert png.startswith(b"\x89PNG") and pdfrender.read_region(png) == {"page": 3, "rect": [0.1, 0.2, 0.3, 0.4]}
    assert pdfrender.read_region(pdfrender.png_encode(b"\0\0\0", 1, 1)) is None


@needs_pdfium
def test_figure_regions(lib, svc):
    regions = pdfrender.figure_regions(svc.config.pdf_dir / "fig.pdf", 2)
    assert [r["label"] for r in regions] == ["Figure 2", "Table 1"]   # "Fig. 2 shows…" is running text
    x, y, w, h = regions[0]["rect"]
    # the chart (x 98…500 pt incl. its "0.5" label, y 412…600 pt from the bottom) without the caption
    assert abs(x - 98 / 612) < 0.02 and abs(x + w - 500 / 612) < 0.02
    assert abs(y - (792 - 600) / 792) < 0.02 and abs(y + h - (792 - 412) / 792) < 0.02
    tx, ty, tw, th = regions[1]["rect"]
    assert abs(ty - (792 - 295) / 792) < 0.02 and abs(ty + th - (792 - 215) / 792) < 0.02


@needs_pdfium
def test_view_page(lib):
    reply = lib["server"].handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "view_page", "arguments": {"paper": "fig2024", "page": 2}}})
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
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "snapshot", "arguments": {"paper": "fig2024", "page": 2, "figure": "Fig. 2"}}})
    content = reply["result"]["content"]
    assert content[0]["type"] == "image"
    info = json.loads(content[1]["text"])
    saved = svc.vault.root / info["saved"]
    assert info["saved"].startswith("attachments/") and saved.is_file()
    region = pdfrender.read_region(saved.read_bytes())
    assert region["page"] == 2 and region["rect"] == info["region"]["rect"]
    assert info["markdown"] == f"![fig2024 p. 2]({info['saved']})\n*Figure: [@fig2024, p. 2]*"
    note = call_json(server, "write_note", title="With figure", text="The chart:\n\n" + info["markdown"].replace(
        "*Figure: ", "*Figure 2: phase diagram "))
    assert note["saved"]
    err, texts = call(server, "snapshot", paper="fig2024", page=2, rect=[0.1, 0.6, 0.7, 0.15])
    assert not err and pdfrender.read_region((svc.vault.root / json.loads(texts[1])["saved"]).read_bytes())["rect"] == [0.1, 0.6, 0.7, 0.15]
    err, texts = call(server, "snapshot", paper="fig2024", page=2, figure="Figure 7")
    assert err and "Figure 2, Table 1" in texts[0]
    err, texts = call(server, "snapshot", paper="fig2024", page=2)
    assert err and "either figure" in texts[0]
    err, texts = call(server, "snapshot", paper="doe2019", page=1, rect=[0, 0, 1, 1])
    assert err and "no PDF" in texts[0]


def test_page_tools_without_pdfium(lib, monkeypatch):
    monkeypatch.setattr(pdfrender, "available", lambda: False)
    err, texts = call(lib["server"], "view_page", paper="fig2024", page=1)
    assert err and "pypdfium2" in texts[0]


# ======================================================================== the script
def test_stdio_server_end_to_end(lib, svc):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_paper",
                                                                      "arguments": {"paper": "smith2021", "pages": "3"}}},
    ]
    p = subprocess.run([sys.executable, str(ROOT / "mcp_server.py"), "--data", str(svc.config.data_dir)],
                       input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    replies = [json.loads(l) for l in p.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert len(replies[1]["result"]["tools"]) == 11
    assert "tensor networks" in replies[2]["result"]["content"][0]["text"]


def test_script_config_and_missing_library(tmp_path):
    p = subprocess.run([sys.executable, str(ROOT / "mcp_server.py"), "--config"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and '"mcpServers"' in p.stdout and "claude mcp add" in p.stdout
    assert str(ROOT / "mcp_server.py") in p.stdout
    p = subprocess.run([sys.executable, str(ROOT / "mcp_server.py"), "--data", str(tmp_path / "nothing")],
                       capture_output=True, text=True, timeout=30, input="")
    assert p.returncode == 1 and "No library found" in p.stderr and p.stdout == ""
    assert not (tmp_path / "nothing").exists()
