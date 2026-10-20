# The MCP server

`mcp_server.py` gives AI apps (Claude Desktop, Claude Code) access to the library through the
Model Context Protocol. The app starts the script itself and talks to it over stdin and stdout.
It reads the same `data/` folder as the web app, which does not need to be running. It can read
items, PDFs, notes and reference lists, and it can write notes under strict rules.

The same tools are also served by the web app at `/mcp`, over HTTP, for an AI app on another
computer (see [Over HTTP](#over-http-mcp)). That needs the web app running and an access token.

[Architecture](architecture.md) shows where it sits. This page covers the code in `mcp_server.py`
and `backend/mcp/`.

| Module | What it does |
|---|---|
| `mcp_server.py` | The script: parses the flags, builds a `Services` for the data folder and serves over stdio. |
| `backend/mcp/protocol.py` | The JSON-RPC 2.0 message layer: `Server`, `Tool`, `ToolError`, argument checks, `serve_stdio`. |
| `backend/mcp/http.py` | `HttpTransport`: the same server over HTTP, for the web app's `/mcp` endpoint. |
| `backend/api/mcp.py` | The `/mcp` endpoint and the access-token endpoints of Settings → AI Access. |
| `backend/access/tokens.py` | `AccessTokens`: the tokens an AI app sends to `/mcp`, hashed in `auth.db`. |
| `backend/mcp/server.py` | `build_server()`: registers every tool with its title, description and argument schema. |
| `backend/mcp/instructions.py` | `INSTRUCTIONS` (how to use the tools) and `WRITING_RULES` (the note syntax). |
| `backend/mcp/tools/` | The tools themselves, one mixin per family, combined in `LibraryTools`. |
| `backend/mcp/notecheck.py` | `NoteChecker`: checks a note before it is saved. |

The package sits in the `mcp` layer: it may use `library`, `notes`, `pdf` and `references`, and
gets everything else through the `Services` object it is given.

## Setting it up

The README section [AI access (MCP)](../README.md#ai-access-mcp) has the steps for users.
In short, `python mcp_server.py --config` prints the entry for `claude_desktop_config.json` and
the `claude mcp add` line for Claude Code, with the paths of this checkout and Python.

| Flag | Effect |
|---|---|
| `--config` | Print the setup for Claude Desktop and Claude Code, then exit. |
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
| `initialize` | The protocol version the client asked for if it is in `PROTOCOL_VERSIONS`, else the newest; `capabilities: {tools: {listChanged: false}}`; `serverInfo`; `instructions`. |
| `ping` | `{}` |
| `tools/list` | Every registered tool, in registration order (`Tool.describe()`). |
| `tools/call` | Runs the tool and returns its content blocks. |
| `resources/list`, `prompts/list` | Empty lists, for clients that ask anyway. |

Errors are split in two kinds:

- **Protocol errors** are JSON-RPC errors: an invalid request (`-32600`), an unknown method
  (`-32601`), an unknown tool or non-object params or arguments (`-32602`), a parse error
  (`-32700`), or an internal error (`-32603`). One bad request never stops the server.
- **Tool errors** are results with `isError: true` and a readable text. A tool raises
  `ToolError` for a problem the model can fix (an unknown key, a bad page range). Any other
  exception in a tool is logged to stderr and reported the same way, as "The tool failed: …".

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
use it), an input schema and `read_only`. `read_only` sets the `readOnlyHint` and
`idempotentHint` annotations; `destructiveHint` and `openWorldHint` are always false.

| Tool | Mixin | What it does | Writes |
|---|---|---|---|
| `search_library` | `library.py` | Searches items (metadata, PDF full text, what notes say) and notes, with the search box's filters. | no |
| `list_collections` | `library.py` | Folders with counts, tags, smart folders, notes folders and overall counts. | no |
| `get_note` | `notes.py` | A note's text and outline, the papers it cites (line and page), its links, backlinks and images, and the user's math macros. | no |
| `view_image` | `images.py` | Images stored with the notes: one by path, or all of a note's (at most 6 per call); an image in a gallery comes with its panel label and the gallery's caption. | no |
| `get_paper` | `papers.py` | Metadata, abstract, PDFs with page counts, the paper note, notes citing it, a summary of its reference list. | no |
| `search_in_paper` | `papers.py` | The pages of a PDF that contain some words, best first, with snippets. | no |
| `read_paper` | `papers.py` | The text of some PDF pages, marked by page (at most 10 pages or 80,000 characters). | no |
| `view_page` | `papers.py` | An image of one page with a 0–1 grid, and proposed regions for figures, tables and equations. | no |
| `snapshot` | `papers.py` | Cuts a figure, table, equation or rectangle into a PNG in `notes/attachments/` and returns the Markdown for it. | yes |
| `get_references` | `citations.py` | What a paper cites (library items marked) and/or the library items citing it. | no |
| `get_citation_graph` | `citations.py` | The neighbourhood of some papers (1 or 2 steps), or a library-wide overview of the most cited works. | no |
| `write_note` | `notes.py` | Saves a new note under `notes/ai/`. | yes |
| `edit_note` | `notes.py` | Changes an existing note: exact replacements, an append, or the whole text. | yes |
| `undo_note_edit` | `notes.py` | Puts a note back as it was before the last AI edit, if it was not changed since. | yes |
| `list_tasks` | `tasks.py` | The user's tasks, open ones by default, filtered with the Tasks page's search syntax (`backend/tasks/query.py`). | no |
| `get_task` | `tasks.py` | One task with its details, sub-tasks, parents and the notes that show it. | no |
| `create_task` | `tasks.py` | Creates a task (optionally a sub-task), and with `note` adds its `{{task:ID}}` line to that note through the note's history. | yes |
| `update_task` | `tasks.py` | Changes title, details, status (optionally with the sub-tasks), due date or parent. | yes |
| `delete_task` | `tasks.py` | Moves a task and its sub-tasks to the tasks trash. | yes |

`ToolsBase` in `backend/mcp/tools/common.py` has what the families share:

- `_paper(ref)` finds an item by citation key (also written `[@key]`), by id, or by key ignoring
  case. Trashed items are found too and marked `in_trash`; a library item wins over a trashed one
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

Claude's task changes are recorded with `by: "ai"` in `task_history`, so the app shows them as
Claude's and can undo them; `get_note` lists the tasks a note shows, and `NoteChecker` refuses a
note that shows a task id that does not exist (see [tasks](tasks.md)).

## The rules it enforces

The library itself is read-only to the MCP server: no tool changes items, PDFs, annotations or
reference lists, and none uses the network. `get_references` only reads `citations.db`; when a
list was never fetched, it tells the model to fetch it in the app (see [references](references.md)).

What it can write, and how:

- **New notes only under `notes/ai/`.** `write_note` builds the path from `AI_FOLDER` (`ai`), an
  optional sub-folder (each part slugified, `.` and `..` dropped) and the slugified title. It never
  overwrites: it picks a free name (`Title 2.md` …) and saves with no base hash, which fails if
  the file exists. The front matter gets an `id`, the title, `type: note`, `author: ai` and
  `created`.
- **Edits of existing notes, in any folder, through `edit_note`.** Each old passage must occur
  exactly once. The edit is hash-checked: the tool reads the note and its hash, and
  `NotesService.edit` refuses with `Conflict` if the file changed in between. On a conflict the
  tool reads the note again and reapplies the edit, up to three times, since the user may be
  saving that note at the same moment. A whole-text replacement keeps the note's front matter if
  the new text has none.
- **A history of every AI edit.** `NotesService.edit` records the version it replaces in
  `notes/.history/` (`backend/notes/history.py`): one JSON file per change with the text before,
  the hashes before and after, a summary and `by: ai`. Each change has its own uniquely named file,
  so the two processes never overwrite each other's entries. The app shows these changes and can
  undo them (`GET /api/notes/history`, `GET /api/notes/history/version`).
- **Undo only when it is safe.** `undo_note_edit` restores the text before the last AI edit only if
  the note's hash still equals that edit's `after_hash`. Otherwise undoing would also remove the
  user's later changes, and the tool refuses.
- **Images only in `notes/attachments/`.** `snapshot` saves through `NotesService.add_image`, with a
  timestamped name. The web app moves images that no note uses to `.trash-attachments/` and
  deletes them later (`backend/notes/attachments.py`), so a discarded snapshot does not stay.
- **Checked notes.** `NoteChecker` (`backend/mcp/notecheck.py`) runs before every save. Links and
  `#headings` must resolve, citation keys and pages must exist, a quote's words must be on the cited
  page, images must exist, and formulas must be LaTeX, not Unicode symbols. A link written with a
  note's title is rewritten to its file name when exactly one note has that title. Problems are
  errors and nothing is saved; things that work but look wrong (citing a trashed item) are
  warnings. For `edit_note`, only problems the change adds block it: the checker runs on the old
  and the new text, and errors already present are subtracted.

The limits are constants in `common.py`: `MAX_PAGES_PER_READ` (10), `MAX_CHARS_PER_READ` (80,000),
`MAX_NOTE_CHARS` (100,000, and an edit may grow a note to four times that), `MAX_PAPER_NOTE_CHARS`
(20,000) and `MAX_IMAGES_PER_VIEW` (6).

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

Claude Code on another computer cannot start `mcp_server.py` here, so the web app serves the same
tools at `/mcp` (the "Streamable HTTP" transport, in its simplest form). It needs `--lan` to be
reachable from the network; on this computer it works without.

```
Claude Code ──POST /mcp, Authorization: Bearer syn_…──► guard: host, Origin, token (access/guard.py)
                                                           └ api/mcp.py → Services.mcp_http()
                                                               └ HttpTransport.handle(body) → Server.handle
```

**Requests.** Each POST carries one JSON-RPC message or a batch, and the answer is the reply as
`application/json` (200), or an empty 202 when the body held only notifications. A body that is
not JSON answers 400 with a parse error. A `MCP-Protocol-Version` header naming a version not in
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
(`frontend/src/views/settings/aiAccess.ts`), only on the computer running Synapse, shown once with
the `claude mcp add --transport http … --header "Authorization: Bearer …"` command, and stored as
SHA-256 hashes. `last_used` and `last_addr` are updated at most once a minute. Removing a token
locks its app out at the next request.

| Endpoint | Who | What |
|---|---|---|
| `POST /mcp` | a valid token | One JSON-RPC message or batch. |
| `GET /api/mcp/tokens` | this computer or a signed-in device | `this_computer`, `lan`, `endpoints` (the `/mcp` URLs, one per address under `--lan`), `tokens`. |
| `POST /api/mcp/tokens` | this computer | `{name}` → 201 `{token, entry, tokens}`; the token is not kept. |
| `DELETE /api/mcp/tokens/<id>` | this computer | Removes the token. |

Over plain HTTP the token crosses the network in the clear, like a device cookie: use `--lan` on a
trusted network, or HTTPS (`--cert`/`--key`; Claude Code must then trust the certificate, for
example with `NODE_EXTRA_CA_CERTS`). See [sync and devices](sync-and-devices.md#security-model).

## Adding a tool

1. **Write the method** on the mixin of its family in `backend/mcp/tools/` (`library.py`,
   `notes.py`, `images.py`, `papers.py`, `citations.py`). A new family gets a new module with a
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
   the tool writes anything. The position in `build_server` is its position in `tools/list`.
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
- notes edited on disk being seen by the next tool call.

`tests/test_mcp_http.py` covers `/mcp`: the tools from another computer with a token, 401 without
one (also from this computer and with a device cookie), foreign origins and hosts, bad bodies and
versions, making, listing and removing tokens, and a note written over HTTP reaching the change
feed. The browser checklist `passkeys` makes a token in Settings and uses it.

Tests that render pages are skipped when `pypdfium2` is not installed. See [testing](testing.md).
