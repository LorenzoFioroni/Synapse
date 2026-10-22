# Architecture

Synapse is a local reference manager. It has a Python backend (Flask, SQLite) and a browser
frontend. Everything runs on your own computer, and the app works fully offline. Network access
is used only for features that need the web by nature, such as metadata lookup, finding PDFs and
fetching reference lists.

This page is the map. Each subsystem gets its own page under `docs/` (see
[Subsystem pages](#subsystem-pages)), and the [conventions](#conventions) say where new code goes.

## The pieces

```
              ┌──────────────────────── your computer ──────────────────────────┐
 browser ───► │ server.py ─► backend.create_app()                               │
 (app, PDF    │                 ├─ guard        who may talk to the server      │
  viewer)     │                 ├─ api/*        JSON endpoints (Flask blueprints)│
 iPad (LAN) ─►│                 └─ Services     wires all components together    │
              │                                                                 │
 browser   ──►│ api/connector   "Save to Library" button                        │
 extension    │                                                                 │
 Claude    ──►│ mcp_server.py ─► backend.mcp   same data/, over stdin/stdout    │
 Desktop/Code │                                                                 │
 Claude Code ►│ /mcp (token) ─► backend.mcp   the web app's own Services        │
 (other PC)   │                                                                 │
              │ data/  library.db · fulltext.db · notes/ · notes_index.db ·     │
              │        citations.db · pdfs/ · backups/ · settings.json · auth.db│
              │        plugins/ · plugin-data/ · plugins.json · fields.db       │
              └─────────────────────────────────────────────────────────────────┘
```

| Entry point | What it is |
|---|---|
| `server.py` | Starts the web app: parses the flags, builds a `Config`, calls `backend.create_app`, and serves it with Werkzeug's threaded server, over HTTPS with `--cert`/`--key`. `--window` opens it in a window of its own, `--quit-when-closed` stops it with its last window and `--make-shortcut` makes a desktop icon that does both (see [the app window](desktop.md)). |
| `mcp_server.py` | The MCP server that Claude Desktop and Claude Code start themselves. It reads the same `data/` folder. New notes it writes go into `notes/ai/`; changes it makes to existing notes are hash-checked and recorded so they can be undone (see [MCP](mcp.md)). |
| `frontend/index.html` | The single-page app. It loads `js/main.js` as an ES module. |
| `frontend/viewer.html` | The PDF viewer, opened in one of the app's own tabs (a frame in `index.html`) or in a browser tab of its own. It loads pdf.js and `js/viewer/viewer.js` (the viewer), then its add-ons `notesBridge`, `links` and `annotations`. |
| `frontend/pair.html` | The page the pairing QR code opens on another device. |
| `frontend/signin.html` | What a device that is not signed in sees instead of any page: how to pair, and **Sign In with a Passkey**. |
| `/mcp` | The MCP tools over HTTP, served by the web app, for an AI app on another computer with an access token (`api/mcp.py`, `mcp/http.py`). |
| `browser_extension/` | The Chrome extension that saves the page you are on to the library. It is plain JavaScript (ES modules) that Chrome loads as it is, with no build; JSDoc comments type it and `npm run check` in `frontend/` type-checks it with `browser_extension/tsconfig.json`. |

## Backend

Two objects tie the backend together:

- **`app.py`** has `create_app(config)`, the Flask factory. It installs the guard, registers the
  API blueprints and `frontend.py` (which serves the pages and static files), and starts the
  background work (the full-text indexer, the backup scheduler and the cleanup of the attachment
  trash). `Services` is stored as `app.extensions["synapse"]`.
- **`services.py`** has `Services`, the one object that owns every component (repositories,
  indexes, the notes vault and service, backups, devices, the change feed) and wires their
  callbacks together. The API reaches it through `api.svc()`.
- **`connector.py`** saves what the browser extension sends (see
  [browser extension](browser-extension.md)). Like `services.py` it combines several packages
  (the library, online lookups, PDF text, the citation queue), so it sits in the composition layer.

| Package / module | Responsibility | Storage |
|---|---|---|
| `library/` | Items, attachments, folders, tags, smart folders, PDF annotations, citation keys and search. `fields.py` is the single source of truth for item fields and types, and `identifiers.py` for DOIs and arXiv ids (cleaning, normalising, matching). | `library.db` (schema and migrations in `library/db.py`) |
| `notes/` | Markdown notes. The files are the source of truth; the index is disposable. | `notes/`, `notes_index.db` |
| `tasks/` | Tasks (to-dos with a body and sub-tasks) and the `{{task:id}}` lines that show them in notes (see [tasks](tasks.md)). | `library.db` (`tasks`, `task_history`) |
| `formats/` | Import and export for BibTeX, RIS, EndNote and CSL-JSON, behind one registry. | none |
| `api/` | One Flask blueprint per area. Endpoints only parse requests and shape responses. | none |
| `mcp/` | The MCP server: the JSON-RPC layer (`protocol.py`), its HTTP transport (`http.py`), the library tools (`tools/`, one mixin per family), their schemas (`server.py`) and the instructions given to the AI app (`instructions.py`). | none (it uses the other packages) |
| `pdf/` | PDF files on disk: text extraction (`text.py`), the full-text index (`fulltext.py`), and for the MCP tools page rendering (`render.py`), finding figures and equations (`regions.py`) and PNG encoding (`png.py`). | `fulltext.db` |
| `online/` | Lookups on the web: item metadata (`metadata.py`), open-access PDFs (`pdf_finder.py`) and PDFs behind an attachment's web link (`remote_pdf.py`). `http.py` has what they share: timeout, User-Agent, the error type and the arXiv request spacing. | none |
| `references/` | The citation graph: reference lists fetched from Semantic Scholar, OpenAlex, Crossref or arXiv's HTML version (`sources.py`, `arxiv_html.py`), stored (`store.py`), fetched in the background and matched to library items (`service.py`), the Graph view's network (`graph.py`), and how a paper outside the library connects to it (`connections.py`). | `citations.db` |
| `access/` | Who may use the server: other websites never (`guard.py`), other devices only once paired (`devices.py`) or signed in with a passkey (`passkeys.py`, checked by `webauthn.py`), AI apps at `/mcp` only with an access token (`tokens.py`); `authdb.py` is what the `auth.db` tables share; `lan.py` finds this computer's network addresses and names. | `auth.db` |
| `config.py`, `settings.py` | Paths of the data folder, and the user's preferences. | `settings.json` |
| `changes.py` | The change feed that tells other windows and devices what changed. | none |
| `backup.py` | Automatic backups of the library, the settings and the notes. | `backups/` |
| `assist/` | Autocompletion in notes from a model in Ollama on this computer: the client, the prompt, and checking that Ollama and the model are there (see [autocompletion](autocompletion.md)). | none |
| `plugins/` | The plugin host: finds the plugins in `data/plugins/` (and those that come with Synapse in `plugins/`), starts and stops them, gives them the `Plugin` object (routes, tools for Claude, events, settings, storage, reading the library, per-item fields, lists of papers outside the library, Synapse's lookups and adding papers), and turns off one that keeps failing (see [plugins](plugins.md)). `backend.plugins` is also the only module plugins import. | `plugins.json`, `plugin-data/`, `fields.db` |
| `desktop/` | Running Synapse like a desktop program: opening its window (`window.py`), the open windows that keep a `--quit-when-closed` server running (`presence.py`), the desktop shortcut (`shortcut.py`). See [the app window](desktop.md). | none |

Only `online/` and `references/` use the network. `assist/` talks only to Ollama on this computer
(its address must be a loopback one).

### Layers

Packages depend on each other in one direction only. A package may import from those below it,
never from those above:

```
  app · frontend · api                   composition: wires and exposes everything
  services · connector
  mcp                                    (library, notes, pdf, references, tasks)
  plugins                                (library, online, references)
  references                             (library, formats, online)
  tasks                                  (library, notes)
  online                                 (library, formats, pdf)
  formats · notes · pdf                  (library)
  library · backup · access · assist     (base only)
  config · settings · changes · desktop   base: no backend imports
```

`tests/test_layers.py` checks this. When a change needs an import against the layers, the code
usually belongs in a lower package (shared helpers moved to `library/identifiers.py` and
`online/http.py` for this reason). If the layering itself must change, update the diagram above
and `ALLOWED` in the test together.

Components that need something from a higher layer get it passed in. For example, `Services`
gives `NotesService` the paper repository, and `backup.Backups` receives the services object.

## Frontend

### Build

The sources are TypeScript in `frontend/src/`. `frontend/build.mjs` compiles them with esbuild
to `frontend/js/`, one output file per source file. That means the browser loads the same module
graph as the sources (including lazy `import()`s), relative paths into `vendor/` stay valid, and
source maps let the browser's debugger show the TypeScript. The pages load `js/main.js` and
`js/viewer/*.js`.

`frontend/js/` is generated and not committed. `server.py` rebuilds it when it is older than
the sources (`backend/frontend.py`, `ensure_built`), so running the app only needs Node.js and,
once, `npm ci` in `frontend/`.

| Command (in `frontend/`) | What it does |
|---|---|
| `npm ci` | Installs the build tools (esbuild, TypeScript), pinned in `package-lock.json`. |
| `npm run build` | Compiles `src/` to `js/`. |
| `npm run watch` | Rebuilds on every change. Reload the page to see it. |
| `npm run check` | Type-checks with `tsc` (`tsconfig.json`: strict mode, no unused locals or imports). CI runs it. |

The JavaScript unit tests (`tests/js/*.test.mjs`) run on the built `js/`; pytest builds it first.

### Modules

- `main.ts` is the entry point. It loads the schema and settings, builds the views and wires the
  toolbar.
- `store.ts` holds the application state, with a small pub/sub that views subscribe to.
- `actions.ts` has the user-level operations. Views call these, and these call `api.ts` (the thin
  JSON client) and update the store.
- `types.ts` describes what the JSON API answers; keep it in step with the backend when an endpoint
  changes. `globals.d.ts` types what the pages put on `window` and the custom events between
  modules. Everything is type-checked in strict mode; there is no `@ts-nocheck` left.
- `changes.ts` polls `/api/changes` and refreshes whatever another window, device or background
  job changed.
- `tabs/*` are the app's own tabs: the first tab (Library or Tasks), note tabs (VS Code-style
  preview tabs, shown by the notes workspace) and PDF tabs (a frame with the viewer each);
  `model.ts` without a DOM, `tabs.ts` the bar, the frames and dragging tabs between windows. See
  [the app window](desktop.md).
  Each window has its own tabs; `windows.ts` opens an item, a folder, a note, a task or a PDF in a
  new window. `presence.ts` tells a `--quit-when-closed` server that a window is open.
- `views/*` holds the screens and dialogs, and `views/notes/*` the notes workspace (a CodeMirror 6
  editor, live preview and Markdown rendering). In the workspace, `notesView.ts` sets things up and
  opens and closes notes; the parts share the open note through `workspace.ts` (its `hooks` are how
  they call back into `notesView.ts`). `tree.ts` draws the notes list and `treeInput.ts` handles
  its mouse, keyboard and drag and drop; `fileOps.ts` creates, renames, moves and trashes notes;
  `readView.ts` is read mode and link following; `sidePanel.ts` the status, breadcrumbs and
  details panel; `aiBar.ts` Claude's edits; `ghost.ts` and `assist.ts` autocompletion's ghost text and where it comes from; `conflict.ts` the dialog for a note changed elsewhere; `exportNote.ts`
  exports a note as PDF, HTML or Markdown (the Markdown conversion is `portable.ts`); `gallery.ts`
  and `galleryView.ts` read ` ```gallery ` blocks and turn their carousels; `figures.ts` numbers
  figures. `settingsView.ts` is the Settings page; its markup
  is in `views/settings/page.ts`, and Autocompletion, Backups and Devices have their own modules there.
  `views/tasks/*` is the Tasks page and tasks in notes (see [tasks](tasks.md)); it reaches into
  the notes workspace only through `taskHooks` in `views/notes/workspace.ts`.
  `itemlist.ts` is the items table (selection, keyboard, drag and drop, copying BibTeX); its
  header is `views/itemlist/columns.ts`, its virtualised rows `rows.ts` (only the rows near the
  viewport are in the DOM), its empty state `empty.ts`, and its context menus `menus.ts`. The
  item pane (`itempane.ts`) draws papers from a plugin's list that are not in the library, and
  why a plugin lists a paper, with `views/itempane/outside.ts`.
- `extend.ts` holds the extension points the views read: commands (the command palette,
  `views/palette.ts`), pages (`views/pages.ts`, listed in the sidebar), item-pane sections and item
  actions. `plugins/host.ts` loads plugins' frontends and registers what they add there; the types
  promised to plugins are in `plugins/api.ts` (see [plugins](plugins.md#the-frontend)).
- `ui/*` holds the generic widgets (dialogs, menus, toasts, long press).
- `viewer/*` holds the PDF viewer (`viewer.html`): `viewer.ts` lays out, draws and zooms the pages
  and publishes `window.zcViewer`; `notesBridge.ts`, `links.ts` and `annotations.ts` add to it
  through the `zc:pagecreated` and `zc:pagerendered` events. The annotations are split into
  `annotations/model.ts` (the records, drawing them, saving each change, undo and redo),
  `annotations/input.ts` (the drawing tools and the controls of a selected annotation),
  `annotations/colorMenu.ts` (choosing a colour),
  `annotations/textMarks.ts` (highlighting and underlining text) with its geometry in
  `annotations/lines.ts`, `annotations/pointer.ts` (mouse, Pencil or finger),
  `annotations/prefs.ts` (each tool's colour and size) and the entry `annotations.ts` (a layer
  on each page, loading, and changes made elsewhere). `inTab.ts` connects a viewer inside one of
  the app's tabs to the page around it, and `jumpHistory.ts` keeps its Back/Forward there.
- `vendor/` holds third-party libraries, committed as built files and loaded from disk. See
  [`frontend/vendor/README.md`](../frontend/vendor/README.md).

## Data flow of a typical change

Follow one edit: you change an item's title in the item pane and press Enter.

```
frontend                                   backend
views/itempane.ts   blur                    api/items.py   update_item()     parse, call, shape
  └ actions.updateField(id, 'title', v)       └ svc().papers.update(id, data)
      └ api.updateItem()  PATCH /api/items/<id>   library/repository.py        clean, citation key
          (X-Synapse-Client: <this page>)           └ Database.session()       library/db.py
      └ patchLocal(item)                               commit → on_write
          └ emit('item-updated')                         └ Services._library_written
              list row and pane repaint                     └ ChangeFeed.record({'library'}, client)

other windows and devices
changes.ts  every 4 s: GET /api/changes?since=N&epoch=E
  └ answer names 'library' (this page's own edits are left out)
      └ actions.loadItems() → the list, sidebar and pane redraw
```

1. **View.** `views/itempane.ts` saves a field when it loses focus. It calls
   `actions.updateField`. Most user-level operations go through `actions.ts` like this; a few
   views call `api.ts` directly and then ask `actions.refreshItems` to reload what changed.
2. **Action.** `actions.ts` sends the change with `api.updateItem` and puts the item the server
   returns into the store (`patchLocal`), then emits `item-updated`. The list row and the item
   pane listen for it and repaint only that item. The server's answer is the truth: it may have
   added a citation key or cleaned a value.
3. **API.** `api/items.py` (`update_item`) only reads the request and shapes the answer. Tags and
   folders go to their own components; the fields go to `PaperRepository.update`.
4. **Component.** `library/repository.py` keeps only editable fields, cleans the values, gives an
   item its first citation key once it has enough metadata, and writes inside
   `Database.session()`. A changed citation key is reported to listeners, so the notes service can
   rewrite notes that cite the old key.
5. **Change feed.** When a session commits a change, `Database.on_write` calls
   `Services._library_written`, which records a `library` change in `changes.py`, credited to the
   page that sent the request (its `X-Synapse-Client` header). Background work is credited to no
   one, so every page refreshes.
6. **Other windows.** Each page polls `GET /api/changes` (`changes.ts`). The answer lists what
   changed since the page last asked, minus its own edits, and the page reloads that part
   (`actions.loadItems` for `library`, the notes tree and open note for `notes`). It waits while
   you are typing in a field or a dialog is open. Writes from another process, such as the MCP
   server saving a note, are noticed from the database files' modification times.

Notes follow the same path with their own pieces: `views/notes/noteSession.ts` saves with the
note's hash, `api/notes.py` passes it to the notes service, and a mismatch comes back as a
conflict instead of overwriting (see [notes](notes.md)). The change feed is described in
[sync and devices](sync-and-devices.md).

## Subsystem pages

| Page | What it covers |
|---|---|
| [Adding a feature](adding-a-feature.md) | Where the pieces of a new feature go, in the order you write them. |
| [Storage](storage.md) | The data folder, who owns each file, the `library.db` schema and its migrations, settings and backups. |
| [Tasks](tasks.md) | Tasks: the records, the `{{task:id}}` lines in notes, the Tasks page and Claude's task tools. |
| [Notes](notes.md) | The notes format, the vault, the index, the parser and its frontend port, rekeying, Claude's edits, and the notes workspace. |
| [Full-text search](fulltext-search.md) | PDF text extraction, the full-text index and the library search with its query syntax. |
| [References](references.md) | Metadata lookup, finding PDFs, the citation graph, and what the Adding Items settings trigger. |
| [Sync and devices](sync-and-devices.md) | The change feed, the viewer and app tabs, the guard, LAN pairing and the security model. |
| [The app window](desktop.md) | `--window`, the server that stops with its window, the desktop shortcut, and the app's own tabs for PDFs. |
| [MCP](mcp.md) | The MCP server, its tools and the rules it enforces. |
| [Plugins](plugins.md) | Using and writing plugins: the manifest, the `plugin` object, events, storage, and how the host runs them. |
| [Autocompletion](autocompletion.md) | Ghost-text suggestions in notes from a local model (Ollama), and the checks that guard against a missing Ollama or model. |
| [Browser extension](browser-extension.md) | The extension and the connector API it uses. |
| [Frontend](frontend.md) | The build, the type checking, the pages, state and events, and the module map. |
| [Testing](testing.md) | pytest, the JavaScript tests, the browser checklists, CI and the tools. |

## Conventions

- **Offline first.** The app must run without a network. A library is welcome only if it works
  offline once it is installed (Python) or vendored (frontend). Nothing may load from a CDN, and
  `tests/test_offline.py` checks this.
- **Docs go with code.** A change that alters behaviour or structure updates the matching page in
  `docs/` in the same commit.
- **Where code goes.**
  - Put code in the package whose responsibility it is (see the table above), at the lowest layer
    that fits.
  - HTTP endpoints go in `api/` and only parse the request, call a component through `svc()`, and
    shape the response.
  - A new component is created and wired in `Services`.
  - Anything that talks to the web goes in `online/` (or `references/sources.py`), using
    `online/http.py`.
  - A module that grows a second responsibility is split. As a guide, a module past roughly 400
    lines usually has one.
- **Names.** Helpers used only inside their own module start with `_`. Anything imported by
  another module has a public name.
- **Errors.** Components raise their own exception types (`LookupFailed`, `AnnotationError`,
  `VaultError` and so on) with a message that can be shown to the user. The API turns them into
  JSON errors. When one exception is turned into another, chain it with `raise ... from e`.
