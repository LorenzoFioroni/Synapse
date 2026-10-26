# Notes

Synapse keeps notes as plain Markdown files in `data/notes/`. The files are the source of truth.
Everything derived from them (titles, links, citations, tags, the full-text index) lives in
`data/notes_index.db` and can be deleted at any time; it is rebuilt from the files.

This page covers the notes format, the backend package `backend/notes/`, its HTTP API and the
notes workspace in `frontend/src/views/notes/`. For where notes sit in the whole app, see
[architecture.md](architecture.md). Related pages: [storage.md](storage.md) (the databases and
backups), [mcp.md](mcp.md) (Claude's note tools), [sync-and-devices.md](sync-and-devices.md) (the
change feed), [frontend.md](frontend.md) and [testing.md](testing.md).

## The notes folder

| Path in `data/notes/` | What it holds |
|---|---|
| `*.md`, `<folder>/*.md` | The notes. Folders are ordinary folders. |
| `papers/<key>.md` | Paper notes: the default place for the note about one library item. |
| `ai/` | Notes Claude created through the MCP server's `write_note` (`AI_FOLDER` in `backend/mcp/tools/common.py`). |
| `attachments/` | Images shown by notes. Reserved: no notes or user folders go here. |
| `.trash/<timestamp>/` | Deleted notes and folders, at their old relative path. |
| `.trash-attachments/<timestamp>/` | Images no note uses any more. `.since` records when automatic emptying started. |
| `.history/<id>.json` | The version each of Claude's edits replaced (see [History of AI edits](#history-of-ai-edits-backendnoteshistorypy)). |
| `.folders.json` | Colours given to folders: `{"folder/path": "#rrggbb"}` (see [Folder colours](#folder-colours)). |

Hidden folders (any name starting with `.`) are never indexed, and a path from outside can never
point into them: `Vault.clean_rel` rejects every path segment that starts with a dot.

### Front matter

A note may start with a `---` header. `backend/notes/frontmatter.py` reads only a small subset of
YAML: `key: value` scalars (optionally quoted), inline lists `[a, b]` and block lists. Anything
richer is kept as a raw string. `set_fields` rewrites only the keys it is given and leaves every
other line byte for byte as it was, so a user's own header survives the app's edits. An
unterminated header is treated as body, by the backend and the frontend alike.

| Field | Meaning |
|---|---|
| `id` | A ULID (`ulid()` in `backend/notes/service.py`), set when the app or Claude creates the note. |
| `title` | The title. Without it the index uses the first `# heading`, then the file name. |
| `type` | `paper` or `note`. When missing, the index says `paper` if `paper` is set, else `note`. |
| `paper` | The citation key of the item a paper note is about. |
| `author` | `human` (default) or `ai` for notes written by an AI app through `write_note`. |
| `agent` | Which AI app wrote it (`Claude`, `Codex` …), beside `author: ai`. |
| `created` | ISO time with offset (`now_iso()`). `updated` is indexed when present but never written. |
| `tags` | A list or a comma-separated string. Joined with the inline `#tags`. |

A **paper note** is any note whose `paper` field names a key. `NotesService.create(item_id=…)`
makes one at `papers/<slugify(key)>.md` with `type: paper`, a `# title` and a `[@key]` line, and
refuses if the key already has a paper note. Other notes can still cite the item; they count as
"citing" notes in `key_summary`, which drives the note markers in the item list.

### Images and the attachment trash

`NotesService.add_image` accepts PNG, JPEG, GIF and WebP up to 25 MB, checked by their magic
bytes. SVG is refused because it can carry scripts. Files are named
`attachments/<YYYYmmdd-HHMMSS>-<name>.<ext>` and `Vault.write_bytes` never overwrites (it adds
`-2`, `-3`, …). That is why `GET /api/notes/attachments/<name>` can be cached as immutable.
In a note, `attachments/x.png`, `./attachments/x.png` and `x.png` all mean the same file
(`attachment_name` in `backend/notes/attachments.py`, `imageSrc` in `render.ts`).

`backend/notes/attachments.py` (`Attachments`) decides what is in use: an image is used while
any note shows it, counting notes in `.trash` (restoring a note brings its images back).
`sweep()` moves unused images to `.trash-attachments/`, but never within `GRACE_SECONDS` (an
hour) of being added, since the note that shows it may not be saved yet. A trashed image that a
note uses again comes back by itself. Notes changes call `NotesService.on_change`, which
`Services` points at `Attachments.poke` (a sweep five seconds later); `start()` also runs
`maintain()` every hour.

### Notes trash and retention

Deleting never removes a file: `Vault.trash` and `Vault.trash_batch` move notes and folders into
`.trash/<timestamp>/`, and one batch shares one trash id so one Undo restores all of it.
`Attachments.purge` deletes entries of both trashes older than the `notes_trash_retention`
setting (`never`, `1d`, `1w`, `2w`, `1m`, `6m`; default `1m`). Age counts from the deletion but
never from before `.since`, so updating the app does not empty a trash that filled up over years.

## Backend

`Services` (`backend/services.py`) creates one `Vault`, one `NotesIndex`, one `NotesService` and
one `Attachments`, runs `NotesIndex.sync()` at startup and connects key changes to rekeying. The
MCP server builds the same objects in its own process over the same folder.

### The vault (`backend/notes/vault.py`)

`Vault` is the only code that touches note files.

- **Paths.** Every outside path is relative, like `papers/smith2021.md`. `clean_rel` normalises
  it (NFC, `/` separators) and raises `InvalidPath` for absolute paths, `..`, dot segments, the
  characters in `_BAD_CHARS`, a missing `.md` and anything that resolves outside the root.
- **Hash-checked writes.** `write(rel, text, expected_hash=…)` compares the SHA-256 of the file
  on disk (`content_hash`) with the hash the caller loaded and raises `Conflict` (with the
  current hash) when they differ. `expected_hash=None` means "create": it raises `NoteExists`
  if the file is there. Writes are atomic (temp file, `fsync`, `os.replace`). The check runs
  under a per-process lock; the app and the MCP server are separate processes, which is why
  both sides also re-read and retry on `Conflict`.
- **Moves and folders.** `move` (optionally hash-checked) allows case-only renames on
  case-insensitive disks. `make_folder`, `move_folder` and `notes_in` refuse the `attachments`
  folder (`_reserved`, compared without case for macOS), and so do `write` and the destination
  of `move`: the index never lists notes there, so a note written there would disappear at the
  next sync.
- **Trash.** `trash`, `trash_batch`, `trashed`, `restore` and `restore_batch`. Restoring skips
  files whose place is taken again and leaves them in the trash.

Errors are `VaultError` subclasses with an HTTP `status`: `InvalidPath` (400), `NoteMissing`
(404), `NoteExists` and `Conflict` (409).

### The index (`backend/notes/index.py`)

`NotesIndex` keeps `data/notes_index.db`. It is derived data: when `SCHEMA_VERSION` changes the
old tables are dropped and rebuilt instead of migrated. The version is also bumped when the parser
changes what it finds, so notes indexed by the old rules are read again.

| Table | Rows |
|---|---|
| `notes` | One per note: path, lower-case `stem`, the front-matter fields, `hash`, `mtime`, `size`. |
| `links` | Every link, with `kind` (`note`, `embed`, `cite`, `image`, `task`), `target`, `target_norm`, heading, alias, locator, page, prefix, `suppress_author`, `grp`, line and context. |
| `tags` | Inline and front-matter tags. |
| `headings` | ATX headings with level and line. |
| `notes_fts` | FTS5 table of title and body (`unicode61 remove_diacritics 2`). |

`sync()` walks the vault and skips every file whose `(mtime, size)` matches the index. `update()`
then compares the content hash and re-parses only when it changed. It takes the file's `stat()`
before reading it, so a file saved in between keeps an older `mtime` in the index and is read
again by the next sync. Files that disappeared are
removed. `rebuild()` empties the tables and syncs. The app syncs at startup and on
`POST /api/notes/rescan`; `NotesService.get` also re-indexes a note whose hash differs. A file
changed by another program therefore shows in the listing only after a rescan or a restart.

What it answers:

- `resolve(target)`: the paths a `[[target]]` may mean, case-insensitive, `.md` optional. With a
  `/` it matches the path or a path ending in it; otherwise the stem. The best match has the
  fewest folders, then the shortest path, then comes first alphabetically.
- `outgoing(path)` (with `resolved` and `ambiguous`), `backlinks(path)` (only links that really
  resolve here), `citing(key)`, `paper_notes(key)`, `cited_keys`, `key_summary`.
- `link_graph()` for the Graph view, `image_links()` for the attachment sweep, and `task_refs()`
  (every `{{task:id}}` with its note, line and the heading above it) for the Tasks page.
- `search(q)`: FTS with every word as a prefix, snippets HTML-escaped with `<mark>`.
- `texts_by_key()`: what notes say about each key, which `NoteTextMatcher` in `service.py` feeds
  to the library search (see [fulltext-search.md](fulltext-search.md)).

### The parser (`backend/notes/parser.py`)

The parser is the only code that decides what a note links to or cites. `parse()` returns a
`ParsedNote` with `meta`, `title`, `links`, `headings`, `tags` and `body`.

| Syntax | Result |
|---|---|
| `[[Note]]`, `[[folder/Note]]`, `[[Note#Heading]]`, `[[Note\|alias]]` | `note` link. An escaped bar (as in tables) also starts the alias. `[[#Heading]]` links into the same note. |
| `![[Note]]` | `embed` |
| `[@key]`, `[see @a, p. 4; -@b]` | `cite`, one per key, sharing a `group` |
| `![alt](path)` | `image` (alias is the alt text before any `\|`) |
| `{{task:k7f3q2mx}}` | `task`: the note shows that task (a record in `library.db`, see [tasks](tasks.md)) |
| ` ```tasks ` … ` ``` ` | each valid ID line is a `task` link; the whole block is otherwise masked (see [task blocks](tasks.md#task-blocks-in-notes)) |
| ` ```gallery ` … ` ``` ` | not code: its images and the citations and links in its captions count (see [Galleries](#galleries)) |
| `# Heading`, `## Method \label{sec:method}` | heading (ATX only, at most three spaces of indent); a trailing section label is omitted from its indexed title |
| `#tag`, `#nested/tag` | tag (starts with a letter; not after a word character, `/`, `&`, `#`, brackets or parentheses) |

Citations follow Pandoc. The brackets must contain `@` and must not be a Markdown link or
reference (`_BRACKET`). A colon right after the brackets makes them a link reference definition
only at the start of a line (at most three spaces in, `_reference_definition`); elsewhere, as in
"the convention of [@key]:", it is punctuation and the citation counts. Each `;`-separated part has an optional prefix, an optional `-` that
suppresses the author, the key and an optional locator after a comma. If one part is not a
citation, the whole bracket is not one. Keys use `[A-Za-z0-9_][A-Za-z0-9_:.+\-/]*`, and a
trailing `.`, `:`, `/` or `-` is punctuation. A bare `@key` is not a citation. The locator gives
`page` when it starts with `p.`, `pp.`, `page`, `pages` or `pg`. `att:<id>` in the locator names
the PDF attachment the citation means (`attachment_of`); without it the item's first PDF is meant.

Nothing inside fenced code, inline code, math, HTML comments or the front matter counts, and a
backslash escapes the next character. A fenced block whose info string starts with the word
`gallery` is the exception: only its fence lines are blanked, and its lines are read like a
paragraph (no code, `$$` blocks or other fences inside it). `mask(body, galleries)` also reports
where each gallery block is, which `backend/notes/gallery.py` uses. `mask()` builds a copy of the body in which those
characters are blanked, keeping length and line breaks, and the regexes run on that copy. That
keeps offsets valid for line numbers, context and rewriting. Inline constructs are masked one
paragraph at a time, so a stray backtick or `$` cannot swallow the rest of the note. Inline
`$…$` follows Pandoc (no space after the opening `$`, none before the closing one, no digit
after it) and must close on the same line, so "$5 and $10" stays text. Indented code blocks are
not supported, here or in the renderer.

`link_spans()` and `replace_spans()` give the exact positions of citation keys and link targets
in the stored text, so a rewrite changes those characters and nothing else.

### Keeping the frontend port in step

`frontend/src/views/notes/mdscan.ts` is a line-by-line port of `parser.py` (`mask`, `scan`,
`splitFrontmatter`, and the pieces `parseCites`, `splitWiki` and `attachmentOf`). The frontend
uses it only for display: the live preview needs to know which characters are math, links or
citations while you type, and the renderer (`render.ts`) takes its citation and link parsing from
it, so the editor, read mode and the backend agree. It also reports the masked
`spans` and image widths (`![alt|300](…)`). The backend stays the authority on the link graph.

The two must agree exactly. `tests/notes/test_scanner_parity.py` runs both on every fixture in
`tests/notes/fixtures/` and on 1,500 random documents built from tricky pieces, and requires the
same masked text, links, headings and tags. The JavaScript side runs through
`tests/js/scan_dump.mjs` on the built `frontend/js/`, so the test needs Node. Change the
parser and the port in the same commit, and add a fixture (with its `.json` expectation for
`tests/notes/test_parser.py`) for the case you changed.

### The service (`backend/notes/service.py`)

`NotesService` is what the API and the MCP tools call. Every change goes to the file first and
the index is then updated from the file.

- `create` writes a new note with front matter (`id`, `title`, `type`, `author: human`,
  `created`) and picks a free name (`Untitled 2.md`, …). With `item_id` it makes a paper note.
- `save(path, text, base_hash)` is the hash-checked save behind the editor. `base_hash=None`
  creates. A `Conflict` reaches the browser as a 409.
- `edit(...)` is the save used for AI apps' changes: same check, plus a history entry naming the app (`current_agent()`).
- `move` and `move_folder` do **not** rewrite links. They collect the backlinks first and return
  the ones that no longer resolve as `broken_links`. Links by name survive a folder move; a
  rename breaks them, and the UI says which.
- `links(path)` adds library info to each citation and lists `cited`, `unresolved` and
  `backlinks`. `cite_index()` lists every item with a key, trashed ones flagged, with its PDFs
  and paper note. `item_notes(id)` gives an item's paper notes, citing notes and suggested path.
- `append_to_paper_note` adds a quote or snapshot from the PDF viewer to the item's paper note,
  creating it if needed, with up to three hash-checked attempts.

### Rekeying (`backend/notes/rekey.py`)

When an item's citation key changes, `PaperRepository.on_citekey_change` calls
`NotesService.rename_key`, which runs `Rekeyer.apply`:

1. `plan()` finds the affected notes (citing the key, paper notes of it, notes linking to a paper
   note that will be renamed) and computes each change. It writes nothing. When a key is changed
   by hand, the item pane shows this preview first (`GET /api/notes/rename_plan`, `rekeyDialog.ts`).
2. `backup()` writes `notes-<timestamp>.tar.gz` of every note into the backup folder
   (`backup_dir` from Settings, else `data/backups/`; `data/backups/` too when that folder
   cannot be written, and without any backup nothing is rewritten), at most once a minute
   (`BACKUP_EVERY_S`) so "regenerate all keys" makes one backup, keeping `BACKUPS_KEPT` (20).
3. Each note is rewritten with `link_spans`/`replace_spans` (`[@old]` becomes `[@new]`, never
   inside code or math) and `paper: old` in the front matter, hash-checked, up to three tries.
4. A paper note still named `papers/<old>.md` moves to `papers/<new>.md` if that name is free,
   and `[[links]]` to it are updated. History entries follow the move.

### History of AI edits (`backend/notes/history.py`)

`NoteHistory` stores one JSON file per change in `.history/`: `id`, `path`, `at`, `by` (`ai`),
`agent` (the AI app that made it: Claude, Codex …; missing in entries recorded before it was
kept, which the app shows as "An AI app"), `summary`, `before` (the whole previous text),
`before_hash`, `after_hash`. One file per change, with a unique name, means the app and the MCP
server cannot overwrite each other's entries. `after_hash` tells whether the note is still exactly
what the AI app left, which decides whether the
app offers a plain Undo. Moves rewrite the stored paths (`moved`). `prune` keeps at most
`KEEP` (1000) entries and none older than a year.

## HTTP API (`backend/api/notes.py`)

Notes are addressed by their vault path. Every successful `POST`/`PUT` records a `notes` change
in the change feed. `guarded` turns `Conflict` into 409 with `conflict` and `current_hash`,
`NoteExists` into 409 with `exists` and `path`, other `VaultError`s into their status and
`ValueError` into 400.

| Method | Path | What it does |
|---|---|---|
| GET | `/api/notes` | Listing: every note's index entry, the folders, `folder_colors` (only folders that exist), counts. |
| GET | `/api/notes/note?path=` | Text, hash and index entry of one note. |
| POST | `/api/notes/note` | Create (`title`, `folder` or `path`, `item_id`, `text`). |
| PUT | `/api/notes/note` | Save `text` over `hash`; with `create: true`, write a note that must not exist. |
| POST | `/api/notes/move` | Rename or move a note (`path`, `to`, optional `hash`); returns `broken_links`. |
| POST | `/api/notes/folder` | Create a folder. |
| POST | `/api/notes/folder/move` | Rename or move a folder; returns the moved notes and `broken_links`. Its colour and its subfolders' go with it. |
| PUT | `/api/notes/folder/color` | `{path, color}`: give a folder a colour (`#rrggbb`), or none (`null` or `""`); answers `folder_colors`. |
| POST | `/api/notes/trash` | Trash one note (`path`) or several notes and folders together (`paths`, `folders`). |
| GET | `/api/notes/trash` | Trashed notes with `trashed_at` and `deletes_at`, and the retention. |
| POST | `/api/notes/restore` | Restore one note (`trash_id`, `path`) or a whole batch (`trash_id` only). |
| GET | `/api/notes/history?path=` | AI apps' changes to a note, newest first, without their text. |
| GET | `/api/notes/history/version?id=` | One change with the text from before it. |
| GET | `/api/notes/search?q=` | Full-text search of notes. |
| GET | `/api/notes/links?path=` | Outgoing links with library info, cited keys, unresolved links, backlinks. |
| GET | `/api/notes/rename_plan?old=&new=` | What a key change would change. Writes nothing. |
| GET | `/api/notes/keys` | Per key: its paper note and how many notes cite it. |
| GET | `/api/notes/cite_index` | Every item with a key, for autocomplete and citation cards. |
| GET | `/api/items/<id>/notes` | An item's paper notes and citing notes. |
| POST | `/api/notes/append` | Append Markdown to an item's paper note (used by the PDF viewer). |
| POST | `/api/notes/attachments` | Upload an image (multipart `file`); returns its path and Markdown. |
| GET | `/api/notes/attachments` | Every image with the notes using it, plus the attachment trash. |
| POST | `/api/notes/attachments/restore` | Restore a trashed image. |
| POST | `/api/notes/attachments/delete` | Delete a trashed image for good. |
| POST | `/api/notes/attachments/clean` | Sweep and purge now (also runs hourly). |
| GET | `/api/notes/attachments/trash/<id>/<name>` | Serve a trashed image. |
| GET | `/api/notes/attachments/<name>` | Serve an image. |
| POST | `/api/notes/rescan` | Sync the index with the files. |

## Folder colours

A folder can be given a colour (right-click it in the notes list → Colour; the palette of tags,
`ui/colors.ts`; default none). A note takes the colour of the nearest coloured folder above it
(`folderColor` in `workspace.ts`), and shows it on its tab and in the window's title bar
([the app window](desktop.md#folder-colours)); a folder icon shows its own colour, or lighter the
one it has from a folder above.

`backend/notes/folder_colors.py` (`FolderColors`, `NotesService.folder_colors`) keeps them in
`.folders.json` at the top of the notes folder: hidden like `.history`, so it is never indexed and
goes into backups with the notes. Renaming or moving a folder in the app moves its colour and
those of the folders inside (`moved`); a folder renamed outside the app loses its colour; a
deleted folder keeps it in the file, so it comes back if the folder is restored, but the listing
only shows colours of folders that exist. Colours are checked (`#rrggbb`).

## Frontend (`frontend/src/views/notes/`)

| Module | Role |
|---|---|
| `notesView.ts` | Builds the workspace, opens and closes notes, wires `NoteSession` to the UI. Notes open in tabs ([the app's tabs](desktop.md#the-apps-tabs)): `openNote` and `openNotes` go through `noteTabs.open`, and the tab shown has `showNote` show its note here (opens are serialised). Each note tab has a view (`createView`, `showView`, `hideView`: its session, editor, link data and mode); hidden views are kept out of the page and come back as they were, the 8 used last (older ones are put away in `ws.parked`). |
| `workspace.ts` | Shared state `ws` (the note views and the one shown, its editor and session, listing, link data, cite index, parked editor states), `hooks` back into `notesView.ts`, and `noteTabs` with `notesRenamed`/`notesDeleted`, through which the notes code reaches the tabs without importing them. |
| `noteSession.ts` | Saving one note: autosave, drafts, retries, conflicts. No DOM. |
| `tree.ts`, `treeInput.ts` | The notes list and filter; mouse, keyboard, context menu, drag and drop. A click opens a note in the preview tab, a double-click keeps its tab (F2 renames). |
| `fileOps.ts` | New, rename, move, trash with Undo, the trash dialog, rescan. |
| `editor.ts`, `livePreview.ts`, `previewWidgets.ts` | CodeMirror 6 (`vendor/cm6/`), the live preview decorations (built on `mdscan.ts`) and the widgets they draw. |
| `completion.ts`, `library.ts` | `[@`, `[[` and `{{task` autocomplete and their ranking (tasks come from the Tasks module through `taskHooks`). |
| `ghost.ts`, `ghostLogic.ts`, `hooks.ts` | Autocompletion: suggestions as ghost text, from the source the Autocompletion core plugin sets in `hooks.ts` (see [autocompletion](autocompletion.md)). |
| `render.ts` | Markdown to HTML with markdown-it and KaTeX (`vendor/md/`): math, links, citations, callouts, tasks. Raw HTML is never rendered. No DOM. |
| `richRender.ts` | Mermaid (loaded on demand from `vendor/mermaid/`), code highlighting, filling embedded notes into the page. |
| `embed.ts`, `linkData.ts` | What an embedded note shows; what a note links to (`/api/notes/links` as maps for the renderer). No DOM. |
| `equations.ts`, `macros.ts` | Equation numbering, `\label`/`\eqref`, and math macros (physics package, Settings, `\newcommand`). |
| `mathClick.ts`, `mathFit.ts` | Clicking into rendered math; shrinking wide equations. |
| `readView.ts` | Read mode, following `[[links]]` (offering to create missing notes), going to a line. |
| `sidePanel.ts`, `panels.ts` | Status, breadcrumbs and details panel; showing and hiding the side panels. |
| `citeCard.ts`, `citeTarget.ts` | Citation hover cards; where in the PDF a citation points (quoted words or a snapshot's region). |
| `aiBar.ts`, `aiEdits.ts`, `diff.ts` | AI apps' edits: the bar ("Codex edited this note"), the history dialog with each app's name, line diffs. |
| `conflict.ts` | The dialog for a note changed or deleted elsewhere. |
| `attachmentsDialog.ts`, `rekeyDialog.ts` | The attachments dialog; the preview before a key change. |
| `exportNote.ts`, `portable.ts` | Exporting a note as PDF, HTML or Markdown; the Markdown conversion and reference entries (no DOM). |
| `crossref.ts`, `sections.ts` | References (`\ref`, `\eqref`, `\cref`, `\Cref`) written out as cleveref does, from equations, figures and labelled Markdown headings; hierarchical section numbering (no DOM). |
| `hooks.ts` | What core plugins add to the editor: ghost text, the graph, galleries and figures (`setNoteHook`). |
| `commands.ts` | The command palette's commands for the open note: inserting images and figures (`Insert Image…`, `Insert Figure with Caption…`), citations, links and tasks (they type `[@]`, `[[]]` or `{{task }}` and open the completion), tables, callouts, equations and diagrams; Live/Source/Read; renaming, moving, deleting, exporting and linking the note; its graph, AI edits, paper and PDF. Each is offered only while a note is on screen and it can act (a figure needs Extended Markdown's figures, a task the Tasks plugin); inserting from Read goes back to editing. |

Galleries and figures are an extension of the **Extended Markdown** core plugin, in
`frontend/src/plugins/core/extended-markdown/figures/`:

| Module | What it does |
|---|---|
| `gallery.ts`, `galleryView.ts` | Reading a ` ```gallery ` block and its figure caption (no DOM); turning the carousel. |
| `figures.ts` | Which images and galleries are figures, their numbers and `\label`s (no DOM). |
| `markdown.ts` | The renderer's rules: `galleryRule`, `figureRule` and their HTML. |
| `portable.ts`, `exportStatic.ts` | Figures in the Markdown export (`portableFigures`); carousels as static figures in HTML and PDF (`staticGalleries`). |
| `hook.ts`, `index.ts` | The `FiguresHook` the notes code asks (`views/notes/hooks.ts`), including `gallery(images, {caption, panels})` (`newGallery` in `gallery.ts`), the block several images become; turning the extension on and off. |

### The editor and read mode

A note shows in one of three modes: **Live** (Markdown rendered except where the cursor is),
**Source** (the plain Markdown, in a monospaced font with one text size, so it reads as code) and
**Read** (`readView.ts`). Switching keeps the line at the top of the view at the top:
`NoteEditor.topLine` and `readTopLine` give it as a line number with a fraction (read mode uses
the `data-line` of its blocks), `scrollToLine` and `scrollReadTo` go back to it. A note that was
just opened starts at its top.

The work done per keystroke is kept small, so long notes stay quick to edit:

- **Live preview** (`livePreview.ts`). The block-level decorations (front matter, `$$` blocks,
  tables, Mermaid diagrams) come from a state field, because CodeMirror needs them for the whole
  note; it walks only the block structure of the syntax tree. Everything inline (formulas on one
  line, links, citations, emphasis, headings, lists, quotes, tasks) comes from a view plugin that
  works only on the part of the note on screen. `mdscan.scan` runs once per version of the text.
  Widgets (`previewWidgets.ts`) keep their DOM while their key stays the same, so a formula,
  table or diagram is drawn again only when its own source changed. A table is drawn by the
  renderer, then its equation, figure and section references are resolved again with the whole
  note's label lookup, so math, citations, links and cross-references in its cells match Read mode. While a
  `$$` block is being edited, its whole source is protected from CodeMirror's Markdown escape and
  emphasis decorations: TeX such as `\,` and `_` therefore stays literal, as it does in Read mode.
- **Read mode.** `renderRead` keeps every top-level block whose HTML did not change, and changes
  the DOM in place: ticking a task redraws only its list, and formulas, diagrams and embedded
  notes elsewhere are not drawn or loaded again. Opening read mode, the theme and the math
  settings redraw everything (`fresh`), so embedded notes are reloaded then.
- **KaTeX** output is remembered by formula and set of macros (`katexMemo` in `render.ts`),
  shared by every renderer.

Task boxes are drawn the same way in both modes (a rounded square, ticked and filled when done),
and a done task is greyed and struck through. A `{{task:id}}` line is different: it shows a task record (see
[tasks](tasks.md)). The live preview always draws it as the task's checkbox, title and labels
(`TaskWidget`, through `taskHooks.preview`) and never as source: it is an atomic range, so the
cursor steps over it and Backspace deletes it whole. Read mode draws it with
`renderDefaults.taskHtml`. A list item whose only content is a task line shows no bullet.

### Galleries

A gallery shows several images as one carousel, and exports as one figure with panels.
Galleries and figure numbers come from the **Extended Markdown** core plugin (on unless its
"Galleries and numbered figures" switch, or the whole plugin, is turned off in Settings → Plugins).
Turned off, a gallery reads as the Markdown it holds (its images and
captions, one after the other), an image's caption line as text, and `\ref` to a figure as ??;
the note itself does not change, and the parser still counts a gallery's images and citations.

````markdown
```gallery|600
![](attachments/clean.png)
*Clean system.*
![](attachments/speckle.png) With speckle, $\xi=1\,\mu$m [@key, p. 3].

Photon number versus $N$ at equal reduced drive.
```
````

- **The block.** A fenced block (backticks or tildes) whose info string starts with `gallery`.
  A width in pixels may follow, as for images: `gallery|600`, `gallery 600`, `gallery 600px` or
  `gallery width=600`. Without one the gallery takes the width of the note.
- **Panels.** Each line that starts with an image starts a panel; several images at the start of
  one line are several panels. The rest of the image's line and the lines right below it are the
  panel's caption, up to a blank line or the next image. An image's own `|300` width still applies.
- **The gallery's caption.** Lines that belong to no panel: after a blank line, or before the
  first image.
- **Several images at once.** Pasting or dropping several images into the editor (one paste or
  drop), or choosing several with **Insert Image…** in the command palette, inserts one gallery
  (`imagesMarkdown` in `notesView.ts`, through the editor's `combineImages`, asks the hook's
  `gallery`): the images only, so it is not numbered until it gets a caption. **Insert Figure with
  Caption…** with several images adds a caption to each panel ("Panel a.") and one for the figure
  with a `\label`, selected to type over. Without the figures extension they are inserted one
  image per line.
- **Captions** are Markdown on a line, so math, citations, `[[links]]`, `\eqref` and emphasis work.
  A caption wrapped as a whole in `*…*` or `_…_` (the way an image is usually captioned) is
  unwrapped, so an image and its caption can be moved into a gallery as they are.

**Rendering** (`markdown.ts` in the plugin, added to each renderer through the `figures` hook;
`render.ts` rebuilds a renderer when a hook changed). A core rule (`galleryRule`, after block
parsing) replaces the fence
token by tokens of its own (`gallery_open`, `gallery_panel_open`, …), each caption an `inline`
token. Captions are therefore parsed with the rest of the note: equation numbering, the note's
macros and `\eqref` work in them. The HTML is a `figure.gallery` with a `.gallery-track` of
`.gallery-panel`s (each image above its caption, labelled "(a)", "(b)", … when there are several;
the label hangs to the left of `.gallery-caption-text`, so the caption's text is centred),
arrows, dots and a `figcaption.gallery-caption`. A gallery of one image has no labels or controls;
an empty one shows a hint. Without the plugin, `render.ts`'s own rule (`gallery_plain`) parses
the block's content as Markdown in its place, keeping the source lines for read mode's clicks.

**The carousel** (`galleryView.ts`, CSS in `notes.css`). The panels sit side by side in a track
that scrolls horizontally with snapping, one panel wide, so a swipe or a trackpad turns it
without code. Listeners on the document (installed by the plugin while it runs) serve every gallery,
however often it is redrawn: the arrows and dots scroll the track, ←/→ do while a dot or arrow
has focus, and a scroll from anywhere updates the dots and disables the arrow at either end.
A press on the controls is stopped before the editor sees it, so turning a gallery in the live
preview does not open its source.

**Live preview.** With the plugin on, the block is one unit (`livePreview.ts`): drawn as the carousel (`W.gallery`,
rendered by the renderer like a table) until the cursor enters it; then its source shows, as
text rather than code, with the carousel below it updating as you type. `\eqref` in its captions
is numbered from the whole note's equations. Citation and link autocomplete work in it
(`completion.ts` treats a gallery's lines as text). Without the plugin its lines show as any text.

**Exports.** HTML and PDF (`staticGalleries` in the plugin's `exportStatic.ts`, called by `exportNote.ts`; CSS in `export.css`): each
carousel becomes a `figure.gallery-figure` whose panels sit in a grid of `figureColumns(n)`
columns (as many as there are panels up to three, two for four, else three), each with its letter
in a white, black-bordered circle on the image's upper left corner (`.gallery-cell-label` inside
`.gallery-cell-image`, a frame that hugs the image), and one caption: the gallery's own, then each panel's as "(a) First. (b) Second.",
every part ending as a sentence (`endsSentence`). Markdown (`portableFigures` in the plugin's `portable.ts`, called by `views/notes/portable.ts`):
the images on one line, which most viewers set side by side, and that caption in italics below.

**Backend.** The parser counts the images and citations in a gallery, so the attachment sweep
keeps its images and the citation graph and backlinks include its captions.
`backend/notes/gallery.py` reads the blocks the same way as `gallery.ts` for the MCP tools:
`get_note` and `view_image` give an image in a gallery its panel's caption and
`gallery: {line, panel: "(b)", caption}`.

### Figures and their numbers

Images with a caption and galleries with a caption are **figures**, numbered Fig. 1, Fig. 2, … in
the order they appear in the note (`figures.ts`, Extended Markdown). An image or gallery without any caption is not a
figure and gets no number.

````markdown
![](attachments/plot.png)
*Photon number versus $N$. \label{fig:photons}*

As \cref{fig:photons} shows, …   ← "As Fig. 3 shows"
````

- **An image's caption** is the line right below it, wholly in `*…*` or `_…_` (emphasis markers
  inside math such as `$\alpha^*$` do not count). The image must be alone on its line, at most
  three spaces in, and the pair must have blank lines (or the start or end of the note) around it;
  `imageFigureAt` decides this. This is how snapshots from the PDF viewer and Claude's `snapshot`
  have always been captioned, so those become numbered figures too.
- **A gallery** is a figure when it has a caption of its own or on any panel.
- **Labels.** `\label{name}` in a figure's caption (outside math) names it and is not shown; in a
  gallery panel's caption it names that panel, numbered "2(b)" as with subfigures. When an
  equation and a figure share a name, the equation wins.
- **References** (`crossref.ts`), as LaTeX's cleveref writes them, so the kind is never typed:

  | Written | Shows |
  |---|---|
  | `\cref{fig:a}`, `\cref{eq:x}`, `\cref{fig:p}` (a panel) | Fig. 2, Eq. (3), Fig. 2(b) |
  | `\Cref{fig:a}`, `\Cref{eq:x}` (start of a sentence) | Figure 2, Equation (3) |
  | `\cref{fig:b,fig:a}`, `\cref{eq:x,eq:y,eq:z}` | Figs. 1 and 2, Eqs. (1)–(3) |
  | `\cref{fig:a,eq:x}` | Fig. 2 and Eq. (3) |
  | `\ref{fig:a}`, `\eqref{eq:x}` | 2, (3) |

  Numbers of one kind are sorted, repeats dropped and three or more in a row written as a range;
  a name the note does not define shows as ?? in red. The name and number are joined by a
  non-breaking space, and every number links to its figure or equation.
- **Where the number goes.** Before the figure's caption: "**Fig. 2.** Caption". A gallery without
  a caption of its own puts it before its panels' labels in the app, "**Fig. 2**(a) First panel",
  and before the combined caption in exports.

**Two paths, one rule.** The renderer (`figureRule` in `render.ts`, after `galleryRule`) turns a
top-level paragraph that `imageFigureAt` accepts into `figure_open`/`figure_close` tokens with
the caption as an inline token (markers and labels taken out), numbers figures and captioned
galleries in token order and puts the labels in `env.figLabels`, where `R.eq_ref` looks after the
equations' labels (`lookupIn`, then `refHtml`, which wraps a `\cref` in `span.cref`). A figure is
`figure.figure` with an `.eq-anchor` per label, a panel's in its `.gallery-panel` (so read mode's
click on a reference and the exported links go there), the image centred and
`figcaption.figure-caption` below it. Captions are centred while short and fill the width when
long (`display: table`), as in LaTeX. The live preview (`figuresOfDoc` in `livePreview.ts`) and
the Markdown export use `figuresOf`, which applies the same `imageFigureAt` to the note's lines
and finds galleries through `mdscan`, so the numbers agree. In the live preview an image's
caption line gets a "Fig. N." chip and its `\label` is hidden until the cursor is on it; a gallery's
widget is rendered on its own, so its number and its `\ref`s are put right afterwards from the
whole note; reference chips show the written-out text ("Figs. 1 and 2") and Cmd/Ctrl+click on
them goes to the first. The Markdown export (`portableFigures`) writes "**Fig. 2.** *Caption*",
drops `\label`s and writes references that name only figures as text ("Fig. 2"); a reference that
names an equation stays as written, like the formulas. Embedded notes number their own figures.

### Embedded notes

In read mode, `renderEmbeds` (`richRender.ts`) fills each `![[Note]]` or `![[Note#Heading]]`
that resolves to a path. It loads the note and its `/api/notes/links` together, and `embedHtml`
(`embed.ts`) renders the note (or the section under the heading) with that link data, falling
back to the open note's for what it does not cover. Citations in an embed therefore show their
item, and embeds inside it get a `data-path` and are filled in turn, up to three levels
(`MAX_EMBED_DEPTH`); a note that embeds itself is not shown again.

### Saving: `NoteSession`

Each open note has one `NoteSession`. It keeps `saved` (the text and hash the server has) and
`text` (what the editor shows), and its status is `saved`, `dirty`, `saving`, `offline` or
`conflict`.

- **Autosave.** Every change calls `update()`. After `notes_autosave_ms` (default 1500 ms)
  without typing, `save()` sends the text with `saved.hash`. The editor also saves on blur, when
  the tab is hidden and before another note opens. Typing during a save triggers another save.
- **Drafts.** While text is unsaved, `{text, baseHash}` sits in local storage under
  `zc.noteDraft:<path>`. It is removed once the server has the same text. On open,
  `NoteSession.open` restores a draft; if its `baseHash` is not the file's hash, the session
  starts in conflict instead of overwriting.
- **Offline.** A network or server error keeps the text and retries with backoff from 2 s up to
  30 s. On `pagehide`, `flushOnUnload` sends a last `keepalive` save; the draft stays as a
  safety net.
- **Conflicts.** A 409 makes the session fetch the file. If it already holds our text, that is
  not a conflict. Otherwise saving stops and `conflictDialog` shows a diff: keep mine (save over
  the file's current hash), use the saved file, or, for a deleted note, save it again
  (`PUT` with `create: true`). Nothing is overwritten before the user chooses. If the file
  could not be read when the conflict began, `resolve` reads it again before using it. If that
  still fails, or writing a deleted note again fails, the session stays in conflict, the error
  is shown and the dialog comes back; a note someone else wrote again meanwhile becomes a
  conflict with their text.
- **Remote checks.** `checkRemote()` loads the file and replaces the editor text only when the
  session is `saved` with no edits. `editor.replace` changes just the differing span, so the
  cursor stays and the change is one undo step.

### How a change made by Claude reaches an open note

1. Claude calls `edit_note`. The MCP process reads the note, applies the edit and calls
   `NotesService.edit`, which writes with the hash it read, records a `.history` entry and
   updates `notes_index.db`.
2. The app's `ChangeFeed` notices that `notes_index.db` changed on disk and reports `notes` on
   the next `/api/changes` poll. `changes.ts` then calls `notesChanged()`, which runs
   `checkRemote()` and refreshes the tree, unless the user typed in the last few seconds.
   `notesView.ts` also calls `checkRemote()` every 5 s while a saved note is visible.
3. If the note has no unsaved edits, the new text replaces the editor content. `remoteArrived`
   loads the history; when the note is exactly Claude's result, the bar offers Show changes and
   Undo, and Cmd/Ctrl+Z in the editor also reverts it. Undo goes through the normal save.
4. If the user has unsaved edits, their next save gets a 409 and the conflict dialog appears.
   On the MCP side, `edit_note` re-reads and retries up to three times when the user saved first.

### Exporting a note

**Export** in the note head (and **Export…** in a note's menu in the notes list) opens
`exportMenu` (`exportNote.ts`). Everything happens in the browser, from files the app serves, so
it works offline. The open note is exported as it is in the editor, saved or not; another note is
loaded with `GET /api/notes/note`. Its `/api/notes/links` gives the library items it cites, with
the library list as the fallback (as in read mode).

- **HTML page.** `embedHtml` renders the note as read mode does. It is drawn off screen so that
  `renderEmbeds` (which now waits for its code and diagrams), `highlightCodeBlocks` and
  `renderMermaidBlocks` can fill it, with Mermaid in its light theme. Then what only works inside
  the app is removed: `[[links]]` become text, task boxes are disabled, and the copy buttons,
  `data-line` and the properties block are dropped. Images served by the app become `data:` URLs.
  The cited keys (embeds included) give a References list, in order of first citation. Each
  citation of a listed key links to its entry (`#ref-<key>`, `referenceId`); a key not in the
  library stays text. The styles are `css/tokens.css`, `css/notes.css` and
  `css/export.css` (page and print rules), with `<html data-theme="light">`. When there is math,
  KaTeX's CSS is added with its woff2 fonts as `data:` URLs. The result is one file that needs
  neither the app nor the network.
- **PDF.** The same page goes into a hidden `<iframe srcdoc>`, which waits for its fonts and then
  calls `print()`. The browser's print dialog saves the PDF, and `export.css` sets the margins and
  page breaks. No PDF library is needed. The browser keeps the links in the PDF: citations jump to
  their entry, and DOIs open the paper.
- **Markdown.** `portableMarkdown` (`portable.ts`, tested in `tests/js/portable.test.mjs`) works
  on the `mdscan.scan` links, so code and math are left alone. It drops the front matter, adds the
  title as `# Title` unless the note starts with one, writes citation groups with `citeLabel` as
  in read mode, writes `[[links]]` as their alias or target, and drops the `|width` of images.
  `![[embeds]]` are replaced by the embedded note (or section), itself converted, up to three
  levels and never in a cycle; `exportNote.ts` loads them first. It ends with
  `## References` (`referenceMarkdown`). Image paths are kept as they are in the notes folder.

**References.** `exportNote.ts` loads the full library items of the cited keys with one
`GET /api/items?ids=…` (`fullItems`), since the citation index has only authors, year, title and
journal. If that request fails, the entries are made from the index. `referenceParts`
(`portable.ts`) writes an author–year entry by item type:

| Item type | Entry |
|---|---|
| journal article, preprint, web page, other | Authors (Year). Title. *Journal*, *volume*(issue), pages. A preprint adds its archive id (`arXiv:…`). |
| book | Authors (Year). *Title* (2nd ed., Vol. 3). Series. Publisher, Place. |
| book section, conference paper | Authors (Year). Title. In *Book or proceedings* (pp. 1–20). Publisher. |
| thesis, report, data set | Authors (Year). *Title* (series) [Thesis]. University or institution. |

`referenceLink` adds the DOI as `https://doi.org/…`, else the arXiv page of an `arXiv:` archive id,
else the item's URL. `referenceHtml` makes it a link, and `referenceMarkdown` writes it as
`<…>`.

## Where to change things

| To change… | Edit |
|---|---|
| What counts as a link, citation, tag or heading | `backend/notes/parser.py` and `frontend/src/views/notes/mdscan.ts` together, plus a fixture in `tests/notes/fixtures/` and a bump of `SCHEMA_VERSION` in `backend/notes/index.py`. The renderer (`render.ts`) uses `parseCites`, `splitWiki` and `attachmentOf` from `mdscan.ts`, so it follows. |
| Front-matter reading or writing | `backend/notes/frontmatter.py` (and `splitFrontmatter` in `mdscan.ts` for the delimiters). |
| A new query over notes | A method on `NotesIndex`, called from `NotesService`, exposed in `backend/api/notes.py`. |
| The index layout | `SCHEMA` and `SCHEMA_VERSION` in `backend/notes/index.py`. Bumping the version rebuilds it. |
| File safety, naming or the trash | `backend/notes/vault.py`. |
| Image types, sweeping or retention | `backend/notes/service.py` (`IMAGE_TYPES`) and `backend/notes/attachments.py`. |
| What a key change rewrites | `backend/notes/rekey.py`. |
| Saving behaviour | `noteSession.ts`, with a test in `tests/js/noteSession.test.mjs`. |
| How notes look when rendered | `render.ts` (tested in `tests/js/render.test.mjs`), `equations.ts`, `macros.ts`, `livePreview.ts`. |
| Claude's note tools | `backend/mcp/tools/notes.py`; see [mcp.md](mcp.md). |
| What an exported note looks like | `exportNote.ts` (HTML, PDF), `portable.ts` (Markdown, reference entries), `frontend/css/export.css`. |
| Which images are figures, and how they are numbered | Extended Markdown's figures (`frontend/src/plugins/core/extended-markdown/figures/`): `figures.ts` (`imageFigureAt`, `figuresOf`) for the rule and the text; `figureRule` in `markdown.ts`; `livePreview.ts` for the chips. |
| How galleries are written, drawn or exported | Extended Markdown's figures: `gallery.ts` (the block, the figure caption), `markdown.ts` (`galleryRule`), `galleryView.ts` (the carousel), `exportStatic.ts`, `portable.ts`; `backend/notes/gallery.py` for the MCP tools. What counts inside one is the parser's (above), plugin or not. |

The notes format, the `data/notes/` layout and these endpoints are contracts used by the MCP
server and older data. Change them only deliberately. Tests live in `tests/notes/` and
`tests/js/`; the browser checklists `notes_checklist.py`, `links_checklist.py`,
`claude_edits_checklist.py`, `render_checklist.py`, `equations_checklist.py`,
`attachments_checklist.py`, `export_checklist.py` and `gallery_checklist.py` in `tests/e2e/` cover the workspace (see [testing.md](testing.md)).

## Extended Markdown (core plugin)

**Extended Markdown** collects Markdown syntax beyond Synapse's own (math, citations, links,
callouts, footnotes and the rest stay in the core renderer). It starts on and adds no page of its
own. In Settings → Plugins it can be turned off as a whole, and each of its extensions has a
switch among its settings:

- **Galleries and numbered figures**: see [Galleries](#galleries) and
  [Figures](#figures-and-their-numbers);
- **Task blocks**: a compact fenced collection of task IDs, described in
  [tasks](tasks.md#task-blocks-in-notes);
- **Spoilers**: below.

Off, notes render as ordinary Markdown and their text is unchanged.

### Sections and internal references

Section references are part of the core renderer because the same `\ref`/`\cref` engine already
numbers equations. End a Markdown heading with `\label{name}`; the label is hidden and the heading
receives its hierarchical number. `\ref{name}` shows the bare number, `\cref{name}` shows
“Sec. 2.1”, and `\Cref{name}` shows “Section 2.1”. The number is an internal link to the heading.

```markdown
## Experimental method \label{sec:method}

The calibration is described in \cref{sec:method}.
```

Heading levels determine the hierarchy. A leading H1 is treated as the note's document title, so
the next level starts at 1 rather than 1.1; otherwise numbering starts at the shallowest heading.
Put `\tableofcontents` on its own line for a linked, nested contents block in Live and Read modes
and in HTML/PDF exports. Use a star after the heading marks (`##* Internal detail`) to keep a
numbered heading out of the contents. Inline formatting and LaTeX in heading names are rendered
inside the contents too. Labels in inline code or math are left alone. Read mode, standalone HTML, and exported
PDFs all retain the anchors and internal links. Equation labels win a duplicate name, then figure
labels, then section labels.

### Spoilers

Use `Before ||**hidden words**|| after` for an inline spoiler. Its Show/Hide button works
with mouse, Enter and Space. For a collapsible block:

```markdown
:::spoiler Optional summary
Hidden **Markdown**, citations, links and $math$.

:::spoiler Nested summary
Nested content.
:::
:::
```

An omitted summary reads “Spoiler”. Blocks may nest and include fenced code; a `:::` inside a
code fence does not close a block. Unclosed blocks and inline markers remain literal; inline
spoilers must close on the same line. Escape an opening pipe with `\|` or use inline/fenced code
to show literal syntax. Live preview shows source when editing that range and otherwise uses
the same renderer as Read mode. Disabling redraws current/retained notes as ordinary Markdown
and preserves every source character. Existing Mermaid handling is unchanged.

Spoilers conceal presentation, not the stored text. Markdown export retains the markers;
HTML/PDF export reveals the contents and opens blocks so printed/exported text is complete.
Raw note HTML is still escaped.

### Where the code is

`frontend/src/plugins/core/extended-markdown/` holds one folder per extension (`figures/`,
`tasks/`, `spoilers/`), each a `MarkdownExtension` (`extension.ts`): its markdown-it rules, what HTML/PDF
export must open up, and what it starts while on (listeners, a note hook). `index.ts` lists the
extensions, reads the package's settings (one boolean per extension, keyed by its id, in
`plugins/extended-markdown/manifest.json`) and follows them through the change feed: a switch
takes effect in open notes at once. Spoilers reach the notes code through the `extended` hook in
`views/notes/hooks.ts`; galleries and figures through the `figures` hook, which they had before
they joined the package. A new syntax is a new folder, a line in that list and a setting.

Live preview asks for the spoiler ranges on every keystroke, so finding them is one pass over the
note: only positions holding `||` are examined, and code/math ranges are looked up by binary
search; the result is kept until the text changes. `tests/js/spoilers.test.mjs` checks the ranges
against the rules applied position by position and keeps a 300 KB note under 250 ms.
