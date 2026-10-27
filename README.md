# Synapse

A local, web-based reference manager: a Flask backend with SQLite storage and a
TypeScript frontend. Everything runs on your machine.

## ✨ Your research, connected

- 🏠 **Local first, offline ready** — keep your library, PDFs and Markdown notes on your own machine.
- 📚 **Build your library in seconds** — add papers by DOI, arXiv ID or ISBN, drop in PDFs,
  or save articles with the browser extension.
- 🔎 **Find the passage you remember** — search metadata and PDF full text, then organise
  your collection with folders, coloured tags and smart folders.
- 🖍️ **Read, highlight, capture** — annotate PDFs with highlights, drawings and comments,
  and send quotes or snapshots straight into your notes.
- 📝 **Write notes that connect** — live Markdown preview, LaTeX maths, diagrams, images,
  paper citations and links between notes.
- 🕸️ **See the bigger picture** — explore a graph of your notes, papers and citation links.
- ✅ **Turn ideas into action** — track tasks, due dates and subtasks right alongside your notes.
- 🤖 **Bring your AI tools** — connect Claude, Codex and other AI apps through MCP;
  optionally complete notes with a local Ollama model.
- 🔄 **Move references freely** — import and export BibTeX, RIS, EndNote and CSL JSON,
  with customisable citation keys.
- 🧩 **Make it yours** — extend Synapse with plugins, including arXiv feeds, and choose
  light or dark mode.
- 📱 **Take research around the house** — use the responsive interface on a paired phone
  or tablet over your local network.
- 🛟 **Keep a way back** — automatic library and notes backups, note version history,
  and Trash with Undo.

Explore the [full feature list](#features) below, or [get started](#run-it).

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
python server.py --window         # in a window of its own, like a program
python server.py --make-shortcut  # an icon on the Desktop that does that (see below)
```

`server.py` compiles the web app's scripts (`frontend/src` → `frontend/js`) whenever they changed,
for example after a `git pull`.

The server only answers to `127.0.0.1` / `localhost`, and only the app's own pages and the
browser button can change the library: other websites open in your browser cannot.

### As a program of its own

`python server.py --window` opens Synapse in a window of its own instead of a tab of your
browser: Synapse installed as an app by Chrome or Edge if you did that (it then has the Synapse
icon in the Dock: open `http://127.0.0.1:8000` in Chrome, **⋮ → Cast, Save and Share → Install
Page as App…**), else a Chrome/Edge/Brave window without tabs or address bar. Right-click the
installed app's icon in the Dock for **New Window**.

`python server.py --make-shortcut` (macOS and Linux) puts a **Synapse** icon on the Desktop and,
on a Mac, in `~/Applications` (Spotlight, Launchpad, the Dock). Double-clicking it starts the
server in the background and opens the window; closing the window stops the server. Options given
with it (`--data-dir`, `--port`…) are used by the shortcut. Make it again after moving the folder.
Details: [docs/desktop.md](docs/desktop.md).

Notes and PDFs open in tabs inside Synapse (in a browser tab too), after the first tab, which shows
the library or Tasks. Notes open as in VS Code: in a preview tab (italic title) that the next note
replaces, until you double-click the tab, double-click the note in the notes list, or edit it. Each
note tab is its own editor, with its own mode (Live, Source, Read), place and undo history, so
going back to it is instant; when several notes are selected quickly, the last click always wins.
PDFs get a tab each. Ctrl+Tab switches, ⌃W (Alt+W elsewhere) or a
middle click closes, ⌃⇧T reopens. Each window has its own tabs, which a reload brings back; a new
window, or the app started again, opens on the library alone. Drag a tab onto another Synapse
window to move it there, or outside every window for a new one (or right-click it: **Move to New
Window**); right-click items, PDFs, folders, notes and tasks for **Open in New Window**. Settings →
PDF Viewer → Open PDFs in → Browser Tabs keeps PDFs in browser tabs of their own.

Give a notes folder a colour (right-click it in the notes list → Colour): its notes show it on
their tab and in the app window's title bar, which is otherwise neutral like the tab bar.

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

The connection is plain HTTP, so use it on a network you trust (your home Wi-Fi), or serve it
over HTTPS (below). Paired devices are kept in `data/auth.db` (hashed), which backups leave out on
purpose.

#### Signing in with a passkey

Pairing needs the computer at hand. With a passkey, a device signs itself in: on the "This device
isn't paired" page, tap **Sign In with a Passkey**. A passkey saved in iCloud Keychain or Google
Password Manager reaches your other devices by itself; a security key or a phone nearby works too.

Browsers only use passkeys on HTTPS pages opened by a name (not an IP address), so Synapse needs a
certificate your devices trust. [mkcert](https://github.com/FiloSottile/mkcert) makes one, offline:

```bash
mkcert -install                                   # once: a local certificate authority on this computer
mkcert Lorenzos-MacBook.local                     # your computer's .local name (Synapse prints it)
python server.py --lan --cert Lorenzos-MacBook.local.pem --key Lorenzos-MacBook.local-key.pem
```

With `--lan` and HTTPS, Synapse opens its browser tab or `--window` at that computer's `.local`
name rather than at an IP address, so the address matches the certificate. The certificate must
name the `.local` address Synapse prints; if the Mac's sharing name changes, make a new certificate
for the new name.

Then install mkcert's authority on each device: `mkcert -CAROOT` shows the folder; send
`rootCA.pem` to the iPad (AirDrop or mail), install the profile in Settings → General → VPN &
Device Management, and turn it on in Settings → General → About → Certificate Trust Settings. On
Android: Settings → Security → Encryption & credentials → Install a certificate → CA certificate.

Open `https://Lorenzos-MacBook.local:8000/` (on the computer too), go to **Settings → Devices →
Add a Passkey…** and save it. A device already paired can add one as well. Passkeys work only at
the address they were made on. Remove one in Settings → Devices on the computer; devices it signed
in stay until you remove them there too. Passkeys need the `cryptography` package (in
`requirements.txt`).

#### Installing on Android

Open the trusted HTTPS `.local` address above in Chrome, then choose **⋮ → Add to Home screen →
Install app**. Synapse opens in its own window and uses its red Synapse icon. Chrome deliberately
does not offer an app installation from a plain `http://` LAN address or from HTTPS whose
certificate the phone does not trust; in that case it only offers a basic browser shortcut. If an
old iconless shortcut already exists, remove it and add or install Synapse again after opening the
HTTPS address.

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
| `auth.db` | devices paired with `--lan` or signed in with a passkey, the passkeys, and AI apps' access tokens; not in backups |

Back up the `data/` folder to keep everything. It is git-ignored: code and data
never mix.

## Features

- **Clear startup**: while settings and the library are being prepared, a branded screen says
  what Synapse is loading instead of leaving an empty app shell on screen.

- **Responsive on phones and tablets**: compact, touch-sized navigation keeps the library,
  item details, notes, tasks and Settings usable one pane at a time on narrow screens.

- **Notification center**: a bell beside Settings keeps unread notices and long-running jobs,
  with a red count on the bell and browser tab icon. Info, Success, Warning and Error are separate
  from optional OS delivery: arXiv Today's ready notice can reach the OS after you grant permission
  under **Settings → Notifications**, where OS delivery can also be disabled again.
  Job notifications whose window crashed or restarted become interrupted and can be dismissed;
  browsers without automatic owner detection offer manual dismissal for previous windows' jobs.
  History stays in this browser. See [notifications](docs/notifications.md).

- **Search** in three scopes, picked from the search box (the default is a setting):
  *Title, Abstract & Authors* · *All Fields* · *All Fields & Full Text*.
  Terms are ANDed, `"exact phrase"` and `-exclude` work, and accents are ignored
  (`banuls` finds *Bañuls*). Full-text hits show a highlighted snippet.
- **Full-text index** of attached PDFs, kept in its own database so the library
  stays small. New attachments are indexed in the background; progress is shown
  in the sidebar and Settings, which can also rebuild it.
- **Import / export**: BibTeX, RIS, EndNote (tagged `.enw` and XML) and CSL JSON. Library export
  writers are the **Export Formats** core plugin; turning it off preserves imports and clipboard
  citation copying.
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
  with a section per article, word-level diffs that wrap long text with a checkbox per field, and the
  article's unchanged metadata below. Nothing is saved until you apply; replacing an
  existing citation key is opt-in. Several lookups run at once (arXiv requests are spaced out),
  and they can be stopped, or continue in the background with a progress notification and a
  *Review* button when done. Articles with updates are listed first, those not found next, and
  the ones already up to date are folded away at the end.
  OpenAlex author enrichment uses the names credited on the paper, with author-profile names
  as a fallback when those are missing.
- **Adding items** (Settings → Adding Items): whether to retrieve metadata for imported items, find
  an available PDF, fetch reference lists, and turn imported keywords into tags — each *Never*,
  *Always* or *Ask* (a ticked checkbox where you add the items: the import dialog, Add by
  Identifier, the browser button). They all run in the background.
- **References / citation graph** (online fetching is the **Reference Sources** core plugin;
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
  paper shows a card (paper note, Show in Library, PDF, Add to Library). It is a core plugin
  (**Graph**, also under **Plugins** in the sidebar): turn it off in Settings → Plugins if you don't use it.
- **Find Available PDF** (single or batch) looks for open-access copies (Unpaywall,
  arXiv: the core plugin Open-Access PDFs; other plugins can add places to look) and checks the file is really the right paper. Batch runs skip items
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
  [notes history](docs/notes-history.md).
- **Notes**: Markdown notes with live preview, KaTeX, code, Mermaid and images; `[@key]`
  citations and `[[links]]` between notes; a paper note per item. An item with several PDFs gets
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
  after a blank line the caption of the whole gallery (see [Galleries](docs/notes.md#galleries)).
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
  [Figures](docs/notes.md#figures-and-their-numbers)); galleries and figure numbers are part of the core
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
- **Extended Markdown** (core plugin, on by default) is where Markdown syntax beyond the basics
  lives, each part with its own switch in its settings: **galleries and numbered figures** (above),
  **task blocks** (` ```tasks table group=deadline completed=bottom ` with one task ID per line;
  their rich block can filter open/done tasks, sort them, and create a task in place),
  and **spoilers**: `||inline spoilers||` with a keyboard-accessible
  Show/Hide button, and collapsible `:::spoiler Optional summary` blocks closed by `:::`. Blocks
  support nested Markdown and spoilers. Source text stays intact when the plugin is off; HTML/PDF
  exports reveal spoiler contents. See [Extended Markdown](docs/notes.md#extended-markdown-core-plugin).
- **Tasks** (open it under **Plugins** in the left sidebar; a core plugin, on by default; turn it off in Settings → Plugins without losing tasks): to-dos with a title, a due date, details written in Markdown like a note, and
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
  restored. See [`docs/tasks.md`](docs/tasks.md).
- **Autocompletion** in notes (a core plugin, off by default: turn it on in Settings → Plugins): after a short pause a
  model running on this computer in [Ollama](https://ollama.com) suggests how the text goes on, in
  grey after the cursor. Tab accepts, Esc dismisses, Cmd/Ctrl+→ takes one word, Alt/⌥+\ asks for
  one now, clicking it accepts it. Only local models are supported: install Ollama and a model
  (`ollama pull qwen2.5-coder:1.5b`); Settings says what is missing. Without them nothing is sent
  and the notes say so once. See [docs/autocompletion.md](docs/autocompletion.md).
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
<plugin>" in the command palette) with its settings, its log and a Restart button; a plugin that keeps failing is turned off and Settings says why. If one keeps
the app from working, start it with `python server.py --safe-mode` (only core plugins run) and turn it
off.

Synapse’s own optional features are **core plugins**: Graph, Metadata Sources,
Reference Sources, Export Formats, Extended Markdown, Open-Access PDFs and Tasks start on,
while Autocompletion starts as previously configured.
Turn them off in Settings → Plugins without losing their data. Core plugins can also be toggled
and restarted in safe mode; changes are followed by the standalone MCP server. Safe mode keeps core plugins
running and stops third-party plugins. Tasks and Graph add their own entries to the sidebar's
list; their selection follows the visible workspace when switching between views. Plugins'
settings sections appear and disappear immediately as their plugin is switched on or off, including
while Settings stays open. Disabling Autocompletion dismisses visible
suggestions and cancels pending ones immediately.

The built-in metadata providers (Crossref, OpenAlex, arXiv and Open Library) are the
**Metadata Sources** core plugin, on by default. Switch it off in Settings → Plugins to stop
those lookups; all existing metadata stays in the library. Other plugins can supply sources too.

Three plugins come with Synapse, off until you turn them on:

- **Citation Counts** adds a *Citations* column from OpenAlex and Semantic Scholar, refreshed
  weekly, and lets you search for `citations:>100`.
- **Metadata Check** compares every identifiable library record with current metadata every
  three days (or as often as you set), flags fields to review, and identifies journal versions of
  arXiv preprints. **Run Now** checks everything at once, with progress in the notification center.
  **Review Changes** opens the metadata review dialog from stored results: apply selected fields,
  edit values, reject suggestions, or ignore a paper altogether. Rejected values stay hidden until
  they change; Settings lists rejected suggestions and ignored papers so you can restore them.
- **arXiv Feed** lists each morning's arXiv papers in the categories you choose, ranked by how
  they connect to your library (papers of yours they cite, shared authors, similar topics), with
  a notification ("12 new papers, 3 connected to your library"), a **+** to add a paper and
  Dismiss for the rest. It stays one entry in the main sidebar; opening it shows a second sidebar
  for choosing today or any retained day. Earlier days stay for a month, and Claude can ask for
  them.

A plugin can offer a **list of papers** that are not in your library yet (today's arXiv papers,
say), under **Plugins** in the sidebar. It shows like a folder, ranked by the plugin (often by how
each paper connects to your library: papers it cites or that cite it, shared authors, similar
topics); the item pane says why each one is listed. Click **+** on a row, or **Add to My
Library** in the item pane, to add a paper; right-click or select several to add them to a folder.

A plugin has a `manifest.json`, Python for the server (routes, tools for Claude, listeners for
changes in the library, settings and stored data, and per-item fields: values such as a citation
count that appear as list columns, search filters like `citations:>100` and rows in the item pane) and JavaScript for the app (pages listed under
**Plugins** in the sidebar, sections in the item pane, actions in the item menu, commands).
Start with the [plugin authoring guide](docs/plugin-authoring.md) and its runnable Hello Library
example. [Plugins](docs/plugins.md) is the API reference;
[`docs/examples/recently-added`](docs/examples/recently-added) shows a larger plugin to adapt
([`suggested-reading`](docs/examples/suggested-reading) offers a list of papers).
To write one, `python server.py --new-plugin my-plugin --dev-plugins ~/synapse-plugins` makes a
plugin to start from, and `python server.py --dev-plugins ~/synapse-plugins` restarts it whenever
you save a file. Plugins can also run work on a schedule, reach the sites their manifest names and
bring Python packages of their own; their frontend may be TypeScript. Daily local schedules keep
their wall-clock time across daylight-saving changes.
Their tools reach AI apps like Synapse's own; restart the AI app if they do not show up.

## AI access (MCP)

`mcp_server.py` lets AI apps — Claude Desktop, Claude Code, Codex, or any other app that speaks MCP
(Model Context Protocol) — read your library: ask a question about a project note and the AI can
read the note, the papers it cites,
the relevant pages of their PDFs and their references. It reads the same `data/` folder as the app
(which does not need to be running); from another computer, see below. When you ask it to save an
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
- **Codex** (CLI, IDE extension, app): run the printed `codex mcp add …` line, or add the printed
  `[mcp_servers.synapse]` entry to `~/.codex/config.toml`.
- **Other apps:** run the same command as a stdio MCP server.

Whatever an AI app changes is shown with its name: "Codex edited this note", "Claude changed this
task", and in each note's history, all with Undo.

**From another computer.** An AI app on another computer of your network reaches the library
through the running app instead (start it with `--lan`). On the computer running Synapse, open
**Settings → AI Access → New Token…**, name it, pick the app (Claude Code, Codex or another) and
run what it shows on the other computer. For Claude Code:

```bash
claude mcp add --scope user --transport http synapse http://Lorenzos-MacBook.local:8000/mcp \
  --header "Authorization: Bearer syn_…"
```

For Codex, keep the token in an environment variable (put the `export` line in your shell profile):

```bash
export SYNAPSE_TOKEN=syn_…
codex mcp add synapse --url http://Lorenzos-MacBook.local:8000/mcp --bearer-token-env-var SYNAPSE_TOKEN
```

The token is shown once; Settings lists when each token was last used, and **Remove…** locks its
app out at once. The tools are the same. Over plain HTTP the token crosses the network unencrypted,
like everything else in `--lan`; with `--cert` the address starts with `https://` (and the AI app
must trust the certificate, for Claude Code: `NODE_EXTRA_CA_CERTS="$(mkcert -CAROOT)/rootCA.pem"`).

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
| `edit_note` | changes an existing note (replace passages, append, or new text) — checked the same way; a version of its own in the note's history |
| `undo_note_edit` | takes the last AI edit back out of a note, keeping what you changed since (unless you changed the same lines) |
| `list_tasks`, `get_task` | your tasks (open ones unless asked), filtered like the Tasks page; one task with its details, sub-tasks and the notes that show it |
| `create_task`, `update_task`, `delete_task` | make, change (title, details, open/done, due date, parent) or delete tasks, and put a new task's line in a note — marked as the AI app's (with its name), undoable in the app |

Page numbers are PDF pages counted from 1, so the AI can cite `[@key, p. N]` and the citation opens
the right page in the app. The AI app is told the notes syntax (KaTeX math, quotes as `> words [@key, p. N]`,
snapshots, callouts…), and `write_note` refuses a note — saving nothing — when a `[[link]]` or `#heading`
does not resolve, a citation key or page does not exist, a quote's words are not on the cited page, an
image is missing, or formulas are written in Unicode instead of `$…$`; the AI then corrects it. A link
written with a note's title is fixed to its file name. `edit_note` is checked the same way, but only
problems the change adds block it. Every change an AI app makes to a note is a version of its own
in the [notes history](docs/notes-history.md): an open note takes the change as one step, so ⌘Z (Ctrl+Z)
undoes it; a bar over the note says which app changed what ("Codex edited this note"), with Show
changes and Undo; and the note's history lists each change with the app's name, its diff and a
way back. The diff highlights changed words and wraps long paragraphs to the dialog's width.
Equations are found by the number in
parentheses at the end of their line, and the snapshot covers all their lines (never half an
equation). `view_page` and `snapshot` need `pypdfium2`
(in `requirements.txt`). Note text and PDF passages the AI reads are sent to the AI app's provider
(Anthropic for Claude, OpenAI for Codex …) as part of the conversation, as if you had pasted them.

## Project layout

For how the code fits together and where new code goes, see
[`docs/architecture.md`](docs/architecture.md) (and [`CLAUDE.md`](CLAUDE.md) for the working rules).

```
./
├── server.py              starts the web app
├── mcp_server.py          MCP server for AI apps: Claude, Codex … (stdio)
├── backend/               Flask + SQLite backend (packages described in docs/architecture.md)
├── frontend/              the web app: index.html, viewer.html, src/ (TypeScript; built to js/), css/, vendor/ (third-party libraries)
├── browser_extension/     Chrome extension (Manifest V3) – load unpacked
├── tools/                 build scripts for the vendored libraries and the macOS icon
├── tests/                 pytest suite, js/ unit tests, e2e/ browser checklists
├── plugins/               plugins that come with Synapse, off until turned on (Citation Counts, arXiv Today)
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
python tests/e2e/tabs_checklist.py       # PDFs in the app's own tabs; a --quit-when-closed server stops with its window
python tests/e2e/export_checklist.py     # Export: HTML (self-contained, offline), Markdown, PDF via the print dialog
python tests/e2e/extensions_checklist.py # provider/export lifecycle, spoilers in Live/Read, static export
python tests/e2e/navigation_checklist.py # workspace switches, sidebar selection, history, shortcuts and disabled plugins
python tests/e2e/graph_checklist.py      # Graph: local graph, full graph, levels, filters, paper card, Show in Graph, Back/Forward
python tests/e2e/attachments_checklist.py  # note attachments: unused ones to the trash, Attachments dialog, retention
python tests/e2e/adding_checklist.py     # bulk Retrieve Metadata (parallel, grouped, background), Settings → Adding Items
python tests/e2e/claude_edits_checklist.py  # Claude's note edits: appear live, ⌘Z / bar Undo / Show changes, the note's history
python tests/e2e/notes_history_checklist.py # notes history: versions grouped, diffs, restore, outside changes, milestones
python tests/e2e/equations_checklist.py  # equation numbers, \eqref, macros, clicks into formulas, panels and width
python tests/e2e/extension_checklist.py  # the browser button: options, reading an article page, saving it
```

The notes editor (CodeMirror 6) is vendored as a pre-built bundle in `frontend/vendor/cm6/`;
to rebuild it from the pinned versions: `cd tools/cm6 && npm ci && ./build.sh`. The renderer
(markdown-it, KaTeX, Mermaid) likewise lives in `frontend/vendor/md/` and `frontend/vendor/mermaid/`
(`tools/md`), and the graph library (force-graph) in `frontend/vendor/force-graph/` (`tools/graph`).
