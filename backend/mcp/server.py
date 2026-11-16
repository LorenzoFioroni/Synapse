"""The MCP server: every library tool with its description and argument schema."""

from __future__ import annotations

from .instructions import INSTRUCTIONS, WRITING_RULES
from .prompts import PROMPTS
from .protocol import Server, Tool, ToolError
from .resources import TEMPLATES, Resources
from .tools import LibraryTools
from .tools.citations import AUTO_COMPACT
from .tools.common import MAX_IMAGES_PER_VIEW, MAX_PAGES_PER_READ
from .tools.editing import FIELDS_FOR_AI, LIBRARY_EDITS


def build_server(services, version: str = "0.1.0", *, remote: bool = False) -> Server:
    """The server with every library tool, plus the tools of running plugins (``remote``: for /mcp,
    where only plugins the user allowed for other computers take part)."""
    t = LibraryTools(services)
    server = Server(name="synapse", version=version, instructions=INSTRUCTIONS, before_call=t.refresh)
    resources = Resources(t)
    server.resource_list, server.resource_read, server.resource_templates = resources.listed, resources.read, TEMPLATES
    server.prompts = {p.name: p for p in PROMPTS}
    paper_arg = PAPER_ARG
    att_arg = ATT_ARG
    edits = _edit_tools(t)
    # Asked at every tools/list and tools/call: the tools that change the library come and go with their
    # setting, and a plugin's tools with the plugin, without restarting the server.
    server.extra = lambda: {
        **(edits if services.settings.get(LIBRARY_EDITS) else {}),
        **_plugin_tools(services, remote),
    }

    server.add(
        Tool(
            "search_library",
            "Search the library",
            "Search the user's library: items (metadata, PDF full text and, with scope all, what notes say about "
            "them) and notes. Filters (all ANDed; a leading - negates): "
            'folder:"Physics/Transport" (whole names) tag:x year:2018-2022 (also 2020- or >2015) '
            "added:2026-10 (also >2026-10-15, 2026-01..2026-03, 14d for the last 14 days) author:name title:word "
            'journal:"physical review" doi: key: type:book has:pdf has:abstract has:note (a note mentions it) '
            "is:review (a guess from journal, title and abstract; type:review is not a type), the fields plugins "
            "add (e.g. citations:>100, citations:none), "
            '"quoted phrases"; -term excludes; a OR b between two terms finds either (magic OR nonstabilizerness '
            "year:2024-). Returns items with citation "
            "keys and a matching snippet, and notes with paths (the user's own before those an AI app wrote); a "
            "filter that is unknown or cannot match (a folder "
            "that does not exist, type:foo, year:abc, venue:) is reported under warnings, with suggestions. An item "
            "that has the words only in its reference list (it cites the subject) is marked only_in_references and "
            "listed last. "
            "Use list_collections to see folder, tag and type names, and compact to list many items cheaply.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Words and/or filters."},
                    "scope": {
                        "type": "string",
                        "enum": ["all", "items", "notes"],
                        "description": "What to search (default: all).",
                    },
                    "fulltext": {"type": "boolean", "description": "Also search the text of PDFs (default true)."},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Maximum results per kind (default 20).",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Skip this many results per kind (use next_offset from the previous call).",
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["relevance", "added", "year"],
                        "description": "Order of items: relevance (default), newest added first, or newest year first.",
                    },
                    "abstracts": {
                        "type": "boolean",
                        "description": "Include the start of each item's abstract (default false); get_paper with "
                        "papers=[…] gives whole abstracts.",
                    },
                    "compact": {
                        "type": "boolean",
                        "description": "One line per item (key, year, first author, title) and no snippets: for "
                        "listing a folder or a long result (default false).",
                    },
                },
                "required": ["query"],
            },
            t.search_library,
        )
    )
    server.add(
        Tool(
            "list_collections",
            "List folders, tags and notes folders",
            "The library's folders (with item counts), tags, smart folders (saved searches) and notes folders, "
            "plus overall counts. Use the names in search_library filters. With include_notes it instead returns the "
            "notes only: every note with its title, type, author, size and last update.",
            {
                "type": "object",
                "properties": {
                    "include_notes": {
                        "type": "boolean",
                        "description": "Return the notes' inventory instead (path, title, size in bytes, last update; "
                        "default false).",
                    },
                },
            },
            t.list_collections,
        )
    )
    server.add(
        Tool(
            "get_note",
            "Read a note",
            'Read a Markdown note by path ("projects/crispr.md") or title. Returns its text, the papers it cites '
            "(resolved to library items, with the lines and pages where each is cited), its links to other notes "
            "and the notes linking to it. In notes, [@key] cites a paper and [[Title]] links a note. A long note "
            "is read one part at a time: above ~30,000 characters get_note returns only the outline (with each "
            "section's size, and cited_papers for the whole note); section or lines returns the text (and "
            "citations, links, images) of one part, and search_in_note finds the lines about a subject.",
            {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Path in the notes folder, file name, or title."},
                    "section": {
                        "type": "string",
                        "description": 'A heading of the note, e.g. "## Threshold" (the # marks are optional). '
                        "Default: the whole note.",
                    },
                    "lines": {
                        "type": "string",
                        "description": 'A range of line numbers, e.g. "120-300" (also "120-"): for a part that is not '
                        "one section, or a section too big for one call. Overrides the outline of a long note.",
                    },
                    "full": {
                        "type": "boolean",
                        "description": "Return a long note's whole text instead of its outline (default false). "
                        "Above ~50,000 characters the result is more than most AI apps accept: read by lines.",
                    },
                },
                "required": ["note"],
            },
            t.get_note,
        )
    )
    server.add(
        Tool(
            "search_in_note",
            "Search inside a note",
            "Find the paragraphs of one note that contain some words (case and accents ignored), best first, each "
            "with its line numbers, its section and a snippet. Use it on a long note to locate what it says about "
            "a subject, then read those lines with get_note(lines=…).",
            {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Path in the notes folder, file name, or title."},
                    "query": {"type": "string", "description": 'Words to look for; "quoted phrases" stay together.'},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum paragraphs to return (default 10).",
                    },
                },
                "required": ["note", "query"],
            },
            t.search_in_note,
        )
    )
    server.add(
        Tool(
            "view_image",
            "Look at images in a note",
            "Show images stored with the notes (pasted pictures, figures cut from PDFs by snapshot): one by its path "
            'as the note writes it ("attachments/x.png"), a few of them (paths), or all images of a note (give '
            f"note, at most {MAX_IMAGES_PER_VIEW} per call). Each comes with its line, alt text and caption; a figure "
            "cut from a PDF also says which page and region it shows. For a note with many images, list_only gives "
            "every image's path and caption without the pictures, to choose which to look at. Use it when a "
            "note's image matters for the answer; the files are in the app's notes folder and cannot be opened "
            "by path from elsewhere.",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": 'Image path as written in the note, e.g. "attachments/20261012-figure.png".',
                    },
                    "note": {
                        "type": "string",
                        "description": "A note (path, file name or title): all its images, or with path the one it shows.",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": MAX_IMAGES_PER_VIEW,
                        "description": "Several image paths as written in the note, instead of path.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "With note: start at this image (next_offset from the previous call).",
                    },
                    "list_only": {
                        "type": "boolean",
                        "description": "With note: list all its images (line, path, caption) without showing them "
                        "(default false).",
                    },
                },
            },
            t.view_image,
        )
    )
    server.add(
        Tool(
            "get_paper",
            "Get a paper's details",
            "Metadata and abstract of a library item (when none is stored, the one printed on its PDF's first page, marked approximate), its PDFs (with page counts), the user's paper note about it, "
            "the notes that cite it (one entry per note with its lines; the first 10), a summary of its stored reference list, and values plugins "
            "attach to it (plugin_fields, e.g. a citation count). To compare several items give papers=[…] "
            "(up to 20): each comes back with its whole abstract and nothing else.",
            {
                "type": "object",
                "properties": {
                    "paper": paper_arg,
                    "papers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                        "description": "Several items (keys, ids, DOIs or arXiv ids) for a short summary each, "
                        "instead of paper.",
                    },
                },
            },
            t.get_paper,
        )
    )
    server.add(
        Tool(
            "get_paper_outline",
            "Get a paper's outline",
            "The sections of a paper's PDF with the PDF page each starts on (from the PDF's bookmarks, else its "
            "printed table of contents), and its figure and table captions with their pages. Use it on a long "
            "paper to see its structure, then read_paper the pages you need.",
            {
                "type": "object",
                "properties": {
                    "paper": paper_arg,
                    "attachment": att_arg,
                    "captions": {
                        "type": "boolean",
                        "description": "Also list the figure and table captions (default true).",
                    },
                },
                "required": ["paper"],
            },
            t.get_paper_outline,
        )
    )
    server.add(
        Tool(
            "search_in_paper",
            "Search inside a paper's PDF",
            "Find the pages of a paper's PDF that contain some words (case and accents ignored), best pages first, "
            "with snippets and the section. A page with all the words close together "
            "ranks above one that has them far apart; a page that has them only in the reference list (works the "
            "paper cites) comes last, marked in_reference_list. Use it to locate the relevant pages, then read "
            "them with read_paper.",
            {
                "type": "object",
                "properties": {
                    "paper": paper_arg,
                    "query": {"type": "string", "description": 'Words to look for; "quoted phrases" stay together.'},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum pages to return (default 8).",
                    },
                    "attachment": att_arg,
                },
                "required": ["paper", "query"],
            },
            t.search_in_paper,
        )
    )
    server.add(
        Tool(
            "read_paper",
            "Read pages of a paper",
            f"The text of some pages of a paper's PDF, marked by page number (at most {MAX_PAGES_PER_READ} pages per call). "
            "Page numbers are PDF pages counted from 1, the numbers to use in [@key, p. N].",
            {
                "type": "object",
                "properties": {
                    "paper": paper_arg,
                    "pages": {
                        "type": "string",
                        "description": 'Pages to read: "3", "2-5", "1,4,7-9" or "10-" (to the end). Default "1-2".',
                    },
                    "attachment": att_arg,
                },
                "required": ["paper"],
            },
            t.read_paper,
        )
    )
    server.add(
        Tool(
            "view_page",
            "Look at a page",
            "An image of one page of a paper's PDF (figures, tables, equations as they look), with a light 0–1 grid "
            "for reading off positions. Figure and table captions found on the page are listed, and the region of "
            "each figure/table is proposed (red numbered boxes) for snapshot.",
            {
                "type": "object",
                "properties": {
                    "paper": paper_arg,
                    "page": {"type": "integer", "minimum": 1, "description": "PDF page number (from 1)."},
                    "attachment": att_arg,
                },
                "required": ["paper", "page"],
            },
            t.view_page,
        )
    )
    server.add(
        Tool(
            "snapshot",
            "Snapshot a figure, table or equation for a note",
            "Cut a figure, table or numbered equation of a PDF into an image in the notes folder, recording where it "
            "came from, and return the Markdown to put in a note; in the app, clicking its citation opens the PDF "
            'there and flashes the region. Prefer naming it: figure="Figure 2" / "Table 1" (found by its caption) '
            'or equation="3" for the equation numbered (3) (its lines and number, never cut in the middle); the '
            "page is found if you leave it out. Otherwise give page and rect=[x, y, w, h] (fractions of the page from "
            "the top left, read off view_page's grid); adding figure or equation to a rect only names the cut "
            '("Figure 2" in its caption line). Returns a preview to check the crop, and the caption printed in '
            "the PDF.",
            {
                "type": "object",
                "properties": {
                    "paper": paper_arg,
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "PDF page number (from 1). Needed with rect; "
                        "optional with figure or equation (default: search the PDF).",
                    },
                    "figure": {
                        "type": "string",
                        "description": 'A figure or table by its caption label, e.g. "Figure 2", "Fig. 3b" or "Table I".',
                    },
                    "equation": {
                        "type": "string",
                        "description": 'A numbered equation by its number as printed: "3", "(2.5)", "A1".',
                    },
                    "rect": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "[x, y, w, h] as fractions of the page, origin top left.",
                    },
                    "attachment": att_arg,
                },
                "required": ["paper"],
            },
            t.snapshot,
            read_only=False,
        )
    )
    server.add(
        Tool(
            "get_references",
            "Get a paper's references",
            "The works a paper cites (its fetched reference list; library items are marked and come with citation keys, "
            "others with DOI/arXiv ids) and/or the library items that cite it. A long list comes one line per work "
            "unless compact=false.",
            {
                "type": "object",
                "properties": {
                    "paper": paper_arg,
                    "direction": {
                        "type": "string",
                        "enum": ["cites", "cited_by", "both"],
                        "description": '"cites": its references (default); "cited_by": library items citing it; "both".',
                    },
                    "in_library_only": {
                        "type": "boolean",
                        "description": "Only list references that are in the library (default false).",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "description": "Maximum references listed (default 200).",
                    },
                    "compact": {
                        "type": "boolean",
                        "description": "One line per work (key or DOI/arXiv id, year, first author, title) instead "
                        f"of its fields. Default: true when more than {AUTO_COMPACT} works are listed, else false.",
                    },
                },
                "required": ["paper"],
            },
            t.get_references,
        )
    )
    server.add(
        Tool(
            "get_citation_graph",
            "Explore the citation graph",
            "With papers: the citation neighbourhood around them (1 or 2 steps, both directions), with the works "
            "cited by several papers in it (shared foundations, possibly missing from the library). Without papers: "
            "a library-wide overview, the most cited works not in the library and the most cited library items.",
            {
                "type": "object",
                "properties": {
                    "papers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Citation keys to start from (optional).",
                    },
                    "depth": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2,
                        "description": "Steps from the starting papers (default 1).",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "description": "Length of the ranked lists and of the node list, nearest first (default 40).",
                    },
                    "include_edges": {
                        "type": "boolean",
                        "description": "Also list who cites whom among the listed nodes (long; default false).",
                    },
                    "compact": {
                        "type": "boolean",
                        "description": "One line per work instead of its fields. Default: true when more than "
                        f"{AUTO_COMPACT} works are listed, else false.",
                    },
                },
            },
            t.get_citation_graph,
        )
    )
    server.add(
        Tool(
            "get_annotations",
            "Read a paper's highlights and comments",
            "What the user marked on a paper's PDF in the app: highlights and underlines with their words, "
            "comments, text boxes and drawings, each with its page. Use it to see what the user found important "
            "in a paper.",
            {"type": "object", "properties": {"paper": paper_arg, "attachment": att_arg}, "required": ["paper"]},
            t.get_annotations,
        )
    )
    server.add(
        Tool(
            "write_note",
            "Save a new note",
            'Save a new Markdown note (marked author: ai), in the notes folder "ai/" unless folder names another. '
            "Never overwrites: a free file "
            "name is chosen. The note is checked first and NOT saved if a link, citation, page, quote or image would "
            "be broken, or if formulas are written in Unicode instead of LaTeX: fix what the error lists and call again. "
            "Use only when the user asks to save something.\n\n" + WRITING_RULES,
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the note (also its file name)."},
                    "text": {
                        "type": "string",
                        "description": 'Markdown body. A "# Title" heading is added if there is none.',
                    },
                    "folder": {
                        "type": "string",
                        "description": 'Where to save it: a notes folder by its path (e.g. "Projects/Transport", see '
                        'list_collections), when the user names one. Default: "ai". A folder that does not exist is '
                        "made inside ai/.",
                    },
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags."},
                },
                "required": ["title", "text"],
            },
            t.write_note,
            read_only=False,
        )
    )
    server.add(
        Tool(
            "edit_note",
            "Change an existing note",
            "Change a note the user already has (any folder): replace exact passages (edits), add text at the end "
            "(append), or give the whole new text. Read it with get_note first and copy each old passage exactly. "
            "The change is checked like write_note (links, citations, pages, quotes, images, math) and refused if it "
            "adds a problem; problems already in the note do not block it. The version before the change is kept: the "
            "user sees what you changed in the app and can undo it (Cmd+Z in the open note, or from the note's list "
            "of AI edits). Use only when the user asks to change the note.\n\n" + WRITING_RULES,
            {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Path in the notes folder, file name, or title."},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old": {
                                    "type": "string",
                                    "description": "Exact text now in the note, occurring once (add neighbouring words if needed).",
                                },
                                "new": {"type": "string", "description": "What replaces it (empty to delete it)."},
                            },
                            "required": ["old", "new"],
                        },
                        "description": "Replacements, applied in order.",
                    },
                    "append": {"type": "string", "description": "Markdown to add at the end of the note."},
                    "text": {
                        "type": "string",
                        "description": "The whole new note (instead of edits/append). Its front matter is kept if you leave it out.",
                    },
                    "summary": {
                        "type": "string",
                        "description": 'A few words on what changed, shown to the user with the change (e.g. "Added Figure 2 and its discussion").',
                    },
                },
                "required": ["note"],
            },
            t.edit_note,
            read_only=False,
        )
    )
    server.add(
        Tool(
            "undo_note_edit",
            "Undo the last AI edit of a note",
            "Take the last edit_note by an AI app back out of a note, keeping the edits made after it (refused when they "
            "touch the same lines). Use when the user asks to revert it.",
            {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Path in the notes folder, file name, or title."},
                },
                "required": ["note"],
            },
            t.undo_note_edit,
            read_only=False,
        )
    )
    return server


PAPER_ARG = {
    "type": "string",
    "description": 'Citation key of the item (e.g. "smith2021"; "[@smith2021]" also works), its item id, '
    "or its DOI or arXiv id.",
}
ATT_ARG = {
    "type": "integer",
    "description": "Attachment id when the item has several PDFs (see get_paper). Default: the first PDF.",
}
PAPERS_ARG = {
    "type": "array",
    "items": {"type": "string"},
    "description": "The items: citation keys, ids, DOIs or arXiv ids.",
}


def _edit_tools(t: LibraryTools) -> dict[str, Tool]:
    """The tools that change the library, offered while Settings → AI Access allows it."""
    strings = lambda what: {"type": "array", "items": {"type": "string"}, "description": what}
    tools = [
        Tool(
            "add_paper",
            "Add a paper to the library",
            "Add an item to the library: by identifier (a DOI, arXiv id or ISBN, whose metadata is looked up "
            "online), or by hand with fields. Nothing is added when the library already has the work (it is "
            "returned instead). Use when the user asks to add a paper, e.g. one that get_references or "
            "get_citation_graph showed missing.",
            {
                "type": "object",
                "properties": {
                    "identifier": {
                        "type": "string",
                        "description": 'A DOI, arXiv id or ISBN, e.g. "10.1103/PhysRevLett.1.1".',
                    },
                    "fields": {
                        "type": "object",
                        "description": "Metadata given by hand (needs a title), or to fill what the lookup leaves "
                        f"empty: {', '.join(FIELDS_FOR_AI)}.",
                    },
                    "folder": {
                        "type": "string",
                        "description": 'An existing folder to put it in, by path ("Physics/Transport").',
                    },
                    "tags": strings("Tags to give it."),
                    "fetch_references": {
                        "type": "boolean",
                        "description": "Also fetch its reference list (default: as the user's settings say).",
                    },
                },
            },
            t.add_paper,
            read_only=False,
            open_world=True,
        ),
        Tool(
            "update_paper",
            "Correct a paper's details",
            "Change fields of a library item (title, authors, year, abstract, journal, DOI …). Returns each "
            "changed field's value before and after; the app keeps no history of fields, so that is the way "
            "back. Use when the user asks to fix or complete an item. Tags and folders: organize_papers.",
            {
                "type": "object",
                "properties": {
                    "paper": PAPER_ARG,
                    "fields": {
                        "type": "object",
                        "description": f"The fields to set (an empty string clears one): {', '.join(FIELDS_FOR_AI)}. "
                        'Authors are written "Last, First; Last, First".',
                    },
                },
                "required": ["paper", "fields"],
            },
            t.update_paper,
            read_only=False,
        ),
        Tool(
            "organize_papers",
            "Tag papers and put them in folders",
            "Add or remove tags and folder memberships of one or several items at once. An item can be in several "
            "folders; taking it out of one never deletes it. Folders are named by path (see list_collections).",
            {
                "type": "object",
                "properties": {
                    "papers": PAPERS_ARG,
                    "add_tags": strings("Tags to add (made if new)."),
                    "remove_tags": strings("Tags to take off these items."),
                    "add_folders": strings('Folders to put the items in, by path ("Physics/Transport").'),
                    "remove_folders": strings("Folders to take the items out of."),
                    "create_folders": {
                        "type": "boolean",
                        "description": "Make a folder of add_folders that does not exist (default false: an unknown "
                        "folder is an error, with the close names).",
                    },
                },
                "required": ["papers"],
            },
            t.organize_papers,
            read_only=False,
        ),
        Tool(
            "trash_papers",
            "Move papers to the Trash",
            "Move items to the library's Trash, or bring them back with restore. Nothing is deleted for good: only "
            "the user empties the Trash. Use only when the user asks to remove items.",
            {
                "type": "object",
                "properties": {
                    "papers": PAPERS_ARG,
                    "restore": {
                        "type": "boolean",
                        "description": "Bring these items back from the Trash (default false).",
                    },
                },
                "required": ["papers"],
            },
            t.trash_papers,
            read_only=False,
        ),
        Tool(
            "fetch_references",
            "Fetch a paper's reference list",
            "Fetch the list of works an item cites from the online sources (it needs a DOI or arXiv id) and store "
            "it, replacing the stored one. Use when get_references says the list was never fetched.",
            {"type": "object", "properties": {"paper": PAPER_ARG}, "required": ["paper"]},
            t.fetch_references,
            read_only=False,
            open_world=True,
        ),
        Tool(
            "find_pdf",
            "Find a PDF for a paper",
            "Look online for an open-access PDF of an item that has none, and attach it when one is found and it "
            "is that paper.",
            {"type": "object", "properties": {"paper": PAPER_ARG}, "required": ["paper"]},
            t.find_pdf,
            read_only=False,
            open_world=True,
        ),
        Tool(
            "add_highlight",
            "Highlight a passage in a paper",
            "Highlight some words on a page of a paper's PDF, as the user does in the app, optionally with a "
            "comment. The words must be on that page: copy one passage from read_paper. Use when the user asks to "
            "mark passages; get_annotations lists what is marked.",
            {
                "type": "object",
                "properties": {
                    "paper": PAPER_ARG,
                    "page": {"type": "integer", "minimum": 1, "description": "PDF page number (from 1)."},
                    "quote": {"type": "string", "description": "The exact words to highlight, from one page."},
                    "comment": {"type": "string", "description": "A comment to attach (optional)."},
                    "color": {
                        "type": "string",
                        "description": "Colour as #rrggbb (default: the user's highlight colour).",
                    },
                    "attachment": ATT_ARG,
                },
                "required": ["paper", "page", "quote"],
            },
            t.add_highlight,
            read_only=False,
        ),
    ]
    return {tool.name: tool for tool in tools}


def _plugin_tools(services, remote: bool) -> dict[str, Tool]:
    host = getattr(services, "plugins", None)
    if host is None:
        return {}
    return {
        spec.name: Tool(spec.name, spec.title, spec.description, spec.schema, _readable(spec.fn), spec.read_only)
        for spec in host.mcp_tools(remote=remote)
    }


def _readable(fn):
    """A plugin's ``PluginError`` reaches the AI app as a tool error it can read and act on."""

    def call(**kwargs):
        try:
            return fn(**kwargs)
        except Exception as e:
            if getattr(e, "readable", False):
                raise ToolError(str(e)) from e
            raise

    return call
