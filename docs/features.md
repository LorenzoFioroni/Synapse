# Features

A guide to Synapse’s library, notes and optional plugins. For installation and device setup,
see the [README](../README.md); for code ownership, see the [architecture map](architecture.md).

## Library, notes and PDFs

- **Clear startup**: while settings and the library are being prepared, a branded screen says
  what Synapse is loading instead of leaving an empty app shell on screen.

- **Responsive on phones and tablets**: compact, touch-sized navigation keeps the library,
  item details, notes, tasks and Settings usable one pane at a time on narrow screens.
  On tablets and desktop, drag the item-pane divider with a finger or mouse; its width is
  remembered after reload. Phone item details use the full view.

- **Notification center**: a bell beside Settings keeps unread notices and long-running jobs,
  with a red count on the bell and browser tab icon. Info, Success, Warning and Error are separate
  from optional OS delivery: arXiv Today's ready notice can reach the OS after you grant permission
  under **Settings → Notifications**, where OS delivery can also be disabled again.
  Job notifications whose window crashed or restarted become interrupted and can be dismissed;
  browsers without automatic owner detection offer manual dismissal for previous windows' jobs.
  History stays in this browser. See [notifications](notifications.md).

- **Search** in three scopes, picked from the search box (the default is a setting):
  *Title, Abstract & Authors* · *All Fields* · *All Fields & Full Text*.
  Terms are ANDed, `"exact phrase"` and `-exclude` work, and accents are ignored
  (`banuls` finds *Bañuls*). Full-text hits show a highlighted snippet.
- **Full-text index** of attached PDFs, kept in its own database so the library
  stays small. New attachments are indexed in the background; progress is shown
  in the sidebar and Settings, which can also rebuild it.
- **Import / export**: BibTeX, RIS, EndNote (tagged `.enw` and XML) and CSL JSON. Library export
  writers are the **Export Formats** plugin, on by default; turning it off preserves imports and
  clipboard citation copying.
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

- **Find preferences** with Search settings (or Cmd/Ctrl+F while Settings is open).
  Matching built-in and plugin sections stay editable. Notes has its own section for enabling
  notes, trash retention, width, equation numbering, physics commands and math macros.

- **Workspace controls** follow what you are doing: Library keeps item actions and search;
  Notes offers new notes/folders, note export and note search; Tasks keeps its own search and
  creation controls. The footer shows library counts, the open note's word count and save state,
  or open/overdue task counts. Phone menus follow the workspace too.

- **Add items** by DOI, arXiv ID or ISBN (several at once), by dropping PDFs (the
  DOI/arXiv id on the first pages is used to fetch metadata), or manually.
  When creating items through `POST /api/items`, unsupported field names return HTTP 400
  with an explanation instead of silently discarding metadata. Use `journal` for Publication;
  see [the accepted creation fields](storage.md#creating-items-through-the-http-api).
- **Retrieve Metadata** works on one item or a whole selection: one review dialog
  with a section per article, word-level diffs that wrap long text with a checkbox per field, and the
  article's unchanged metadata below. Nothing is saved until you apply; replacing an
  existing citation key is opt-in. Several lookups run at once (arXiv requests stay spaced out
  even after delayed wake-ups), and they can be stopped, or continue in the background with a progress notification and a
  *Review* button when done. Articles with updates are listed first, those not found next, and
  the ones already up to date are folded away at the end.
  OpenAlex author enrichment uses the names credited on the paper, with author-profile names
  as a fallback when those are missing.
- **Adding items** (Settings → Adding Items): whether to retrieve metadata for imported items, find
  an available PDF, fetch reference lists, and turn imported keywords into tags — each *Never*,
  *Always* or *Ask* (a ticked checkbox where you add the items: the import dialog, Add by
  Identifier, the browser button). They all run in the background.
- **References / citation graph** (online fetching is the **Reference Sources** contributed plugin;
  turning it off keeps saved references and citation links): for new items (see Adding Items) and after Retrieve Metadata
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
  paper shows a card (paper note, Show in Library, PDF, Add to Library). It is a contributed plugin
  (**Graph**, also under **Plugins** in the sidebar): turn it off in Settings → Plugins if you don't use it.
- **Find Available PDF** (single or batch) looks for open-access copies (Unpaywall,
  arXiv: the plugin Open-Access PDFs, on by default; other plugins can add places to look) and checks the file is really the right paper. Batch runs skip items
  that already have a PDF, show progress, and can select the ones that weren't found.
  These downloads, web-linked PDFs and plugins' public HTTP requests connect only to validated
  public DNS addresses, including after redirects, while preserving HTTPS certificate verification.
  They bypass environment proxies; explicit loopback permission still supports local models.
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
- **Notes history**: every version of your notes' text is kept, with who made it — you (and on
  which device), an AI app (with its name and summary) or another program — like Overleaf's
  history. **History** in a note's head (or **Versions…** in its details, or **History of This
  Note…** in the command palette) lists them: your edits close together as one row, each AI change on its own; select one
  to see exactly what it changed (changed words marked) or how it differs from the note now, and
  **Restore This Version** (⌘Z takes it back). **Notes History…** shows every change to all notes;
  **Mark Milestone…** names how all notes are now (a submitted draft, say), shows what changed
  since and can put every note back as it was. Edits become a version when you leave the note, or
  after two minutes without typing; an edit you undo leaves no version. Images are not versioned,
  and nothing else is ever deleted from the history. It needs [Git](https://git-scm.com) (on a Mac: `xcode-select --install`), kept in
  `data/notes-history.git` apart from your own Git setup; backups include it. See
  [notes history](notes-history.md).
- **Notes**: Markdown notes with live preview, KaTeX, code, Mermaid and images; `[@key]`
  citations and `[[links]]` between notes; a paper note per item. Autosave keeps unchanged rendered
  blocks in place to avoid scroll jumps while writing; callouts preserve explicit italics.
  Cmd/Ctrl+B, I and U toggle bold, italic and `++underline++`; Cmd/Ctrl+Shift+X adds strikethrough,
  Cmd/Ctrl+Shift+C inline code, and Cmd/Ctrl+K a web link. Dollars and backticks pair automatically
  and wrap selected text. Tables stay inside their callouts in Live preview.
  Dragging a selection keeps rendered math and code in place until the mouse is released.
  See [writing shortcuts](notes.md#writing-shortcuts-and-paired-delimiters) for the exact keys and behavior.
  An item with several PDFs gets
  one button per PDF in citation cards and in its menus; `[@key, p. 4, att:12]` names the PDF with
  attachment id 12 (a citation without `att:` means the first PDF). **Export** (the button next
  to Live | Source | Read, or **Export…** in a note's menu in the notes list) saves a note as a
  **PDF** (print dialog, "Save as PDF"), a self-contained **HTML page**, or **Markdown** that any
  viewer shows. Citations are written out as "(Smith et al. 2021, p. 4)" and, in PDF and HTML,
  link to their entry in a References list at the end. Each entry has what is needed to find the
  work (journal, volume, issue and pages; publisher and edition for books; the arXiv id of a
  preprint) and its DOI as a link (else its arXiv page or URL). `[[links]]` become their text, embedded notes are included, and formulas, diagrams, code and
  images are kept. **Galleries** put several images in one carousel: a ` ```gallery ` block
  (` ```gallery|600 ` sets its width) with one image per line, each followed by its caption, then
  after a blank line the caption of the whole gallery (see [Galleries](notes.md#galleries)).
  Arrows, dots or a swipe turn it; exported, it becomes one figure with the panels side by side
  (at most three to a row), each marked a, b, … in a white circle on its upper left corner, under
  one caption: the gallery's, then
  "(a) first panel's. (b) second panel's." **Figures** are numbered Fig. 1, 2, … like equations: an
  image with a caption (the line right below it in `*italics*`, with blank lines around the two)
  or a gallery with a caption; images without a caption are not numbered. `\label{fig:name}` in a
  caption names the figure (in a panel's caption, that panel). References work like LaTeX's
  cleveref: `\cref{fig:name}` writes "Fig. 2", `\cref{eq:name}` "Eq. (3)", `\cref{a,b,c}` "Figs. 1
  and 2 and Eq. (3)" (ranges as "Eqs. (1)–(3)"), `\Cref` the full word for the start of a
  sentence ("Figure 2"), and `\ref` / `\eqref` the bare number (see
  [Figures](notes.md#figures-and-their-numbers)); galleries and figure numbers are part of the core
  plugin **Extended Markdown**, with a switch of their own in its settings. A Markdown heading can
  similarly end in `\label{sec:name}`; `\ref`, `\cref` and `\Cref` produce linked section numbers
  in Read mode and HTML/PDF exports. Switch with **Library** and **Notes** at the top of the
  sidebar or Cmd/Ctrl+Shift+L; the browser's Back/Forward (mouse buttons,
  Cmd+[ / Alt+←) restore notes, Graph, Tasks, plugin pages and library views/selections.
  If a destination's plugin is off, navigation returns to the library (or Notes for Graph).
  Images pasted into notes are kept in the notes' attachments folder. Several images pasted or
  dropped at once (or chosen with **Insert Image…** in the command palette) become one gallery
  with Extended Markdown's galleries on, else one image per line; one that no note uses any more
  moves to an attachment trash by itself (and comes back if a note uses it again). **Attachments…**
  in the notes list menu shows them all — which notes use each, insert one into the open note,
  restore or delete trashed ones. Deleted notes and trashed attachments are deleted for good after
  a while (Settings → Notes → *Empty the notes trash*: never, 1 day … 6 months; default 1 month). The pencil column
  in the item list marks items with a paper note (blue) or cited in notes (grey).
  In live preview a formula, link or citation shows its source only while the cursor is in it, and
  clicking a symbol of a rendered formula puts the cursor on it; a `$$` block being edited shows
  the formula below its source. Tables are rendered in live preview too, with their math and
  citations. Source mode shows the Markdown as code, in a monospaced font. Switching between
  Live, Source and Read keeps your place in the note. While Notes is open, the toolbar's two panel buttons hide and show
  the notes list (also Cmd/Ctrl+\) and the note details; a hidden list slides in when the pointer
  reaches the window's left edge (or with a tap there). Settings → Notes → *Note width* lets
  the text use the whole pane. A `$$` equation gets a number when it has a `\label{name}`
  (`equation`/`align` environments are numbered as in LaTeX, `\tag{3}` sets one; Settings can
  number every one), and `\eqref{name}` refers to it; the number never overlaps the formula, and
  an equation too wide for the pane is shrunk a little, including space for its number (then scrolls).
  Formulas know the physics package's commands (`\vb`, `\dv`, `\pdv`, `\qty`, `\abs`, `\expval`, `\mel`, `\comm`, …), your own
  macros (Settings → Notes → *Math macros*), and a `\newcommand` written in any formula of the
  note (it applies to the whole note).
  In the notes list: Cmd/Ctrl-click and Shift-click (or Shift+arrows) select several,
  Delete/Backspace moves them to the notes trash (with Undo), F2 renames, Enter opens,
  drag to move; folders can be renamed, moved and deleted the same way.
- **Extended Markdown** (contributed plugin, on by default) is where Markdown syntax beyond the basics
  lives, each part with its own switch in its settings: **galleries and numbered figures** (above),
  **task blocks** (` ```tasks table group=deadline completed=bottom ` with one task ID per line;
  their rich block can filter open/done tasks, sort them, and create a task in place),
  and **spoilers**: `||inline spoilers||` with a keyboard-accessible
  Show/Hide button, and collapsible `:::spoiler Optional summary` blocks closed by `:::`. Blocks
  support nested Markdown and spoilers. Source text stays intact when the plugin is off; HTML/PDF
  exports reveal spoiler contents. See [Extended Markdown](notes.md#extended-markdown-core-plugin).
- **Tasks** (open it under **Plugins** in the left sidebar; a contributed plugin, on by default; turn it off in Settings → Plugins without losing tasks): to-dos with a title, a due date, details written in Markdown like a note, and
  sub-tasks to any depth. In a note, Cmd/Ctrl+Enter (or **Add Task to This Note…** in the command palette) opens a
  small popup: name the task (the line's text, if any), give it a date in words ("fri", "27 oct",
  "in 3 days") and details, or pick a task you already have — or type `{{task` and a few words of
  its title to pick one from a list, as `[@` does for papers. The note then shows the task as one
  line with its checkbox; ticking it or clicking it to edit changes the task, not the note (the note
  only holds `{{task:id}}`). Plain `- [ ]` checkboxes stay checkboxes; select some and press
  Cmd/Ctrl+Enter to turn them into tasks. The **Tasks** page (Cmd/Ctrl+Shift+K) lists the tasks of
  all notes, with views (Open, Today, Next 7 days, No date, Not in a note, Done, each note), search
  with filters (`due:week`, `is:done`, `note:"…"`, `has:body`), sorting, grouping, quick add, a
  details pane, keyboard editing, drag and drop, and Undo. Ticking a task whose sub-tasks are open
  asks whether to tick them too. A task you just made from a note and whose line you then delete
  goes to the trash with it (Undo, or Cmd/Ctrl+Z in the note, brings it back); older tasks stay in
  Tasks. Tasks are kept in the library database, so backups include them; deleted tasks can be
  restored. See [`docs/tasks.md`](tasks.md).
- **Autocompletion** in notes (a contributed plugin, off by default: turn it on in Settings → Plugins): after a short pause a
  model running on this computer in [Ollama](https://ollama.com) suggests how the text goes on, in
  grey after the cursor. Tab accepts, Esc dismisses, Cmd/Ctrl+→ takes one word, Alt/⌥+\ asks for
  one now, clicking it accepts it. Only local models are supported: install Ollama and a model
  (`ollama pull qwen2.5-coder:1.5b`); Settings says what is missing. Without them nothing is sent
  and the notes say so once. Suggestions also work before closing marks followed by punctuation
  (such as `$.`); they stop before existing closers while retaining nested brackets and escaped
  dollars. Long notes retain the title, current section and relevant earlier definitions alongside
  recent text, within the existing model context budget. See [docs/autocompletion.md](autocompletion.md).
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

## Command palette

**⌘⇧P** (Ctrl+Shift+P, or F1) opens the command palette: type a few letters of a command (New Item,
Show Tasks, Settings: Backups …) and press Enter. While a note is open: **Insert Image…**,
**Insert Figure with Caption…** (an image, or a gallery when you choose several, with a caption to
type and a `\label` to refer to it), **Insert Citation…**, **Insert Link to Note…**, **Add Task to
This Note…**, **Insert Existing Task…**, Insert Table / Callout / Numbered Equation / Mermaid
Diagram, Live Preview / Source / Read Mode, Rename / Move / Delete / Export This Note, Copy Link to
This Note, Show This Note in the Graph, and for a paper note its paper and PDF. What the item menu does to the selected items is
there too while the library is shown (Add PDF Attachment…, Find Available PDF, Retrieve Metadata…,
Edit Tags…, Add to Folder…, Copy as BibTeX …), as are New Note…, New Folder, Add PDFs as New
Items…, Empty Trash…, "Settings: <plugin>" for each plugin that is on, and the plugins' own
commands, with their keys.

## The sidebar

The sidebar is one list of what Synapse shows: **Library**, **Notes**, and what plugins add
(**Graph**, **Tasks**, a list such as **arXiv Today**, a plugin's page). What belongs to the
library alone — Duplicate Items, Trash, folders, smart folders and tags — is shown below it only
while the library is open. Library stays marked while one of its folders is shown; clicking it
from Notes or Tasks goes back to the library as you left it, and clicking it in the library shows
all items.

## Plugins

Plugins add features without changing Synapse itself. To install one, put its folder in
`data/plugins/`, open **Settings → Plugins**, press **Look Again** and turn it on. Turning it on
asks first and lists what the plugin may do (read the library, connect to sites, offer tools to
AI apps). A plugin runs with full access to your library and computer, like any program you
install, so turn on only plugins you trust. Each plugin that is on has a section of its own in
Settings (listed under Plugins, opened by the **Settings** button on its row or "Settings:
<plugin>" in the command palette) with its settings, its log and a Restart button; a plugin that keeps failing is turned off and Settings says why.
Bundled and installed plugins share one list, with bundled rows labelled "comes with Synapse".
If a plugin keeps the app from working, start with `python server.py --safe-mode` to use the basic
library and notes while diagnosing it. Safe mode stops bundled and installed plugins, preserves
their settings and enabled choices, and disables their controls until you restart normally.

All bundled optional feature plugins now use the public API as **contributed plugins**.
Tasks starts on and retains its records, history and note-link integrity in core.
**Open-Access PDFs** (where Find Available PDF looks),
**Export Formats** (the library exports), **Metadata Sources** (scholarly lookups) and
**Reference Sources** (citation lists) are contributed plugins built only on the public plugin
API, as yours would be; they come with Synapse and start on, but safe mode stops them like any other
contributed plugin. **Autocompletion** is also contributed: it starts off (or as previously
configured), uses only Ollama on this computer through an explicit loopback permission, and stops
in safe mode. Plugin API 1.3 exposes plain-text editor suggestions to plugins; 1.4 adds offline scholarly
helpers for identifiers, author names and title matching, shared with the library and importers.
API 1.5 lets reference plugins declare named catalogues and share the existing reference-record
contract. Saved provider identities remain stable and the item pane can show their display titles.
API 1.6 adds permission-checked metadata editing, explicit review dialogs, progress notifications and
field scheduling helpers. **Metadata Check** now uses these public APIs as a contributed plugin;
it stays off by default, preserves settings and cached findings, and stops in safe mode.
API 1.8 adds public Markdown/figure contributions and a shared renderer SDK. **Extended Markdown**
is now contributed: existing syntax, switches and exports remain, and safe mode stops it.
API 1.7 adds permission-gated notes/citation reads and Notes visualizations. **Graph** now uses
these public APIs as a contributed plugin: its local/full views, sidebar, history and shortcut
remain, and safe mode stops it.
API 1.9 adds permission-checked task services and shared editor/UI integration. **Tasks** is now
contributed, with its existing routes, AI tool names, shortcut and saved records preserved.
Turn plugins off in Settings → Plugins without losing their data. The standalone MCP server follows
those choices. Safe mode keeps the basic library and notes available and stops contributed plugins. Tasks and Graph add their own entries to the sidebar's
list; their selection follows the visible workspace when switching between views. Plugins'
settings sections appear and disappear immediately as their plugin is switched on or off, including
while Settings stays open. Disabling Autocompletion dismisses visible
suggestions and cancels pending ones immediately.

The built-in metadata providers (Crossref, OpenAlex, arXiv and Open Library) are the
**Metadata Sources** contributed plugin, on by default and stopped in safe mode. Switch it off in Settings → Plugins to stop
those lookups; all existing metadata stays in the library. It uses the public HTTP client with
declared provider hosts, the shared arXiv queue, and Online Services credentials for OpenAlex.
Other plugins can supply sources too.

Three plugins come with Synapse, off until you turn them on:

- **Citation Counts** adds a *Citations* column from OpenAlex and Semantic Scholar, refreshed
  weekly, and lets you search for `citations:>100`.
  A batch not yet indexed by Semantic Scholar still uses the OpenAlex fallback; papers neither
  service knows keep an unknown count and are checked again weekly.
- **Metadata Check** compares every identifiable library record with current metadata every
  three days (or as often as you set), flags fields to review, and identifies journal versions of
  arXiv preprints. **Run Now** checks everything at once, with progress in the notification center.
  **Review Changes** opens the metadata review dialog from stored results: apply selected fields,
  edit values, reject suggestions, or ignore a paper altogether. Rejected values stay hidden until
  they change; Settings lists rejected suggestions and ignored papers so you can restore them.
- **arXiv Feed** lists each morning's arXiv papers in the categories you choose, ranked by how
  they connect to your library (library references they cite, shared authors, title/abstract similarity), with
  a notification ("12 new papers, 3 connected to your library"), a **+** to add a paper and
  Dismiss for the rest. It stays one entry in the main sidebar; opening it shows a second sidebar
  for choosing today or any retained day. Earlier days stay for a month, and Claude can ask for
  them. Empty or delayed feeds are checked again every 30 minutes on announcement weekdays until
  that day's papers arrive. Day labels follow the calendar even without a new fetch. Set **Your
  author names** to distinguish citations to your authored library papers: these score 6 instead
  of 3. Shared authors contribute 0.5 per library paper, capped at 1.5. Similarity is local TF-IDF
  on titles and abstracts. Add full publication names to **Followed authors**, one per row, to
  show a gold **Following** badge on their papers across retained feed days. Hover over
  the badge or open the paper to see the matched names. Matching ignores case, accents and extra
  spaces; add name variants separately. A Synapse red **Cites you** badge marks citations to your
  authored library papers, identified through **Your author names**. Both badges can appear
  together on papers and retained days; the main feed entry shows only today's indicators.
  Day indicators use a white star on yellow for Following and a white megaphone on Synapse red for Cites you. Hover for counts.
  Categories and author lists share Add/Remove row controls; Enter adds a row.
  These badges do not change scores or ordering.
  OS notification delivery requires the app to be open; background phone push is not supported.

A plugin can offer a **list of papers** that are not in your library yet (today's arXiv papers,
say), under **Plugins** in the sidebar. It shows like a folder, ranked by the plugin (often by how
each paper connects to your library: papers it cites or that cite it, shared authors, similar
topics); the item pane says why each one is listed. Click **+** on a row, or **Add to My
Library** in the item pane, to add a paper; right-click or select several to add them to a folder.

A plugin has a `manifest.json`, Python for the server (routes, tools for Claude, listeners for
changes in the library, settings and stored data, and per-item fields: values such as a citation
count that appear as list columns, search filters like `citations:>100` and rows in the item pane) and JavaScript for the app (pages listed under
**Plugins** in the sidebar, sections in the item pane, actions in the item menu, commands).
Start with the [plugin authoring guide](plugin-authoring.md) and its runnable Hello Library
example. [Plugins](plugins.md) is the API reference;
[`docs/examples/recently-added`](examples/recently-added) shows a larger plugin to adapt
([`suggested-reading`](examples/suggested-reading) offers a list of papers).
To write one, `python server.py --new-plugin my-plugin --dev-plugins ~/synapse-plugins` makes a
plugin to start from, and `python server.py --dev-plugins ~/synapse-plugins` restarts it whenever
you save a file. Plugins can also run work on a schedule, reach the sites their manifest names and
bring Python packages of their own; their frontend may be TypeScript. Daily local schedules keep
their wall-clock time across daylight-saving changes.
Their tools reach AI apps like Synapse's own; restart the AI app if they do not show up.
