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
| `.history/<id>.json` | The version each of Claude's edits replaced (see [History of Claude's edits](#history-of-claudes-edits-backendnoteshistorypy)). |

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
| `author` | `human` (default) or `ai` for notes written by Claude. |
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
| `links` | Every link, with `kind` (`note`, `embed`, `cite`, `image`), `target`, `target_norm`, heading, alias, locator, page, prefix, `suppress_author`, `grp`, line and context. |
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
- `link_graph()` for the Graph view and `image_links()` for the attachment sweep.
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
| `# Heading` | heading (ATX only, at most three spaces of indent) |
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
backslash escapes the next character. `mask()` builds a copy of the body in which those
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
- `edit(...)` is the save used for Claude's changes: same check, plus a history entry.
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

### History of Claude's edits (`backend/notes/history.py`)

`NoteHistory` stores one JSON file per change in `.history/`: `id`, `path`, `at`, `by`,
`summary`, `before` (the whole previous text), `before_hash`, `after_hash`. One file per change,
with a unique name, means the app and the MCP server cannot overwrite each other's entries.
`after_hash` tells whether the note is still exactly what Claude left, which decides whether the
app offers a plain Undo. Moves rewrite the stored paths (`moved`). `prune` keeps at most
`KEEP` (1000) entries and none older than a year.

## HTTP API (`backend/api/notes.py`)

Notes are addressed by their vault path. Every successful `POST`/`PUT` records a `notes` change
in the change feed. `guarded` turns `Conflict` into 409 with `conflict` and `current_hash`,
`NoteExists` into 409 with `exists` and `path`, other `VaultError`s into their status and
`ValueError` into 400.

| Method | Path | What it does |
|---|---|---|
| GET | `/api/notes` | Listing: every note's index entry, the folders, counts. |
| GET | `/api/notes/note?path=` | Text, hash and index entry of one note. |
| POST | `/api/notes/note` | Create (`title`, `folder` or `path`, `item_id`, `text`). |
| PUT | `/api/notes/note` | Save `text` over `hash`; with `create: true`, write a note that must not exist. |
| POST | `/api/notes/move` | Rename or move a note (`path`, `to`, optional `hash`); returns `broken_links`. |
| POST | `/api/notes/folder` | Create a folder. |
| POST | `/api/notes/folder/move` | Rename or move a folder; returns the moved notes and `broken_links`. |
| POST | `/api/notes/trash` | Trash one note (`path`) or several notes and folders together (`paths`, `folders`). |
| GET | `/api/notes/trash` | Trashed notes with `trashed_at` and `deletes_at`, and the retention. |
| POST | `/api/notes/restore` | Restore one note (`trash_id`, `path`) or a whole batch (`trash_id` only). |
| GET | `/api/notes/history?path=` | Claude's changes to a note, newest first, without their text. |
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

## Frontend (`frontend/src/views/notes/`)

| Module | Role |
|---|---|
| `notesView.ts` | Builds the workspace, opens and closes notes (`openNote` serialises opens), wires `NoteSession` to the UI. |
| `workspace.ts` | Shared state `ws` (open note, editor, listing, link data, cite index) and `hooks` back into `notesView.ts`. |
| `noteSession.ts` | Saving one note: autosave, drafts, retries, conflicts. No DOM. |
| `tree.ts`, `treeInput.ts` | The notes list and filter; mouse, keyboard, context menu, drag and drop. |
| `fileOps.ts` | New, rename, move, trash with Undo, the trash dialog, rescan. |
| `editor.ts`, `livePreview.ts`, `previewWidgets.ts` | CodeMirror 6 (`vendor/cm6/`), the live preview decorations (built on `mdscan.ts`) and the widgets they draw. |
| `completion.ts`, `library.ts` | `[@` and `[[` autocomplete and its ranking. |
| `render.ts` | Markdown to HTML with markdown-it and KaTeX (`vendor/md/`): math, links, citations, callouts, tasks. Raw HTML is never rendered. No DOM. |
| `richRender.ts` | Mermaid (loaded on demand from `vendor/mermaid/`), code highlighting, filling embedded notes into the page. |
| `embed.ts`, `linkData.ts` | What an embedded note shows; what a note links to (`/api/notes/links` as maps for the renderer). No DOM. |
| `equations.ts`, `macros.ts` | Equation numbering, `\label`/`\eqref`, and math macros (physics package, Settings, `\newcommand`). |
| `mathClick.ts`, `mathFit.ts` | Clicking into rendered math; shrinking wide equations. |
| `readView.ts` | Read mode, following `[[links]]` (offering to create missing notes), going to a line. |
| `sidePanel.ts`, `panels.ts` | Status, breadcrumbs and details panel; showing and hiding the side panels. |
| `citeCard.ts`, `citeTarget.ts` | Citation hover cards; where in the PDF a citation points (quoted words or a snapshot's region). |
| `aiBar.ts`, `aiEdits.ts`, `diff.ts` | Claude's edits: the bar, the history dialog, line diffs. |
| `conflict.ts` | The dialog for a note changed or deleted elsewhere. |
| `attachmentsDialog.ts`, `rekeyDialog.ts` | The attachments dialog; the preview before a key change. |
| `exportNote.ts`, `portable.ts` | Exporting a note as PDF, HTML or Markdown; the Markdown conversion and reference entries (no DOM). |

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
  renderer, so math, citations and links in its cells are rendered as in read mode.
- **Read mode.** `renderRead` keeps every top-level block whose HTML did not change, and changes
  the DOM in place: ticking a task redraws only its list, and formulas, diagrams and embedded
  notes elsewhere are not drawn or loaded again. Opening read mode, the theme and the math
  settings redraw everything (`fresh`), so embedded notes are reloaded then.
- **KaTeX** output is remembered by formula and set of macros (`katexMemo` in `render.ts`),
  shared by every renderer.

Task boxes are drawn the same way in both modes (a rounded square, ticked and filled when done),
and a done task is greyed and struck through.

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

The notes format, the `data/notes/` layout and these endpoints are contracts used by the MCP
server and older data. Change them only deliberately. Tests live in `tests/notes/` and
`tests/js/`; the browser checklists `notes_checklist.py`, `links_checklist.py`,
`claude_edits_checklist.py`, `render_checklist.py`, `equations_checklist.py`,
`attachments_checklist.py` and `export_checklist.py` in `tests/e2e/` cover the workspace (see [testing.md](testing.md)).
