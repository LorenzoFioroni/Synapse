# The MCP server

`mcp_server.py` gives AI apps (Claude Desktop, Claude Code, Codex, any other MCP client) access to
the library through the Model Context Protocol. Nothing in it is particular to one app: it speaks
the protocol, and what an app changes is recorded with that app's name
([Which AI app](#which-ai-app)). The app starts the script itself and talks to it over stdin and stdout.
It reads the same `data/` folder as the web app, which does not need to be running. It can read
items, PDFs, annotations, notes and reference lists, and it can write notes under strict rules. When the
user allows it in Settings → AI Access, it can also change the library (see
[Changing the library](#changing-the-library)).

The same tools are also served by the web app at `/mcp`, over HTTP, for an AI app on another
computer (see [Over HTTP](#over-http-mcp)). That needs the web app running and an access token.

[Architecture](architecture.md) shows where it sits. This page covers the code in `mcp_server.py`
and `backend/mcp/`.

| Module | What it does |
|---|---|
| `mcp_server.py` | The script: parses the flags, builds a `Services` for the data folder and serves over stdio. |
| `backend/mcp/protocol.py` | The JSON-RPC 2.0 message layer: `Server`, `Tool`, `ToolError`, argument checks, `serve_stdio`. |
| `backend/mcp/clients.py` | `client_label()`: the name of the connected AI app (Claude, Codex …) from `initialize`'s `clientInfo`. |
| `backend/library/agent.py` | `acting_as()` / `current_agent()`: the AI app making the current change, read by notes and tasks. |
| `backend/mcp/http.py` | `HttpTransport`: the same server over HTTP, for the web app's `/mcp` endpoint. |
| `backend/api/mcp.py` | The `/mcp` endpoint and the access-token endpoints of Settings → AI Access. |
| `backend/access/tokens.py` | `AccessTokens`: the tokens an AI app sends to `/mcp`, hashed in `auth.db`. |
| `backend/mcp/server.py` | `build_server()`: registers every tool with its title, description and argument schema, and the resources and prompts. |
| `backend/mcp/resources.py` | `Resources`: notes, papers and the folder overview as `synapse://` resources. |
| `backend/mcp/prompts.py` | `PROMPTS`: the ready-made requests of the AI app's prompt picker. |
| `backend/mcp/instructions.py` | `INSTRUCTIONS` (how to use the tools) and `WRITING_RULES` (the note syntax). |
| `backend/mcp/tools/` | The tools themselves, one mixin per family, combined in `LibraryTools`. |
| `backend/pdf/refsection.py` | `reference_spans()`: where a PDF's reference list starts and ends, so a hit inside it can be told from a hit in the paper (see [full-text search](fulltext-search.md#reference-lists)). |
| `backend/pdf/source.py` | `PdfSource`: a PDF a tool can read, either an attachment of an item or a PDF in the notes folders (path, label, title, how a page is cited, which kind it is). |
| `backend/mcp/notecheck.py` | `NoteChecker`: checks a note before it is saved. |

The package sits in the `mcp` layer: it may use `library`, `notes`, `pdf` and `references`, and
gets everything else through the `Services` object it is given.

## Setting it up

The README section [AI access (MCP)](../README.md#ai-access-mcp) has the steps for users.
In short, `python mcp_server.py --config` prints the entry for `claude_desktop_config.json`, the
`claude mcp add` line for Claude Code, and the `codex mcp add` line and `~/.codex/config.toml`
entry for Codex, with the paths of this checkout and Python. Any other MCP client runs the same
command over stdio. Codex starts the server with a reduced environment, so `--data` is written
into the command whenever the data folder was chosen (by `--data` or `SYNAPSE_DATA_DIR`).

| Flag | Effect |
|---|---|
| `--config` | Print the setup for Claude Desktop, Claude Code and Codex, then exit. |
| `--data DIR` | Use another data folder. The default is `$SYNAPSE_DATA_DIR` or `./data` (`Config.from_env`). |
| `--verbose` | Log at INFO level instead of WARNING. |

Logging always goes to stderr, because stdout carries the protocol. If `library.db` is missing,
the script prints a message to stderr and exits with status 1, without creating the folder.

## The protocol layer

`backend/mcp/protocol.py` implements only what a tools-only server needs. It does not depend on
the transport: `Server.handle(message)` takes one decoded JSON-RPC message (or a batch) and
returns the reply, or `None` for a notification. `serve_stdio` and `HttpTransport` are the two
transports that call it.

| Method | Answer |
|---|---|
| `initialize` | The protocol version the client asked for if it is in `PROTOCOL_VERSIONS`, else the newest; `capabilities` (`tools`, plus `resources` and `prompts` when the server has them, all with `listChanged: false`); `serverInfo`; `instructions`. |
| `ping` | `{}` |
| `tools/list` | Every registered tool, in registration order (`Tool.describe()`). |
| `tools/call` | Runs the tool and returns its content blocks. |
| `resources/list`, `resources/templates/list`, `resources/read` | The listed resources, the URI templates and the contents of one resource (see [Resources and prompts](#resources-and-prompts)). A URI that names nothing is the error `-32002`. |
| `prompts/list`, `prompts/get` | The prompts and the text of one, with its arguments filled in. An unknown prompt, an unknown argument or a missing required one is `-32602`. |

Errors are split in two kinds:

- **Protocol errors** are JSON-RPC errors: an invalid request (`-32600`), an unknown method
  (`-32601`), an unknown tool or non-object params or arguments (`-32602`), a parse error
  (`-32700`), or an internal error (`-32603`). One bad request never stops the server.
- **Tool errors** are results with `isError: true` and a readable text. A tool raises
  `ToolError` for a problem the model can fix (an unknown key, a bad page range). Any other
  exception in a tool is logged to stderr with its traceback and reported the same way, but only
  by its type ("The tool failed unexpectedly (OSError); …"): its text can carry file paths, SQL or
  URLs with API keys, and the client may be an AI app on another computer. Internal errors
  (`-32603`) likewise name only the exception type.

`check_args()` validates the arguments against the tool's flat schema before the call: unknown
names, required ones, basic types, `enum` and integer `minimum` and `maximum`. It is lenient where
clients often slip: an integer sent as a whole float or a numeric string is accepted, and a single
string where an array is expected becomes a one-element list.

A tool returns a string (sent as text), a dict or list (sent as indented JSON), an `Image` (sent
as a base64 image block) or `Parts`, a list of these sent as several blocks. `get_note`, for
example, returns a JSON summary followed by the note's Markdown.

`serve_stdio()` reads one JSON message per line and writes each reply as one line. While it runs,
`sys.stdout` points to stderr, because a stray `print` would corrupt the protocol stream.

## Tools

`build_server(services)` in `backend/mcp/server.py` creates one `LibraryTools` and registers its
methods. Each `Tool` has a name, a title, a description (what the model reads to decide when to
use it), an input schema, `read_only` and `open_world`. `read_only` sets the `readOnlyHint` and
`idempotentHint` annotations and `open_world` the `openWorldHint` (a tool that looks something up
online); `destructiveHint` is always false.

| Tool | Mixin | What it does | Writes |
|---|---|---|---|
| `search_library` | `library.py` | Searches items (metadata, PDF full text, what notes say) and notes, with the search box's filters. `offset` and `next_offset` page through results, `sort` orders by relevance, date added or year, `abstracts` adds the start of each abstract, `compact` gives one line per item (key, year, first author, title, and the date added when sorting by it) for listing a folder, `sort:added` written in the query is taken as the argument, and `warnings` names a filter that was searched as plain text (with the filter it probably meant: `venue:` → `journal:`), an `OR` that is not an operator, and a filter value that cannot match (a folder or tag that does not exist, with the close names; `type:foo`, `year:abc`). `has:note` and `is:review` (a guess) are filters too; `"quoted phrases"` stay together in notes and PDFs. `a OR b` between two terms finds either: `search.alternatives()` gives one query per combination (at most 8; a filter applies to each) and the results are joined. An item that has the words only in the reference list of its PDF (it cites the subject) is marked `only_in_references` and comes after the others, with their number in `items_only_in_references`; then come title matches, then authors and abstract. A single word that finds more than a page of items and is an author of only some of them gets a warning naming `author:`. Notes written by an AI app come after the user's own and are marked `written_by_ai`. Notes count as evidence about an item (and give its snippet) only with `scope` all, not with `scope="items"`; a title match ranks first, also across a hyphen ("time crystal" finds "Time-Crystalline"). An `author:` filter shows the matching author even when the long author list is cut. A query with several parts and no result gets a warning with the number of items each part matches alone, which names the part that rules everything out. `related_tags` names the user's tags that stand for the subject searched: a tag named like a search word, or one at least half of whose items (two or more) the words found, with its size (`tag:Transport has 8 items, 5 of them among these results`), since the user may file under another word papers that do not say the one searched; a tag whose items were all found has nothing to add and is left out, as is all of it when the query has a `tag:` filter. | no |
| `list_collections` | `library.py` | Folders with counts, item types, tags, smart folders, notes folders and overall counts; `include_notes` returns instead the notes' inventory: every note with title, type, author, size in bytes and last update, and the notes folders. The inventory lists the largest note first. | no |
| `get_note` | `notes.py` | A note's text and outline, the papers it cites (`lines` and `pages` where each is cited, not the surrounding text, which is in the returned text; each with title, year and PDF flag, the rest is in `get_paper`; `cited_papers` is empty rather than missing, and `cited_papers_scope` says whether it covers the whole note or only the part asked for), its links (one entry per target, with every line that links to it), backlinks and images (a gallery's caption on its first panel only), and the user's math macros. `section` returns only the text, citations, links and images under one heading, and `lines="120-300"` those of a range of lines (for a part that is not one section, or a section too big for one call); such a read leaves the outline out. A note over 30,000 characters is not returned whole: the result is its outline with each section's size and lines, `too_long`, the keys it cites (keys only) and `images_total`, until `section`, `lines` or `full=true` is given. A title or file name that fits two notes is an error listing their paths, never a guess. The `too_long` message lists the line ranges that read the whole note in turn (`lines='1-216', '217-'`, each within 30,000 characters) and offers `full=true` only for a note of at most 40,000 characters, which is what an AI app accepts in one result. A part longer than that (a section, `lines`, `full`) is cut at a line and names the `lines='N-'` that goes on. A link to one of the note's own headings is not listed among `links_to_notes`, and a line that links a note twice is listed once. | no |
| `search_in_note` | `notes.py` | The paragraphs of one note that contain some words, best first (every word, then a phrase, then the words within 300 characters of each other), each with its line numbers, the heading it is under and a snippet; the front matter is not searched. `get_note(lines=…)` then reads those lines. For a long note, whose text `get_note` does not return at once. | no |
| `view_image` | `images.py` | Images stored with the notes: one by path, or all of a note's (at most 6 per call); an image in a gallery comes with its panel label and the gallery's caption (given once). `offset` (with `note`) continues after the first 6; the result says `next_offset`. `paths` shows several chosen images (at most 6), and `list_only` (with `note`) lists every image of the note with its line, path and caption and no pictures, to choose from a note that has many. | no |
| `get_paper` | `papers.py` | Metadata, abstract (when none is stored, the one printed on the PDF's first page after an "Abstract" line, as `abstract_from_pdf`, marked approximate; else `no_abstract`), PDFs with page counts, the paper note, the notes citing it (one entry per note with its `lines`, the user's own before an AI app's, which are marked `written_by_ai`; the first 10, then `cited_in_notes_total`), a summary of its reference list. The printed abstract is also found after the "(Received …)" or "(Dated: …)" line under the authors, where physics journals and arXiv put it. `papers=[…]` (up to 20) gives instead a short summary per item with the whole abstract, to compare several in one call. The paper is named by citation key, item id, DOI or arXiv id (every tool that takes a paper accepts all four); an unknown key gets "Did you mean …?". With neither an "Abstract" line nor a date line (a preprint, Nature), the abstract is the first paragraph of running text under the title, authors and affiliations (`backend/pdf/text.py`, `_prose_after_header`): three lines of mostly lowercase words in a row, taken back to where the paragraph starts, up to a clearly shorter last line or to where the narrower columns of the body start. The suggestions for an unknown key put the same author first (the same year, then the nearest years), before keys that only look alike. | no |
| `get_paper_outline` | `papers.py` | A PDF's sections with the PDF page each starts on, and its figure and table captions with pages (`backend/pdf/outline.py`). The sections come from the PDF's bookmarks, else from its printed table of contents, whose printed page numbers are matched to PDF pages by locating the headings (a `warning` says when that fails and the printed numbers are given). A PDF with fewer than six entries (a letter, whose section headings are run into the paragraphs) is completed with headings recognised in the page text ("Introduction.—", "II. MODEL", "3 Results"), and `outline_from` says so. The page-text headings stop at a "References" line, and a reference's initial ("N. Gedik, …") is not a heading. A PDF with none gets a hint to use `search_in_paper`. A caption is a line that starts with "Figure N" / "Fig. N" / "Table N", also when the title touches the number ("Figure 14Luttinger"); "Fig.19(a)" at a line start is a reference. Its text is the first line and the lines that continue it, up to 220 characters. | no |
| `search_in_paper` | `papers.py` | The pages of a PDF that contain some words, best first, with snippets and the section the page is in (from the same outline as `get_paper_outline`; "Abstract" and "References" bookmarks never name a page). Pages with every word, then pages with the words within 300 characters of each other (`together`), rank above pages that only have them far apart. Hits inside the reference list do not count; a page that has the words only there comes last, marked `in_reference_list`, and `pages_in_reference_list` says how many there are. | no |
| `read_paper` | `papers.py` | The text of some PDF pages, marked by page (at most 10 pages or 40,000 characters, which is about what an AI app accepts from one tool call); it says which pages it read, which limit stopped it, and when the pages asked for run past the end. A page that reproduces another one (a figure taken from a journal repeats whole paragraphs) gives each line of 30 characters or more once, and a page still longer than 25,000 characters loses its middle; both are said in brackets after the page marker. | no |
| `view_page` | `papers.py` | An image of one page with a 0–1 grid, and proposed regions for figures, tables and equations; a caption whose drawing was not recognised (a vector figure) says so in `no_region`. A displayed equation's region stops before the sentence that follows it ("Here … is the"). Captions are given whole (see `snapshot`). | no |
| `snapshot` | `papers.py` | Cuts a figure, table, equation or rectangle into a PNG in `notes/attachments/` and returns the Markdown for it, with the caption printed in the PDF (`caption_in_pdf`). A rectangle given with `figure` or `equation` is named after it. An equation that is not found is an error listing the equation numbers that were recognised. The caption is the whole one, up to 1,500 characters: its first line and the lines right under it in the same column, up to the gap that ends the paragraph (`_whole_caption` in `backend/pdf/regions.py`). A line that starts with a reference ("Fig. 11 (Smith et al., 2021). It is…") is not a caption. | yes |
| `get_references` | `citations.py` | What a paper cites (library items marked) and/or the library items citing it. `compact` gives one line per work. Entries with no author, year or identifier (footnotes, text the source took for a reference) are counted in `not_listed`, not listed. When `compact` is not given, a list of more than 40 works comes one line per work and the result says so under `compact`; `compact=false` gives the fields. A list is also cut where it would pass `MAX_LISTED_CHARS` (35,000 characters, whatever `compact` says): `references_cut`, given before the list, says which ones are shown and the `offset` that continues. | no |
| `get_citation_graph` | `citations.py` | The neighbourhood of some papers (1 or 2 steps), or a library-wide overview of the most cited works. `limit` caps the node list (nearest first) as well as the ranked lists; shared foundations are still counted over the whole neighbourhood. Who-cites-whom edges are listed only with `include_edges`; `edges_total` always gives their number. The library-wide overview groups one work listed under several ids, gives `cited_by_count` and at most 10 citing keys, drops entries without a usable title, merges a title with its subtitled form ("Exploring the Quantum" / "… Atoms, Cavities, and Photons"), and lists a work whose title a library item has too under `in_library_under_another_record` instead of as missing (another edition, or a reference list matched to the wrong work). `compact` gives one line per work in both modes; in a neighbourhood `cited_by_several` counts within the neighbourhood and marks `same_title_in_library`. A work with no DOI or arXiv id on any record, with six or more records sharing its title, or whose records name different first authors (none of them has four fifths of the citations), is a likely wrong match: it comes after the identified works with a `check` text; `also_listed_as` shows two records and `also_listed_as_count` the rest, or, when the first authors differ, `records` lists the four most cited records with their counts. A title of three words or fewer ("Quantum Optics") carries a `check` too but keeps its place: sources match a book of that name to another work. An arXiv id assigned ten or more years after the work's year is not shown (it belongs to another paper). A wrong record that all citing papers agree on, under a longer title, cannot be seen from the stored data. As for `get_references`, more than 40 listed works (both ranked lists, or the nodes and the shared works) come one line per work unless `compact=false`. | no |
| `get_citing_works` | `citations.py` | The works citing a paper, looked up online at each call and not stored (`Citations.citing_works`, which asks the running plugins' citation sources: Reference Sources asks OpenAlex first for a paper with a journal DOI and Semantic Scholar first for a preprint, each the other's fallback). It knows citing works outside the library, which the stored graph (`get_references` direction `cited_by`) does not. `paper` is a library item or a DOI or arXiv id of any work; only that id is sent. `sort` is `cited` (most cited first) or `recent`; `since_year` keeps newer works (one without a year is kept); `limit` and `offset` page through at most 1,000. Library items are marked as in `get_references`; each work has its `cited_by_count`. `citing_total` is the source's count; `source_note` says when the source sent only part to sort (Semantic Scholar sends 1,000 at most). The same `compact` and `MAX_LISTED_CHARS` rules as `get_references`. | no |
| `get_annotations` | `editing.py` | What the user marked on a paper's PDF (or on a PDF kept in a notes folder, named by its path): highlights and underlines with their words, comments, text boxes and drawings, each with its page. | no |
| `write_note` | `notes.py` | Saves a new note under `notes/ai/`, or in a notes folder the user already has when `folder` names one. Line numbers in its error messages count the text that was sent, not the saved note with its front matter. A `warnings` entry says when another note has the same title, as `get_note` and `edit_note` then refuse the title and ask for the path. | yes |
| `edit_note` | `notes.py` | Changes an existing note: exact replacements, an append, or the whole text. | yes |
| `undo_note_edit` | `notes.py` | Takes the last AI edit back out of a note, keeping the user's later edits (refused when they touch the same lines). | yes |
| `list_tasks` | `tasks.py` | The user's tasks, open ones by default, filtered with the Tasks page's search syntax (`backend/tasks/query.py`); `count` includes sub-tasks (`sub_tasks_among_them`). | no |
| `get_task` | `tasks.py` | One task with its details, sub-tasks, parents and the notes that show it. | no |
| `create_task` | `tasks.py` | Creates a task (optionally a sub-task), and with `note` adds its `{{task:ID}}` line to that note through the note's history. | yes |
| `update_task` | `tasks.py` | Changes title, details, status (optionally with the sub-tasks), due date or parent. | yes |
| `delete_task` | `tasks.py` | Moves a task and its sub-tasks to the tasks trash; a task already there is reported as such. | yes |

`ToolsBase` in `backend/mcp/tools/common.py` has what the families share:

- `_paper(ref)` finds an item by citation key (also written `[@key]`), by id, by key ignoring
  case, or by DOI or arXiv id (a URL or `doi:` / `arXiv:` prefix is fine), and suggests close keys when
  nothing matches. Trashed items are found too and marked `in_trash`; a library item wins over a trashed one
  with the same key.
- `_pdf_for(item, attachment)` picks the PDF (the first one unless an attachment id is given),
  and `_pages(att)` extracts its text, cached for 16 PDFs by file time and size. `_page_count`
  asks `fulltext.db` before opening the PDF.
- `_note_path(ref)` finds a note by path, file name, `[[link]]` or title, and suggests similar
  notes when none matches.
- `_brief(item)` is the short form of an item used in every result.

Page numbers are PDF pages counted from 1 everywhere. They are the numbers `[@key, p. N]` uses in
notes, so the model's citations open the right page in the app. `view_page` and `snapshot` need
`pypdfium2` (`backend/pdf/render.py`); without it they return a tool error.

`instructions.py` holds the text sent with `initialize`: how to work with the tools (start from a
note or a search, find pages before reading them) and `WRITING_RULES`, the note syntax. The
descriptions of `write_note` and `edit_note` repeat `WRITING_RULES`, because the model reads tool
descriptions when it decides how to call them. See [notes](notes.md) for the syntax itself.

**Plugin tools.** Running plugins add tools of their own, named `<plugin id>__<name>` and described
with the plugin's name in brackets (see [plugins](plugins.md)). `build_server` sets `Server.extra`,
which `tools/list` and `tools/call` ask each time, so a plugin turned on in the app appears without
restarting the server (the MCP server follows `plugins.json`). Over `/mcp` (`remote=True`) only
plugins whose tools the user allowed for other computers take part. A plugin's `PluginError`
reaches the model as a tool error (`_readable`); anything else counts as the plugin failing.
Plugins' per-item fields reach the model too: `get_paper` lists their known values as
`plugin_fields`, and `search_library` takes them as filters (`citations:>100`).

Task changes made through the tools are recorded with `by: "ai"` and the app's name in
`task_history` (see below), so the app shows whose they are and can undo them; `get_note` lists the tasks a note shows, and `NoteChecker` refuses a
note that shows a task id that does not exist (see [tasks](tasks.md)).

## Changing the library

Seven more tools change the library. They are offered only while **Settings → AI Access → AI apps may
change the library** is on (`ai_library_edits`, off by default): `build_server` puts them in
`Server.extra`, which `tools/list` and `tools/call` ask every time, so the switch takes effect without
restarting the server. While it is off they are not listed and calling one is the "unknown tool"
error. A [companion](companion.md)'s AI tool gets five of them, and only when its companion allows it
too (see [Over HTTP](#over-http-mcp)). The setting is one of `ADMIN_KEYS`: it is changed on the computer running Synapse, or from a
device after a passkey confirmation, because it widens what every access token may do. The stdio
server reads `settings.json` when it starts, so there the AI app is restarted after flipping it.

| Tool | What it does | Network |
|---|---|---|
| `add_paper` | Adds an item by `identifier` (DOI, arXiv id or ISBN, looked up like Add by Identifier: `Services.add_by_identifier`) or by hand with `fields`; fields given with an identifier fill what the lookup left empty. The work the library already has is returned instead of added. `folder` must exist. | yes |
| `update_paper` | Sets fields of an item (`FIELDS_FOR_AI`: every editable field but the citation key, whose change rewrites notes and stays with the user). Returns each changed field before and after. | no |
| `organize_papers` | Adds and removes tags and folder memberships of up to 200 items. Folders are named by path (or by name when only one folder has it); an unknown one is an error with the close names unless `create_folders` is set. | no |
| `trash_papers` | Moves items to the Trash, or back with `restore`. | no |
| `fetch_references` | Fetches and stores an item's reference list (`Services.fetch_references_now`). | yes |
| `find_pdf` | Looks for an open-access PDF of an item that has none and attaches it (`Services.find_pdf`). | yes |
| `add_highlight` | Highlights a passage on a PDF page (also on a PDF of the notes folders, named by its path; it is one of the library-changing tools, so the same setting allows it), with an optional comment. `pdf/regions.py:quote_rects` finds the words on the page (letters and digits only, so line breaks and hyphenation do not matter) and gives one rectangle per line. | no |

What they cannot do: delete anything for good (items go to the Trash, which only the user empties),
change a citation key, remove or change an annotation, or fetch a URL the model gives (lookups go to
the metadata, reference and PDF sources the app already uses, by identifier). An item's fields have no
history, so `update_paper` returns the values before the change: that is the way back. The three
network tools have `openWorldHint: true`, as has the reading tool `get_citing_works`. Errors of a lookup reach the model as the lookup's own
sentence; anything else only by its type, since its text can hold request URLs with API keys.

## Resources and prompts

Besides tools, the server offers **resources**, which an AI app can attach by reference (an `@`
mention or an attachment menu, depending on the app), and **prompts**, ready-made requests the
user picks (a prompt picker or slash command, depending on the app). Both are read-only and give nothing the
tools do not: the same data, behind the same access token over `/mcp`.

| Resource | Contents |
|---|---|
| `synapse://collections` | What `list_collections` returns, as JSON. |
| `synapse://note/<path>` | A note's Markdown, up to `MAX_NOTE_CHARS`. `?section=Heading` gives only the text under that heading. The notes are listed (at most `MAX_LISTED_NOTES`, 200); any note is reachable through the template. |
| `synapse://paper/<key>` | JSON: the item's metadata and abstract (`get_paper`) and its outline (`get_paper_outline`, left out when it has no PDF). Papers are not listed (a library has hundreds); the template reaches them. |

| Prompt | What it asks for |
|---|---|
| `review_folder` (`folder`, optional `focus`) | A literature review of a folder, grouped by theme, with page citations; saved only if the user asks. |
| `summarise_paper` (`paper`) | A summary built on the user's paper note, saved as a note with `write_note`. |
| `compare_papers` (`papers`, optional `aspect`) | A table of differences with page citations, saying where a paper is silent. |
| `find_missing_literature` (optional `scope`) | The works several library papers cite that the library lacks, from `get_citation_graph`. |

A prompt is only text: it names the tools to use and how to cite, and nothing runs until the user
sends it. `test_prompts_only_name_tools_that_exist` fails when a prompt mentions a tool that does
not exist, so renaming a tool means updating `prompts.py`. Resource URIs are resolved by the
tools' own lookups (`_note_path`, `_paper`), so they cannot leave the notes folder.

## Which AI app

Every MCP client names itself in `initialize` (`clientInfo`: `{"name": "codex-mcp-client",
"title": "Codex"}`, `{"name": "claude-code"}` …). `Server` keeps `client_label(clientInfo)` in
`Server.client`: the apps people use are recognised (Claude, Codex, Cursor, Gemini, GitHub Copilot,
VS Code, Windsurf, Zed, LM Studio, ChatGPT), any other is shown by its `title`, else by its name
tidied up, and a client that gives no name stays `None`. `tools/call` runs the tool inside
`acting_as(Server.client)` (a context variable, so a web request handled meanwhile in another thread
is not affected), and the components that record changes read `current_agent()`:

| Where | What is recorded |
|---|---|
| Notes history (`notes/history/`) | The AI app as the commit's author and `Synapse-Agent` trailer, beside `Synapse-By: ai`; edit_note's default summary is "Edited by Codex". |
| New notes (`write_note`) | `agent: Codex` in the front matter, beside `author: ai`. |
| Tasks (`tasks/store.py`) | `tasks.updated_agent` and `task_history.agent`, beside `updated_by` / `by` = `ai`. |

The app shows the name: "Codex edited this note", "Codex changed this task", and each AI change in
a note's history. Changes recorded before the name was kept have no `agent`; the app then
says "An AI app". Over HTTP one `Server` answers every app, so `HttpTransport` remembers the name
each access token's `initialize` gave and sets `Server.client` from it for each request; after a
restart of the web app a client that does not initialize again is unnamed until it does.

Codex (CLI 0.164) was checked against both transports: it lists the tools (as one namespace,
`mcp__synapse`), calls them, and its changes are recorded as Codex's. The official MCP Python SDK
client works too.

## The rules it enforces

A [locked note](notes.md#locked-notes) is never read or changed by an AI app: the note tools
answer that it is locked (`NoteLocked` is a readable error, passed on by `protocol.py`),
`get_paper` leaves its paper note out, and searches, backlinks and citation lists never list it.

Unless the user turns on [Changing the library](#changing-the-library), the library itself is
read-only to the MCP server: no tool changes items, PDFs, annotations or reference lists. That
setting is about changes, not the network. One reading tool looks something up online:
`get_citing_works` sends a paper's DOI or arXiv id, and nothing else, to the citation sources
(OpenAlex, Semantic Scholar), as Fetch References does in the app; it stores nothing, and while Reference Sources is off it answers
that no citation sources are on. `get_references` only reads
`citations.db`; when a list was never fetched, it tells the model to fetch it in the app (see
[references](references.md)).

What it can write, and how:

- **New notes under `notes/ai/`, or in a folder the user has.** `write_note` builds the path from
  `AI_FOLDER` (`ai`), an optional sub-folder (each part slugified, `.` and `..` dropped) and the
  slugified title. When `folder` is the path of an existing notes folder (`Vault.folders()`, compared
  without case; never the images folder or a hidden one), the note is saved there instead, for a user
  who asks for a note "in the project folder". A folder that does not exist is made inside `ai/`, so
  an AI app never adds folders beside the user's own. It never
  overwrites: it picks a free name (`Title 2.md` …) and saves with no base hash, which fails if
  the file exists. The front matter gets an `id`, the title, `type: note`, `author: ai` and
  `created`.
- **Edits of existing notes, in any folder, through `edit_note`.** Each old passage must occur
  exactly once. The edit is hash-checked: the tool reads the note and its hash, and
  `NotesService.edit` refuses with `Conflict` if the file changed in between. On a conflict the
  tool reads the note again and reapplies the edit, up to three times, since the user may be
  saving that note at the same moment. A whole-text replacement keeps the note's front matter if
  the new text has none.
- **Every AI edit is a version of its own.** `NotesService.edit` (and `write_note`) records the
  change in the notes history (see [notes history](notes-history.md)): the user's edits still
  waiting are committed first, then the AI change alone, with its summary and the app's name. The
  MCP process and the web app take turns through a lock file, so neither takes the other's change
  for one made outside Synapse. The app shows the change and can undo it. Without Git, the change
  is kept the old way (one JSON file in `notes/.history/`).
- **Undo keeps the user's work.** `undo_note_edit` takes the last AI edit back out with a
  three-way merge: when the note is still exactly what the AI left, that is the text before;
  when the user changed it since, their changes stay (`kept_later_edits`). When they touched the
  same lines, the tool refuses and nothing changes.
- **Images only in `notes/attachments/`.** `snapshot` saves through `NotesService.add_image`, with a
  timestamped name. The web app moves images that no note uses to `.trash-attachments/` and
  deletes them later (`backend/notes/attachments.py`), so a discarded snapshot does not stay.
- **Checked notes.** `NoteChecker` (`backend/mcp/notecheck.py`) runs before every save. Links and
  `#headings` must resolve, citation keys and pages must exist, a quote's words must be on the cited
  page (a quote whose start or end is on the page but whose words in between differ is refused, naming the words), images must exist, and formulas must be LaTeX, not Unicode symbols. A link written with a
  note's title is rewritten to its file name when exactly one note has that title. Problems are
  errors and nothing is saved; things that work but look wrong (citing a trashed item) are
  warnings. For `edit_note`, only problems the change adds block it: the checker runs on the old
  and the new text, and errors already present are subtracted.

- **PDFs of the notes folders.** `paper` of `read_paper`, `search_in_paper`, `view_page`,
  `get_paper_outline`, `get_annotations` and `add_highlight` may name a PDF kept in a notes folder
  (see [notes](notes.md#pdfs-in-the-notes-folders)) by its path, file name or `[[folder/file.pdf#p=N]]`
  link: `ToolsBase._source` (a `paper` ending in `.pdf`) resolves it through the notes registry and the
  index's `files` table, and everything else goes through a `PdfSource`, whose key (`att:12` or
  `npdf:7`) keys the page cache. Answers use the path as the label, and pages are cited as
  `[[folder/file.pdf#p=N]]`; `attachment` is refused for such a PDF. Highlights are stored in
  `note_pdf_annotations`. `get_note` lists the PDFs a note links to (`linked_pdfs`: link, resolved path, lines, pages,
  `broken`), and `NoteChecker` requires a `[[file.pdf#p=N]]` to name an existing PDF and page (a heading is an error) and checks a
  block quote ending with such a link like one ending with a citation. `snapshot` and the library tools are unchanged.

The limits are constants in `common.py`: `MAX_PAGES_PER_READ` (10), `MAX_CHARS_PER_READ` (40,000),
`MAX_CHARS_PER_PAGE` (25,000), `MAX_LISTED_CHARS` (35,000, a list of references),
`MAX_NOTE_CHARS` (100,000, and an edit may grow a note to four times that), `MAX_PAPER_NOTE_CHARS`
(20,000) and `MAX_IMAGES_PER_VIEW` (6).

Whatever a tool returns, `text_content` in `protocol.py` cuts the text of one result at
`MAX_RESULT_CHARS` (50,000) at a line and appends what to do (a smaller `limit`, `compact`, a
narrower query): AI apps refuse results of about 25,000 tokens (measured: 56,000 characters and
more), which leaves the model with nothing. The tools bound their own output first, so this is the
last resort.

## Sharing `data/` with the web app

The MCP server is a separate process with its own `Services` over the same folder. There is no
connection between the two processes; they share files and SQLite databases.

**Start-up.** `Services(config)` opens the databases and runs the library migrations, as the web
app does. `mcp_server.py` then sets `note_service.on_change` to `None`, so the MCP process never
runs the app's background jobs such as the sweep of unused attachments. Settings are read from
`settings.json` once, when the server starts.

**Seeing the app's changes.** `build_server` sets `before_call=t.refresh`. Before a tool runs,
`refresh()` calls `notes.sync()`, at most every 2 seconds. The sync compares file times and sizes,
so it is cheap, and notes edited in the app are seen by the next tool call. Items are read from
`library.db` on every call; cached PDF text is keyed by file time and size.

**The app seeing the MCP server's changes.** The MCP server writes note files and updates
`notes_index.db` itself. The web app notices through its change feed (`backend/changes.py`): at
each `GET /api/changes` poll, `ChangeFeed` compares the time and size of `library.db` and
`notes_index.db` (and their `-wal` files) with what it last recorded, and reports `notes` or
`library` when they changed without going through the server. In the browser,
`frontend/src/changes.ts` then calls `notesChanged()`: the notes list is refreshed and the open
note runs `checkRemote()` (`frontend/src/views/notes/noteSession.ts`), which loads the new text
unless the user has unsaved changes. If the user is typing, the editor's own save is hash-checked
and the conflict dialog takes over. The bar over the note that shows the latest AI edit is
`frontend/src/views/notes/aiBar.ts`. See [sync and devices](sync-and-devices.md) for the change
feed.

## Over HTTP (`/mcp`)

An AI app on another computer cannot start `mcp_server.py` here, so the web app serves the same
tools at `/mcp` (the "Streamable HTTP" transport, in its simplest form). It needs `--lan` to be
reachable from the network; on this computer it works without.

```
AI app ──POST /mcp, Authorization: Bearer syn_…──► guard: host, Origin, token (access/guard.py)
                                                           └ api/mcp.py → Services.mcp_http()
                                                               └ HttpTransport.handle(body, caller=token id) → Server.handle
```

**Requests.** Each POST carries one JSON-RPC message or a batch, and the answer is the reply as
`application/json` (200), or an empty 202 when the body held only notifications. A body that is
not JSON answers 400 with a parse error that says where (line and column), not the decoder's text. A `MCP-Protocol-Version` header naming a version not in
`PROTOCOL_VERSIONS` answers 400. GET and DELETE answer 405: there is no event stream (every tool
answers at once) and no session, because the server keeps no state between requests.

**One at a time.** `HttpTransport` holds a lock around `Server.handle`, so tool calls run one after
another as they do over stdio; the tools' caches are not made for several threads. The server is
built on the first request, with the web app's own `Services`, so its writes go through the same
components as the app's.

**Change feed.** A library write made during a `/mcp` request is recorded like any other request
without a client id, so every window refreshes. After a tool with `read_only=False`, `HttpTransport`
calls the `wrote` callback, which `Services` points at `changes.record({"notes", "tasks"})`: notes
and tasks written in-process are reported at once. Without it a task change would go unseen: the
request's own record says `library` (`kind_of("/mcp")`), and that also explains the new file time of
`library.db`, so the file-time check finds nothing.

**Access tokens.** The guard lets a request to `/mcp` through only with
`Authorization: Bearer <token>` naming a token in `auth.db` (`AccessTokens.check`), from this
computer too; device cookies do not count. Otherwise it answers 401 with
`WWW-Authenticate: Bearer`. A request with an `Origin` other than the server's own answers 403,
so a web page cannot use a token the browser does not have anyway, and the Host check still stops
DNS rebinding. Tokens (`syn_` and 43 random characters) are made in **Settings → AI Access**
(`frontend/src/views/settings/aiAccess.ts`), on the computer running Synapse or on a signed-in device
after confirming with a passkey ([`remote-access.md`](remote-access.md)), shown once with
what to run on the other computer, and stored as SHA-256 hashes. The token box offers three apps:
Claude Code (`claude mcp add --transport http … --header "Authorization: Bearer …"`), Codex
(`export SYNAPSE_TOKEN=…` and `codex mcp add synapse --url … --bearer-token-env-var SYNAPSE_TOKEN`,
since Codex reads the token from an environment variable), and any other app (the URL and the
header to enter). `last_used` and `last_addr` are updated at most once a minute. Removing a token
locks its app out at the next request. MCP tokens cannot authenticate browser connector calls.
**New Companion Token…** creates a `companion`-scope token (`syn_companion_…`) for
[the companion](companion.md): it opens `/api/companion/…` and `/api/connector/…` (which the
companion forwards for the browser extension), and not `/mcp`. The AI tool the companion runs gets
a token of its own for each question instead (`Companions.job_access`), valid while that question
runs. On `/mcp` such a token is *restricted*: with `Server.edits` (set per request by
`HttpTransport.handle`), `build_server` offers it the tools that change the library only when
Settings → AI Access allows them **and** the companion's window allows them for its questions, and
then only `COMPANION_EDITS` (`add_paper`, `find_pdf`, `organize_papers`, `add_highlight`,
`trash_papers`); note and task tools are unchanged. Each of those calls that succeeds becomes a
`change` event of the question (`Companions.add_changes`), shown with the answer. Browser tokens
(`connector` scope) are no longer made or accepted; old ones are listed so they can be removed.
Stored tokens work until revoked. `AccessTokens.check` takes one scope or several.
The scopes share token listing and revocation, with their purpose shown in Settings. See [browser extension](browser-extension.md).

| Endpoint | Who | What |
|---|---|---|
| `POST /mcp` | a valid token | One JSON-RPC message or batch. |
| `GET /api/mcp/tokens` | this computer or a signed-in device | `this_computer`, `can_confirm`, `confirmed`, `lan`, `endpoints` (the `/mcp` URLs: the `--public-url` one first, then one per address under `--lan`), `tokens`. |
| `POST /api/mcp/tokens` | this computer, or a device after a passkey confirmation (else 403, with `confirm: true` when it can confirm) | `{name, scope?}` → 201 `{token, entry, tokens}`; `scope` defaults to `"mcp"` or can be `"connector"` or `"companion"`. Entries include `scope`; both scopes remain valid until revoked. The token is not kept. |
| `DELETE /api/mcp/tokens/<id>` | the same | Removes the token. |

Over plain HTTP the token crosses the network in the clear, like a device cookie: use `--lan` on a
trusted network, or HTTPS (`--cert`/`--key`; the AI app must then trust the certificate, for
example Claude Code with `NODE_EXTRA_CA_CERTS`). See [sync and devices](sync-and-devices.md#security-model).

## Adding a tool

1. **Write the method** on the mixin of its family in `backend/mcp/tools/` (`library.py`,
   `notes.py`, `images.py`, `papers.py`, `citations.py`, `editing.py`). A new family gets a new module with a
   mixin class that subclasses `ToolsBase` (so the type checker knows its helpers), added to the
   bases of `LibraryTools` in `backend/mcp/tools/__init__.py`. Keep it
   a thin layer over the backend: use the components on `self.s` and the `ToolsBase` helpers
   (`_paper`, `_pdf_for`, `_pages`, `_note_path`, `_brief`). Logic that the web app could use too
   belongs in its own package, not here.
2. **Report problems with `ToolError`**, worded so the model can fix its call ("Use
   search_library to find it."). Return a dict (keep it small with `drop_empty`), a string,
   `Image` or `Parts`.
3. **Register it** in `build_server()` (`backend/mcp/server.py`) with a `Tool(name, title,
   description, schema, method)`. The schema is a flat object of `string`, `integer`, `boolean`,
   `array` or `object` properties, with `enum`, `minimum`, `maximum` and `required` where they
   apply; `check_args` enforces those. Reuse `paper_arg` and `att_arg`. Pass `read_only=False` if
   the tool writes anything and `open_world=True` if it uses the network. The position in
   `build_server` is its position in `tools/list`. A tool that changes items, attachments or
   annotations goes in `_edit_tools()` instead, so that it follows the `ai_library_edits` setting.
4. **Follow the writing rules** if it writes: new notes only under `AI_FOLDER`, existing notes
   only through `NotesService.edit` with the hash that was read, and every note through
   `NoteChecker`.
5. **Tell the model** when to use it: in the description, and in `INSTRUCTIONS`
   (`backend/mcp/instructions.py`) if it changes how the tools work together.
6. **Test it** in `tests/test_mcp.py`: add the name to the list in `test_initialize_and_list`
   (and to the writing tools there if it writes), raise the tool count in
   `test_stdio_server_end_to_end`, and add a test that calls it with `call` or `call_json`.
7. **Document it** in the tools table of the README section
   [AI access (MCP)](../README.md#ai-access-mcp) and in the table on this page.

## Tests

`tests/test_mcp.py` tests the protocol and every tool. The `lib` fixture builds a small library
through the web app's `Services`: items, PDFs generated by `make_stream_pdf` (no external tools),
notes, a stored reference list, and a server from `build_server`. Most tests call
`Server.handle` in the same process. Others cover:

- protocol errors, batches and notifications, and a crashing tool reported as a tool error;
- `serve_stdio` with in-memory streams, checking that stray prints stay off stdout;
- the whole script: `mcp_server.py --data …` run as a subprocess with an `initialize`,
  `tools/list` and `tools/call` exchange, plus `--config` and a missing library;
- notes edited on disk being seen by the next tool call;
- the tools that change the library: absent until the setting is on, then each one, with the lookups
  replaced by fakes (no test uses the network).

`tests/test_refsection.py` covers finding reference lists, on made-up text.

`tests/test_mcp_http.py` covers `/mcp`: the tools from another computer with a token, 401 without
one (also from this computer and with a device cookie), foreign origins and hosts, bad bodies and
versions, making, listing and removing tokens, and a note written over HTTP reaching the change
feed. The browser checklist `passkeys` makes a token in Settings and uses it.

Tests that render pages are skipped when `pypdfium2` is not installed. See [testing](testing.md).

The five task tools are supplied by the Tasks contributed plugin, keeping their names and schemas.
They are offered to remote MCP clients by default, as before, and disappear while the plugin is
off. Task storage and validation of task IDs in note writes remain available in the core.
