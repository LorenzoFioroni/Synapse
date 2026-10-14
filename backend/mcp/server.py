"""The MCP server: every library tool with its description and argument schema."""
from __future__ import annotations

from .instructions import INSTRUCTIONS, WRITING_RULES
from .protocol import Server, Tool
from .tools import LibraryTools
from .tools.common import MAX_IMAGES_PER_VIEW, MAX_PAGES_PER_READ


def build_server(services, version: str = "0.1.0") -> Server:
    t = LibraryTools(services)
    server = Server(name="synapse", version=version, instructions=INSTRUCTIONS, before_call=t.refresh)
    paper_arg = {"type": "string", "description": "Citation key of the item (e.g. \"smith2021\"; \"[@smith2021]\" also works) or its item id."}
    att_arg = {"type": "integer", "description": "Attachment id when the item has several PDFs (see get_paper). Default: the first PDF."}

    server.add(Tool(
        "search_library", "Search the library",
        "Search the user's library: items (metadata, PDF full text and what notes say about them) and notes. "
        "Supports filters: folder:\"Physics/Transport\" tag:x year:2018-2022 author:name has:pdf type:book, "
        "-term to exclude, \"quoted phrases\"; all terms must match. Returns items with citation keys and "
        "a matching snippet, and notes with paths. Use list_collections to see folder and tag names.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "Words and/or filters."},
            "scope": {"type": "string", "enum": ["all", "items", "notes"], "description": "What to search (default: all)."},
            "fulltext": {"type": "boolean", "description": "Also search the text of PDFs (default true)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Maximum results per kind (default 20)."},
        }, "required": ["query"]}, t.search_library))
    server.add(Tool(
        "list_collections", "List folders, tags and notes folders",
        "The library's folders (with item counts), tags, smart folders (saved searches) and notes folders, "
        "plus overall counts. Use the names in search_library filters.",
        {"type": "object", "properties": {}}, t.list_collections))
    server.add(Tool(
        "get_note", "Read a note",
        "Read a Markdown note by path (\"projects/crispr.md\") or title. Returns its text, the papers it cites "
        "(resolved to library items, with the line and page locator of each citation), its links to other notes "
        "and the notes linking to it. In notes, [@key] cites a paper and [[Title]] links a note.",
        {"type": "object", "properties": {
            "note": {"type": "string", "description": "Path in the notes folder, file name, or title."},
        }, "required": ["note"]}, t.get_note))
    server.add(Tool(
        "view_image", "Look at images in a note",
        "Show images stored with the notes (pasted pictures, figures cut from PDFs by snapshot): one by its path "
        "as the note writes it (\"attachments/x.png\"), or all images of a note (give note, at most "
        f"{MAX_IMAGES_PER_VIEW} per call). Each comes with its line, alt text and caption; a figure cut from a PDF "
        "also says which page and region it shows. Use it when a note's image matters for the answer; the files "
        "are in the app's notes folder and cannot be opened by path from elsewhere.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "Image path as written in the note, e.g. \"attachments/20261012-figure.png\"."},
            "note": {"type": "string", "description": "A note (path, file name or title): all its images, or with path the one it shows."},
        }}, t.view_image))
    server.add(Tool(
        "get_paper", "Get a paper's details",
        "Metadata and abstract of a library item, its PDFs (with page counts), the user's paper note about it, "
        "the lines of other notes that cite it, and a summary of its stored reference list.",
        {"type": "object", "properties": {"paper": paper_arg}, "required": ["paper"]}, t.get_paper))
    server.add(Tool(
        "search_in_paper", "Search inside a paper's PDF",
        "Find the pages of a paper's PDF that contain some words (case and accents ignored), best pages first, "
        "with snippets. Use it to locate the relevant pages, then read them with read_paper.",
        {"type": "object", "properties": {
            "paper": paper_arg,
            "query": {"type": "string", "description": "Words to look for; \"quoted phrases\" stay together."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Maximum pages to return (default 8)."},
            "attachment": att_arg,
        }, "required": ["paper", "query"]}, t.search_in_paper))
    server.add(Tool(
        "read_paper", "Read pages of a paper",
        f"The text of some pages of a paper's PDF, marked by page number (at most {MAX_PAGES_PER_READ} pages per call). "
        "Page numbers are PDF pages counted from 1, the numbers to use in [@key, p. N].",
        {"type": "object", "properties": {
            "paper": paper_arg,
            "pages": {"type": "string", "description": "Pages to read: \"3\", \"2-5\", \"1,4,7-9\" or \"10-\" (to the end). Default \"1-2\"."},
            "attachment": att_arg,
        }, "required": ["paper"]}, t.read_paper))
    server.add(Tool(
        "view_page", "Look at a page",
        "An image of one page of a paper's PDF (figures, tables, equations as they look), with a light 0–1 grid "
        "for reading off positions. Figure and table captions found on the page are listed, and the region of "
        "each figure/table is proposed (red numbered boxes) for snapshot.",
        {"type": "object", "properties": {
            "paper": paper_arg,
            "page": {"type": "integer", "minimum": 1, "description": "PDF page number (from 1)."},
            "attachment": att_arg,
        }, "required": ["paper", "page"]}, t.view_page))
    server.add(Tool(
        "snapshot", "Snapshot a figure, table or equation for a note",
        "Cut a figure, table or numbered equation of a PDF into an image in the notes folder, recording where it "
        "came from, and return the Markdown to put in a note; in the app, clicking its citation opens the PDF "
        "there and flashes the region. Prefer naming it: figure=\"Figure 2\" / \"Table 1\" (found by its caption) "
        "or equation=\"3\" for the equation numbered (3) (its lines and number, never cut in the middle); the "
        "page is found if you leave it out. Otherwise give page and rect=[x, y, w, h] (fractions of the page from "
        "the top left, read off view_page's grid). Returns a preview to check the crop.",
        {"type": "object", "properties": {
            "paper": paper_arg,
            "page": {"type": "integer", "minimum": 1, "description": "PDF page number (from 1). Needed with rect; "
                     "optional with figure or equation (default: search the PDF)."},
            "figure": {"type": "string", "description": "A figure or table by its caption label, e.g. \"Figure 2\", \"Fig. 3b\" or \"Table I\"."},
            "equation": {"type": "string", "description": "A numbered equation by its number as printed: \"3\", \"(2.5)\", \"A1\"."},
            "rect": {"type": "array", "items": {"type": "number"}, "description": "[x, y, w, h] as fractions of the page, origin top left."},
            "attachment": att_arg,
        }, "required": ["paper"]}, t.snapshot, read_only=False))
    server.add(Tool(
        "get_references", "Get a paper's references",
        "The works a paper cites (its fetched reference list; library items are marked and come with citation keys, "
        "others with DOI/arXiv ids) and/or the library items that cite it.",
        {"type": "object", "properties": {
            "paper": paper_arg,
            "direction": {"type": "string", "enum": ["cites", "cited_by", "both"],
                          "description": "\"cites\": its references (default); \"cited_by\": library items citing it; \"both\"."},
            "in_library_only": {"type": "boolean", "description": "Only list references that are in the library (default false)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Maximum references listed (default 200)."},
        }, "required": ["paper"]}, t.get_references))
    server.add(Tool(
        "get_citation_graph", "Explore the citation graph",
        "With papers: the citation neighbourhood around them (1 or 2 steps, both directions), with the works "
        "cited by several papers in it (shared foundations, possibly missing from the library). Without papers: "
        "a library-wide overview, the most cited works not in the library and the most cited library items.",
        {"type": "object", "properties": {
            "papers": {"type": "array", "items": {"type": "string"}, "description": "Citation keys to start from (optional)."},
            "depth": {"type": "integer", "minimum": 1, "maximum": 2, "description": "Steps from the starting papers (default 1)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Length of the ranked lists (default 40)."},
        }}, t.get_citation_graph))
    server.add(Tool(
        "write_note", "Save a new note",
        "Save a new Markdown note in the notes folder \"ai/\" (marked author: ai). Never overwrites: a free file "
        "name is chosen. The note is checked first and NOT saved if a link, citation, page, quote or image would "
        "be broken, or if formulas are written in Unicode instead of LaTeX: fix what the error lists and call again. "
        "Use only when the user asks to save something.\n\n" + WRITING_RULES,
        {"type": "object", "properties": {
            "title": {"type": "string", "description": "Title of the note (also its file name)."},
            "text": {"type": "string", "description": "Markdown body. A \"# Title\" heading is added if there is none."},
            "folder": {"type": "string", "description": "Optional subfolder inside ai/ (e.g. \"crispr\")."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags."},
        }, "required": ["title", "text"]}, t.write_note, read_only=False))
    server.add(Tool(
        "edit_note", "Change an existing note",
        "Change a note the user already has (any folder): replace exact passages (edits), add text at the end "
        "(append), or give the whole new text. Read it with get_note first and copy each old passage exactly. "
        "The change is checked like write_note (links, citations, pages, quotes, images, math) and refused if it "
        "adds a problem; problems already in the note do not block it. The version before the change is kept: the "
        "user sees what Claude changed in the app and can undo it (Cmd+Z in the open note, or from the note's list "
        "of Claude edits). Use only when the user asks to change the note.\n\n" + WRITING_RULES,
        {"type": "object", "properties": {
            "note": {"type": "string", "description": "Path in the notes folder, file name, or title."},
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "old": {"type": "string", "description": "Exact text now in the note, occurring once (add neighbouring words if needed)."},
                "new": {"type": "string", "description": "What replaces it (empty to delete it)."}},
                "required": ["old", "new"]},
                "description": "Replacements, applied in order."},
            "append": {"type": "string", "description": "Markdown to add at the end of the note."},
            "text": {"type": "string", "description": "The whole new note (instead of edits/append). Its front matter is kept if you leave it out."},
            "summary": {"type": "string", "description": "A few words on what changed, shown to the user with the change (e.g. \"Added Figure 2 and its discussion\")."},
        }, "required": ["note"]}, t.edit_note, read_only=False))
    server.add(Tool(
        "undo_note_edit", "Undo Claude's last edit of a note",
        "Put a note back as it was before Claude's last edit_note, if it was not changed since. Use when the user "
        "asks to revert it.",
        {"type": "object", "properties": {
            "note": {"type": "string", "description": "Path in the notes folder, file name, or title."},
        }, "required": ["note"]}, t.undo_note_edit, read_only=False))
    return server
