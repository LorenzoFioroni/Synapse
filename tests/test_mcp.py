"""The MCP server: protocol handling and every library tool, in-process and over stdio."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.mcp import build_server
from backend.mcp.protocol import PROTOCOL_VERSIONS, Server, Tool, serve_stdio
from backend.mcp.tools import parse_pages, ToolError

ROOT = Path(__file__).resolve().parent.parent


def make_pages_pdf(path: Path, pages: list[str]) -> Path:
    """A PDF with one line of text per page (no external deps)."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    font = 3 + 2 * len(pages)
    for text in pages:
        page_no, content_no = len(objs) + 1, len(objs) + 2
        kids.append(page_no)
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_no} 0 R "
                    f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(pages)} >>".encode()
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
    s.indexer.sync(background=False)
    return {"smith": smith, "doe": doe, "lee": lee, "server": build_server(s)}


def call(server: Server, name: str, **args):
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    assert "result" in reply, reply
    res = reply["result"]
    texts = [c["text"] for c in res["content"]]
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
                     "read_paper", "get_references", "get_citation_graph", "write_note"]
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"]
        assert t["annotations"]["readOnlyHint"] == (t["name"] != "write_note")


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
    assert r["library"]["items"] == 3 and r["library"]["with_pdf"] == 1 and r["library"]["with_references"] == 2
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
    r = call_json(server, "write_note", title="Answer: dynamics", text="Based on [@smith2021, p. 2] and [@ghost1999].",
                  tags=["qa"])
    assert r["saved"] == "ai/Answer dynamics.md" or r["saved"].startswith("ai/")
    assert r["unknown_citation_keys"] == ["ghost1999"] and r["citations"] == 2
    text = svc.vault.read(r["saved"])[0]
    assert "author: ai" in text and "# Answer: dynamics" in text and "qa" in text
    assert svc.notes.note(r["saved"])["author"] == "ai"
    again = call_json(server, "write_note", title="Answer: dynamics", text="Second.")
    assert again["saved"] != r["saved"] and svc.vault.read(r["saved"])[0] == text   # never overwrites
    sneaky = call_json(server, "write_note", title="x", text="y", folder="../../etc")
    assert sneaky["saved"].startswith("ai/") and ".." not in sneaky["saved"]
    # the paper is now cited by the AI note too
    p = call_json(server, "get_paper", paper="doe2019")
    assert p["key"] == "doe2019"


def test_notes_edited_elsewhere_are_seen(lib, svc):
    server = lib["server"]
    (svc.vault.root / "fresh.md").write_text("# Fresh\n\nCites [@doe2019].\n")
    import time
    time.sleep(0.01)
    server.tools["get_note"].fn.__self__._last_sync = 0   # skip the 2 s throttle
    r = call_json(server, "search_library", query="fresh", scope="notes")
    assert [n["path"] for n in r["notes"]] == ["fresh.md"]


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
    assert len(replies[1]["result"]["tools"]) == 9
    assert "tensor networks" in replies[2]["result"]["content"][0]["text"]


def test_script_config_and_missing_library(tmp_path):
    p = subprocess.run([sys.executable, str(ROOT / "mcp_server.py"), "--config"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and '"mcpServers"' in p.stdout and "claude mcp add" in p.stdout
    assert str(ROOT / "mcp_server.py") in p.stdout
    p = subprocess.run([sys.executable, str(ROOT / "mcp_server.py"), "--data", str(tmp_path / "nothing")],
                       capture_output=True, text=True, timeout=30, input="")
    assert p.returncode == 1 and "No library found" in p.stderr and p.stdout == ""
    assert not (tmp_path / "nothing").exists()
