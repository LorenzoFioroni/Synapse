# Synapse

A local, web-based reference manager: a Flask backend with SQLite storage and a
TypeScript frontend. Everything runs on your machine.

## Run it

You need Python 3.10+ and [Node.js](https://nodejs.org) 20+ (Node only builds the web app's
scripts; the app itself runs offline).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
(cd frontend && npm ci)           # once, and again when frontend/package-lock.json changes
python server.py                  # http://127.0.0.1:8000 opens in your browser
python server.py --port 5050 --no-browser
python server.py --data-dir ~/MyLibrary
```

`server.py` compiles the web app's scripts (`frontend/src` → `frontend/js`) whenever they changed,
for example after a `git pull`.

The server only answers to `127.0.0.1` / `localhost`, and only the app's own pages and the
browser button can change the library: other websites open in your browser cannot.

### From an iPad or phone

```bash
python server.py --lan            # also reachable on your network (paired devices only)
```

1. On the computer, open **Settings → Devices → Add a Device**. It shows a QR code.
2. Scan it with the iPad's camera (or open the link it shows) and tap **Pair This Device**.

The iPad stays paired until you remove it in Settings → Devices on the computer (or sign it
out on the iPad itself; a paired device cannot remove others). Without pairing, another
device sees only a "pair this device" page — the library, PDFs and notes stay closed. The
computer running Synapse never needs pairing. Both devices must be on the same network, and the
computer must be awake (System Settings → Energy, or `caffeinate -s python server.py --lan`).
If the `.local` address in the QR code doesn't open, pick the IP address instead.

`--debug` only works without `--lan`: Flask's debugger must never be reachable from the network.

The connection is plain HTTP, so use it on a network you trust (your home Wi-Fi). Paired
devices are kept in `data/auth.db` (hashed), which backups leave out on purpose.

Your library lives in `data/` (or `--data-dir` / `SYNAPSE_DATA_DIR`):

| File | What |
| --- | --- |
| `library.db` | items, attachments, annotations |
| `fulltext.db` | extracted PDF text (FTS5); safe to delete, it is rebuilt |
| `pdfs/` | attached files |
| `notes/` | your Markdown notes (the files are the source of truth) |
| `notes_index.db` | links and search index of the notes; safe to delete, it is rebuilt |
| `citations.db` | reference lists fetched online (the citation graph); if deleted they can be fetched again |
| `backups/` | automatic backups of the library, settings and notes (`library-*.tar.gz`, see below), and note backups taken before a citation-key rename (`notes-*.tar.gz`); both go to the backup folder chosen in Settings instead, if there is one |
| `settings.json` | preferences from the Settings page |
| `auth.db` | devices paired with `--lan` (Settings → Devices); not in backups |

Back up the `data/` folder to keep everything. It is git-ignored: code and data
never mix.

## Features

- **Search** in three scopes, picked from the search box (the default is a setting):
  *Title, Abstract & Authors* · *All Fields* · *All Fields & Full Text*.
  Terms are ANDed, `"exact phrase"` and `-exclude` work, and accents are ignored
  (`banuls` finds *Bañuls*). Full-text hits show a highlighted snippet.
- **Full-text index** of attached PDFs, kept in its own database so the library
  stays small. New attachments are indexed in the background; progress is shown
  in the sidebar and Settings, which can also rebuild it.
- **Import / export**: BibTeX, RIS, EndNote (tagged `.enw` and XML) and CSL JSON.
  Imports are previewed first with duplicates flagged. Export the whole library,
  a selection, or copy BibTeX / citation keys from the context menu.
- **Citation keys** from a configurable pattern (Settings → Citation Keys), with a
  live preview and automatic `a, b, c…` suffixes:

  | Token | Example |
  | --- | --- |
  | `{auth}` `{Auth}` `{AUTH}` | cui · Cui · CUI |
  | `{authEtAl}` `{AuthEtAl}` | cuiEtAl · CuiEtAl |
  | `{authors2}` | cuiCirac |
  | `{year}` `{yy}` | 2015 · 15 |
  | `{title}` `{Title}` | variational · Variational |
  | `{shorttitle}` `{ShortTitle}` | VariationalMatrixProduct |
  | `{journal}` | prl |
  | `{doi}` | physrevlett114220601 |

- **Add items** by DOI, arXiv ID or ISBN (several at once), by dropping PDFs (the
  DOI/arXiv id on the first pages is used to fetch metadata), or manually.
- **Retrieve Metadata** works on one item or a whole selection: one review dialog
  with a section per article, word-level diffs with a checkbox per field, and the
  article's unchanged metadata below. Nothing is saved until you apply; replacing an
  existing citation key is opt-in. Several lookups run at once (arXiv requests are spaced out),
  and they can be stopped, or continue in the background with a progress notification and a
  *Review* button when done. Articles with updates are listed first, those not found next, and
  the ones already up to date are folded away at the end.
- **Adding items** (Settings → Adding Items): whether to retrieve metadata for imported items, find
  an available PDF, fetch reference lists, and turn imported keywords into tags — each *Never*,
  *Always* or *Ask* (a ticked checkbox where you add the items: the import dialog, Add by
  Identifier, the browser button). They all run in the background.
- **References / citation graph**: for new items (see Adding Items) and after Retrieve Metadata
  the list of papers the item cites is fetched too, in the background (Semantic Scholar, then
  OpenAlex, then Crossref; optional API keys in Settings → Online Services). The item pane shows *References* — cited works that are
  in your library link to them (matched by DOI, arXiv ID, or else title and year), the others
  can be added with one click — and *Cited by in your library*. Fetch them for any selection
  from the item menu or the multi-selection pane, with progress and a summary. The whole graph (library
  items + every work they cite, with "cites" edges) is at `GET /api/graph/citations`.
- **Graph** (in Notes, Obsidian-style; Cmd/Ctrl+Shift+G): next to the note you are editing, a
  local graph shows the notes it links to and from and the papers it cites (depth 2: what those
  connect to, e.g. other notes citing the same papers); click a node to open it. The full graph
  replaces the editor (link button in the notes list, the local graph's expand button, or **Show in
  Graph** in the item and note menus): notes and the papers they cite, with filters for citations
  between papers, cited works not in your library and orphans. It is centred on the note or paper
  you opened it from and shows only what is 1, 2 (the default) or 3 links away, or **All** of it;
  Shift-click a node, or **Centre graph here** on a paper's card, to move the centre. Clicking a
  paper shows a card (paper note, Show in Library, PDF, Add to Library). The data is at
  `GET /api/graph`.
- **Find Available PDF** (single or batch) looks for open-access copies (Unpaywall,
  arXiv, Scholar) and checks the file is really the right paper. Batch runs skip items
  that already have a PDF, show progress, and can select the ones that weren't found.
- **Folders** (nested; an item can be in several folders but is stored once — a folder
  only holds a link to it), **tags** (many per item, optional colours) and **smart
  folders** (saved searches). Drag items onto a folder to add them (hold Shift to move
  them out of the folder you're in), onto a tag to tag them. Items don't have to be in a folder: everything
  is in My Library (`folder:none` finds the ones in no folder). Inside a folder, Delete
  removes items from that folder only; Cmd/Ctrl+Delete moves them to the Trash.
- **Search filters** combine with text and can be negated with `-`:
  `tag:review`, `tag:"open systems"`, `folder:Physics`, `folder:"Physics/Transport"`,
  `folder:none`, `type:book`, `year:2020`, `year:2018-2022`, `year:>2015`, `has:pdf`,
  `author:cirac`, `title:…`, `journal:…`, `key:…`. Save any search as a smart folder.
- **Cmd/Ctrl+C** copies the BibTeX of the selected items (Cmd+Shift+C: citation keys,
  Cmd+T: edit tags).
- **Browser button** (`browser_extension/`, see its README): saves the article you are
  reading — DOI/arXiv pages, publisher pages, PDFs — with folder, tags and the PDF.
- **Trash** with Undo, **Duplicate Items** view with "keep this one, trash the others".
- **Backups** (Settings → Backups): the library database, settings and all notes with their images,
  daily or weekly while the app runs, and before emptying the Trash, deleting items for good,
  importing and regenerating citation keys. The PDFs of the items are not included (back up
  `data/pdfs` yourself). Keep the last 7–90; choose another folder (e.g. one synced by iCloud
  Drive or Dropbox). **Restore** backs up the current state first, then puts the library,
  settings and notes back and rebuilds the indexes.
- **Notes**: Markdown notes with live preview, KaTeX, code, Mermaid and images; `[@key]`
  citations and `[[links]]` between notes; a paper note per item. An item with several PDFs gets
  one button per PDF in citation cards and in its menus; `[@key, p. 4, att:12]` names the PDF with
  attachment id 12 (a citation without `att:` means the first PDF). Switch with the
  *Library | Notes* toggle or Cmd/Ctrl+Shift+L; the browser's Back/Forward (mouse buttons,
  Cmd+[ / Alt+←) go back to the note or library selection you came from.
  Images pasted into notes are kept in the notes' attachments folder; one that no note uses any more
  moves to an attachment trash by itself (and comes back if a note uses it again). **Attachments…**
  in the notes list menu shows them all — which notes use each, insert one into the open note,
  restore or delete trashed ones. Deleted notes and trashed attachments are deleted for good after
  a while (Settings → General → *Empty the notes trash*: never, 1 day … 6 months; default 1 month). The pencil column
  in the item list marks items with a paper note (blue) or cited in notes (grey).
  In live preview a formula, link or citation shows its source only while the cursor is in it, and
  clicking a symbol of a rendered formula puts the cursor on it; a `$$` block being edited shows
  the formula below its source. Tables are rendered in live preview too, with their math and
  citations. Source mode shows the Markdown as code, in a monospaced font. Switching between
  Live, Source and Read keeps your place in the note. While Notes is open, the toolbar's two panel buttons hide and show
  the notes list (also Cmd/Ctrl+\) and the note details; a hidden list slides in when the pointer
  reaches the window's left edge (or with a tap there). Settings → General → *Note width* lets
  the text use the whole pane. A `$$` equation gets a number when it has a `\label{name}`
  (`equation`/`align` environments are numbered as in LaTeX, `\tag{3}` sets one; Settings can
  number every one), and `\eqref{name}` refers to it; the number never overlaps the formula, and
  an equation too wide for the pane is shrunk a little (then scrolls). Formulas know the physics
  package's commands (`\vb`, `\dv`, `\pdv`, `\qty`, `\abs`, `\expval`, `\mel`, `\comm`, …), your own
  macros (Settings → General → *Math macros*), and a `\newcommand` written in any formula of the
  note (it applies to the whole note).
  In the notes list: Cmd/Ctrl-click and Shift-click (or Shift+arrows) select several,
  Delete/Backspace moves them to the notes trash (with Undo), F2 renames, Enter opens,
  drag to move; folders can be renamed, moved and deleted the same way.
- Sortable, resizable, re-orderable columns; light/dark theme; compact density;
  keyboard navigation (see Settings → Library & Shortcuts).
- Built-in PDF viewer with highlights, underlines, boxes (with a border, a fill or both: a filled
  box without a border highlights an area of the page), text boxes and drawings (pdf.js is bundled, so it works offline). Each annotation is saved on its own, for the PDF it was
  made on (an item's second PDF keeps its own), with the highlighted words; two tabs on one PDF never
  overwrite each other. Undo/redo with Cmd/Ctrl+Z and Cmd/Ctrl+Shift+Z.
  Highlights and underlines take whole words (hold Alt/⌥ while selecting for exact characters, or
  turn it off in Settings → PDF Viewer) and stay one straight line across inline math. Each tool
  remembers the colour you last used with it; the colours it starts with are set in Settings → PDF
  Viewer. Select an annotation to change its colours or delete it. Links in the PDF work: citations, figure and section
  references jump inside the document, web links open in a new tab, and hovering a link shows a
  preview of where it goes, centred on the target's column. Links to figures and tables show the whole
  figure or table with its caption, scaled to fit — the caption is recognised in the page text and
  the figure from what the page draws around it, wherever the PDF anchors the link. Scroll inside
  the preview to look around; zoom it with pinch, Ctrl/Cmd+scroll or its − / + buttons. Back/Forward (toolbar arrows, Alt+← / Alt+→, Cmd+[ / Cmd+], mouse
  buttons) return to where you were before following a link, even after zooming.
- **Quote / Snapshot to note** from the PDF viewer: select text (or drag a region with the camera
  button) and add it to an open note or the paper note, with its citation. Clicking that citation
  later — in read mode, with Cmd/Ctrl+click in the editor, or the PDF button of its card — opens the
  PDF right at the quoted words or the snapshot's region and flashes it for a moment (it is not
  saved as an annotation). Quotes are found by their words, so older quotes work too; a snapshot
  remembers its page region inside the PNG, so the note has no extra markup.

## AI access (MCP)

`mcp_server.py` lets Claude Desktop and Claude Code read your library through MCP (Model Context
Protocol): ask a question about a project note and Claude can read the note, the papers it cites,
the relevant pages of their PDFs and their references. It reads the same `data/` folder as the app
(which does not need to be running) and works only on this computer. When you ask it to save an
answer it adds a new note in `notes/ai/`; when you ask it to change a note, the edit is checked
against the version on disk and recorded, so the app can show it and undo it. Figures it cuts out of
a PDF go to `notes/attachments/`. It never changes the library's items.

Set it up once, from the repository root with the virtual environment active:

```bash
python mcp_server.py --config     # prints the exact entries for your paths
```

- **Claude Desktop:** Settings → Developer → Edit Config, merge the printed `mcpServers` entry into
  `claude_desktop_config.json`, and restart Claude. "synapse" then appears in the tools menu of a chat.
- **Claude Code:** run the printed `claude mcp add …` line.

Add `--data DIR` if your library is not in `./data`. Tools:

| Tool | What it gives |
| --- | --- |
| `search_library` | items and notes matching a query (same syntax as the search box: `folder:`, `tag:`, `year:`…), with snippets |
| `list_collections` | folders, tags, smart folders and notes folders with counts |
| `get_note` | a note's text, the papers it cites (with line and page), its links and backlinks, the images it shows |
| `view_image` | the images of a note (pasted pictures, snapshots — with the page and region they came from), or one by its path; large ones are scaled down when Pillow is installed |
| `get_paper` | metadata, abstract, PDFs and page counts, your paper note, notes citing it, reference-list summary |
| `search_in_paper` | the pages of a PDF containing some words, with snippets |
| `read_paper` | the text of some PDF pages (up to 10 per call), marked by page |
| `view_page` | an image of a page with a 0–1 grid; figures, tables and numbered equations with proposed regions |
| `snapshot` | cuts a figure, table or equation — named by its number (`figure="Figure 2"`, `equation="3"`, page found by itself) or as a region — into `notes/attachments/` like the viewer's camera button, and returns the Markdown for it |
| `get_references` | what a paper cites (library items marked, others with DOI/arXiv) and which library items cite it |
| `get_citation_graph` | the neighbourhood of some papers, or a library-wide overview of the most cited works you do not have |
| `write_note` | saves a new note in `notes/ai/` (`author: ai`), never overwriting anything — after checking it |
| `edit_note` | changes an existing note (replace passages, append, or new text) — checked the same way; the version before is kept |
| `undo_note_edit` | puts a note back as it was before Claude's last edit (if you did not change it since) |

Page numbers are PDF pages counted from 1, so Claude can cite `[@key, p. N]` and the citation opens
the right page in the app. Claude is told the notes syntax (KaTeX math, quotes as `> words [@key, p. N]`,
snapshots, callouts…), and `write_note` refuses a note — saving nothing — when a `[[link]]` or `#heading`
does not resolve, a citation key or page does not exist, a quote's words are not on the cited page, an
image is missing, or formulas are written in Unicode instead of `$…$`; Claude then corrects it. A link
written with a note's title is fixed to its file name. `edit_note` is checked the same way, but only
problems the change adds block it. Every change Claude makes to a note keeps the version it replaced
(`notes/.history/`, in backups): an open note takes the change as one step, so ⌘Z (Ctrl+Z) undoes it;
a bar over the note says what Claude changed, with Show changes and Undo; and the note's details list
all of Claude's edits, each with its diff and a way back. Equations are found by the number in
parentheses at the end of their line, and the snapshot covers all their lines (never half an
equation). `view_page` and `snapshot` need `pypdfium2`
(in `requirements.txt`). Note text and PDF passages Claude reads are sent to Anthropic as part of
the conversation, as if you had pasted them.

## Project layout

For how the code fits together and where new code goes, see
[`docs/architecture.md`](docs/architecture.md) (and [`CLAUDE.md`](CLAUDE.md) for the working rules).

```
./
├── server.py              starts the web app
├── mcp_server.py          MCP server for Claude Desktop / Claude Code (stdio)
├── backend/               Flask + SQLite backend (packages described in docs/architecture.md)
├── frontend/              the web app: index.html, viewer.html, src/ (TypeScript; built to js/), css/, vendor/ (third-party libraries)
├── browser_extension/     Chrome extension (Manifest V3) – load unpacked
├── tools/                 build scripts for the vendored libraries
├── tests/                 pytest suite, js/ unit tests, e2e/ browser checklists
├── docs/                  technical documentation
└── data/                  your library (git-ignored)
```

Notes are plain Markdown files in `data/notes/` (paper notes in `data/notes/papers/`,
images in `data/notes/attachments/`, deleted notes in `data/notes/.trash/`, images no note uses any
more in `data/notes/.trash-attachments/`). The files are
the source of truth; `data/notes_index.db` is derived from them and can be deleted at any
time — it is rebuilt on the next start.

The frontend never talks to external services directly; all lookups go through
the backend.

## Tests

```bash
pip install -r requirements-dev.txt
(cd frontend && npm ci && npm run check)   # type-check the frontend and the browser extension
ruff check .                    # lint
ruff format .                   # format
mypy                            # type-check the Python code
python -m pytest                # backend + JavaScript unit tests (the JS part needs Node.js)
pre-commit install              # optional: ruff, mypy and the type check on every commit
```

CI (`.github/workflows/ci.yml`) runs all of these on every pull request.

Browser checklists (need Playwright; each uses a throw-away data folder).
`python tests/e2e/run.py` runs them all and prints a summary, `python tests/e2e/run.py notes render`
only those, and `--retries 1` gives a failed one a second try. Or run one directly:

```bash
pip install playwright requests && playwright install chromium
python tests/e2e/notes_checklist.py      # editor, saving, conflicts, files
python tests/e2e/render_checklist.py     # live preview and read mode (tests/e2e/kitchen-sink.md)
python tests/e2e/links_checklist.py      # autocomplete, citation cards, side panels, item pane
python tests/e2e/library_checklist.py    # key rename, notes in search, quote/snapshot from the PDF viewer
python tests/e2e/citation_spot_checklist.py  # a quote's / snapshot's citation opens the PDF at that spot and flashes it
python tests/e2e/usability_checklist.py  # multi-select, Delete + Undo, folders, Back/Forward, item-list markers
python tests/e2e/viewer_links_checklist.py  # PDF links: jump, hover preview, Back/Forward in the viewer
python tests/e2e/graph_checklist.py      # Graph: local graph, full graph, levels, filters, paper card, Show in Graph, Back/Forward
python tests/e2e/attachments_checklist.py  # note attachments: unused ones to the trash, Attachments dialog, retention
python tests/e2e/adding_checklist.py     # bulk Retrieve Metadata (parallel, grouped, background), Settings → Adding Items
python tests/e2e/claude_edits_checklist.py  # Claude's note edits: appear live, ⌘Z / bar Undo / Show changes, edit list
python tests/e2e/equations_checklist.py  # equation numbers, \eqref, macros, clicks into formulas, panels and width
python tests/e2e/extension_checklist.py  # the browser button: options, reading an article page, saving it
```

The notes editor (CodeMirror 6) is vendored as a pre-built bundle in `frontend/vendor/cm6/`;
to rebuild it from the pinned versions: `cd tools/cm6 && npm ci && ./build.sh`. The renderer
(markdown-it, KaTeX, Mermaid) likewise lives in `frontend/vendor/md/` and `frontend/vendor/mermaid/`
(`tools/md`), and the graph library (force-graph) in `frontend/vendor/force-graph/` (`tools/graph`).
