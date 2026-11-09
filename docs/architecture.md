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
 AI apps   ──►│ mcp_server.py ─► backend.mcp   same data/, over stdin/stdout    │
 Claude, Codex│                                                                 │
 AI app     ─►│ /mcp (token) ─► backend.mcp   the web app's own Services        │
 (other PC)   │                                                                 │
              │ data/  library.db · fulltext.db · notes/ · notes_index.db ·     │
              │        notes-history.git ·                                      │
              │        citations.db · pdfs/ · backups/ · settings.json · auth.db│
              │        plugins/ · plugin-data/ · plugins.json · fields.db       │
              └─────────────────────────────────────────────────────────────────┘
```

| Entry point | What it is |
|---|---|
| `server.py` | Starts the web app: parses the flags, builds a `Config`, calls `backend.create_app`, and serves it with Werkzeug's threaded server, over HTTPS with `--cert`/`--key`. `--container` is the mode the Docker image runs, behind a tunnel (see [Docker](docker.md)). `--window` opens it in a window of its own, `--quit-when-closed` stops it with its last window and `--make-shortcut` makes a desktop icon that does both (see [the app window](desktop.md)). |
| `mcp_server.py` | The MCP server that AI apps (Claude Desktop, Claude Code, Codex …) start themselves. It reads the same `data/` folder. New notes it writes go into `notes/ai/`; changes it makes to existing notes are hash-checked and recorded so they can be undone (see [MCP](mcp.md)). |
| `frontend/index.html` | The single-page app. It loads `js/main.js` as an ES module, which builds the desktop layout, or on a phone hands over to the phone layout (`phone/main.ts`, see [phone](phone.md)). |
| `frontend/viewer.html` | The PDF viewer, opened in one of the app's own tabs (a frame in `index.html`) or in a browser tab of its own. It loads pdf.js and `js/viewer/viewer.js` (the viewer), then its add-ons `notesBridge`, `links` and `annotations`. |
| `frontend/pair.html` | The page the pairing QR code opens on another device; its standalone script is `src/access/pair.ts`. |
| `frontend/signin.html` | What a device that is not signed in sees instead of any page: how to pair, and **Sign In with a Passkey**, driven by standalone `src/access/signin.ts`. |
| `/mcp` | The MCP tools over HTTP, served by the web app, for an AI app on another computer with an access token (`api/mcp.py`, `mcp/http.py`). |
| `browser_extension/` | The Chrome extension that saves the page you are on to the library, and fetches missing PDFs through the browser (Find Missing PDFs). It is plain JavaScript (ES modules) that Chrome loads as it is, with no build; JSDoc comments type it and `npm run check` in `frontend/` type-checks it with `browser_extension/tsconfig.json`. |

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
| `library/` | Items, attachments, folders, tags, smart folders, PDF annotations, citation keys and search. `fields.py` is the single source of truth for item fields and types, and `identifiers.py` for DOIs and arXiv ids (cleaning, normalising, matching). `agent.py` holds which AI app is making the current change (set by the MCP server, read by notes and tasks). | `library.db` (schema and migrations in `library/db.py`) |
| `notes/` | Markdown notes. The files are the source of truth; the index is disposable. `history/` keeps every version of their text in Git (see [notes history](notes-history.md)). | `notes/`, `notes_index.db`, `notes-history.git/` |
| `tasks/` | Task records, history and the integrity of `{{task:id}}` note links. The optional Tasks contributed plugin owns their UI, HTTP handlers and MCP tools (see [tasks](tasks.md)). | `library.db` (`tasks`, `task_history`) |
| `formats/` | Import parsers and shared codecs for BibTeX, RIS, EndNote and CSL-JSON. The Export Formats plugin registers their library-export writers; `exporting.py` prepares records for writers and core clipboard copying. | none |
| `api/` | One Flask blueprint per area. Endpoints only parse requests and shape responses. | none |
| `mcp/` | The MCP server: the JSON-RPC layer (`protocol.py`), its HTTP transport (`http.py`), the library tools (`tools/`, one mixin per family), their schemas (`server.py`) and the instructions given to the AI app (`instructions.py`). | none (it uses the other packages) |
| `pdf/` | PDF files on disk: text extraction (`text.py`), the full-text index (`fulltext.py`), and for the MCP tools page rendering (`render.py`), finding figures and equations (`regions.py`) and PNG encoding (`png.py`). | `fulltext.db` |
| `online/` | Lookups on the web: normalizing metadata queries (`metadata.py`; providers come from plugins), downloading and checking a PDF of a paper (`pdf_finder.py`; where to look comes from plugins' PDF sources, and an article's page leads to its PDF through `article_page.py`) and PDFs behind an attachment's web link (`remote_pdf.py`), cloud account authorization for remote backups (`backup_oauth.py`), and newer code for Synapse itself on GitHub, installed through the deployment's updater (`updates.py`, see [updates](updates.md)). `http.py` has what they share: timeout, User-Agent, the error type, the arXiv request spacing and the shared public-address validator and pinned HTTP/TLS transport. | none |
| `references/` | The citation graph: reference fetching through injected plugin providers (`sources.py`); scholarly HTTP and arXiv parsing live in `plugins/reference-sources/server/`, stored (`store.py`), fetched in the background and matched to library items (`service.py`), and how a paper outside the library connects to it (`connections.py`). | `citations.db` |
| `access/` | Who may use the server: other websites never (`guard.py`), other devices only once paired (`devices.py`) or signed in with a passkey (`passkeys.py`, checked by `webauthn.py`), AI apps at `/mcp` with MCP-scoped tokens and remote browser saves with revocable connector-scoped tokens (`tokens.py`); requests through a tunnel or proxy never count as this computer (`proxy.py`, `--public-url`); administrative actions from a device after a passkey confirmation (`confirm.py`); `--pairing-link` (`pairing_link.py`); `authdb.py` is what the `auth.db` tables share; `lan.py` finds this computer's network addresses and names. See [`remote-access.md`](remote-access.md). | `auth.db` |
| `config.py`, `settings.py` | Paths of the data folder, how the server was started (`--public-url`, `--container`, the local services of `--local-service`), and the user's preferences. | `settings.json` |
| `changes.py` | The change feed that tells other windows and devices what changed. | none |
| `backup/` | Local backup archives and restore (`__init__.py`, `restore.py`); Settings-managed rclone storage (`remote.py`, `rclone.py`), uploaded trust bundle validation (`certificates.py`), persistent upload retries (`delivery.py`). | `backups/`, `backup-remote/` |
| `plugins/` | Plugin discovery, lifecycle, public permissions and integration registries. `catalog.py` discovers manifests/defaults; `files.py` coordinates storage writes. See [Plugins](plugins.md) and the ownership table below. | `plugins.json`, `plugin-data/`, `fields.db` |
| `desktop/` | Running Synapse like a desktop program: opening its window (`window.py`), the open windows that keep a `--quit-when-closed` server running (`presence.py`), the desktop shortcut (`shortcut.py`). See [the app window](desktop.md). | none |

The per-instance live PDF rendezvous is `online/browser_pdfs.py`, owned by `Services.browser_pdfs`.
It connects app PDF actions to an already-listening browser extension through authenticated
connector endpoints. Offers expire after five seconds and are never persisted; it is separate
from server downloads and does not change the library schema.

### Bundled plugin ownership

Bundled plugins use the public API exported by `backend.plugins` and `frontend/src/plugins/api.ts`.
Core retains the library, notes, task records and validation contracts when a plugin is off.

| Plugin | Plugin owns | Core keeps or bridges |
| --- | --- | --- |
| Metadata Sources | Scholarly HTTP providers, parallel enrichment and record conversion (`server/providers.py`, `parsing.py`) | Query normalization; offline helpers in `backend.plugins.scholarly` |
| Reference Sources | Provider selection, record conversion, arXiv bibliography parsing, retries and stop signal | Citation storage/matching; named sources and legacy aliases in `backend/plugins/references.py` |
| Open-Access PDFs | Candidate sources | Guarded download, PDF identity checking and attachment persistence |
| Export Formats | Library export writers | Shared codecs and export preparation in `formats/` |
| Citation Counts | Batched provider lookup, refresh policy and field declarations | Per-item field storage/indexing and public HTTP transport |
| arXiv Today | Feed days, relevance scoring, news lists and notifications | Library access, outside-paper lists and shared HTTP spacing |
| Autocompletion | Ollama requests, suggestion lifecycle and Settings UI | Permission-checked loopback HTTP; editor suggestion hooks/context |
| Metadata Check | Comparison/rejection rules, review scheduling and progress | Permission-checked edits, review dialogs and job notifications |
| Extended Markdown | Figure/gallery, task-block and spoiler rendering/export extensions | Markdown/attachment integrity while disabled; shared renderer SDK and preview hooks |
| Graph | Graph model, server data builder, local/full views and navigation | Public notes/citation reads; visualization, sidebar and history bridges |
| Tasks | Task page, note widgets, HTTP handlers and MCP tools | Records/history and note-link integrity; permission checks and fixed compatibility aliases |

See [Plugins](plugins.md) for provider contracts, [Frontend](frontend.md) for the plugin bridge
modules, and [Tasks](tasks.md) for record ownership.

### Layers

Packages depend on each other in one direction only. A package may import from those below it,
never from those above:

```
  app · frontend · api                   composition: wires and exposes everything
  services · connector
  mcp                                    (library, notes, pdf, references, tasks)
  plugins                                (library, formats, online, references, notes, tasks)
  references                             (library, formats, online)
  tasks                                  (library, notes)
  online                                 (library, formats, pdf)
  formats · notes · pdf                  (library)
  library · backup · access              (base only)
  config · settings · changes · desktop   base: no backend imports
```

`tests/test_layers.py` checks this. When a change needs an import against the layers, the code
usually belongs in a lower package (shared helpers moved to `library/identifiers.py` and
`online/http.py` for this reason). If the layering itself must change, update the diagram above
and `ALLOWED` in the test together.

Components that need something from a higher layer get it passed in. For example, `Services`
gives `NotesService` the paper repository, and `backup.Backups` receives the services object. `Services` injects `online.backup_oauth.BackupOAuth`
into its remote storage: provider authorization stays in `online/`, while all file transfers use
the server's rclone process. Its credentials live outside archived preferences.

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

Phones get a layout of their own (`frontend/src/phone/`, see [the phone layout](phone.md)): a
tab bar, screens pushed on top, sheets, and plugin destinations in a Plugins tab. It shares the
store, the actions, the API client and the plugins with the desktop layout described next.

The desktop shell is responsive down to phone widths (a phone told to use it, a narrow window).
At 700px and below the toolbar wraps, secondary
actions move to its More menu, configured library columns remain available in the list's own
horizontal scroller, and item, note and task details use one full-width pane at a time. Settings
uses a horizontally scrolling section strip.
Tablet widths keep the richer split panes but move the collections sidebar into a drawer.

### Modules

- `main.ts` is the entry point. It loads the schema and settings, builds the views and wires
  navigation and shortcuts. `views/toolbar.ts` owns workspace actions and search;
  `views/statusbar.ts` shows library counts, note words/save state or open-task counts.
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
- `views/*` holds screens and dialogs. `itemlist.ts` coordinates the virtualized table, with
  columns, rows, empty states and menus under `views/itemlist/`; `itempane.ts` and
  `views/itempane/outside.ts` display library and plugin-listed papers.
- `views/notes/*` is the notes workspace. `notesView.ts` coordinates open notes and view lifecycle;
  `workspace.ts` shares state/hooks; `noteSession.ts` owns saving, drafts and conflicts. Tree,
  file operations, read mode, side panels, editor formatting and exports each have their own
  modules. See [Notes](notes.md) and the [frontend module map](frontend.md).
- `settingsView.ts` coordinates Settings, one section (page) at a time; `settings/page.ts` renders
  its grouped navigation and sections and `settings/search.ts` shows the matching rows of built-in
  and dynamic plugin sections without rebuilding controls.
  Backups, updates, devices, passkeys, AI Access, plugin lifecycle and export formats have modules under
  `views/settings/`. Notes preferences have their own section.
- `phone/*` is the phone layout: `layout.ts` decides which layout a page gets, `shell.ts` the tab
  bar and stacks of screens, `library.ts`, `notes.ts`, `reader.ts` and `plugins.ts` the screens,
  `ui.ts` shared pieces, `model.ts` the decisions without a DOM. See [phone](phone.md).
- `plugins/*` bridges public plugin contributions into the app. `host.ts` owns frontend lifecycle;
  suggestion, Markdown, graph/notes, task and progress bridges connect plugins to the existing
  views. Bundled plugin implementations live under top-level `plugins/`, as mapped above. See
  [Frontend](frontend.md) and [Plugin authoring](plugin-authoring.md) for individual modules.
- `extend.ts` holds the extension points the views read (also the phone's: a page's `phone`
  layout, `captures` for the + sheet, `editorButtons` above the keyboard, an action's `role`): workspace navigation (Tasks and the public Notes visualization bridge,
  registered and disposed with their plugins), commands (the command palette,
  `views/palette.ts`), pages (`views/pages.ts`, listed in the sidebar), item-pane sections and item
  actions. `plugins/host.ts` loads plugins' frontends and registers what they add there; the types
  promised to plugins are in `plugins/api.ts` (see [plugins](plugins.md#the-frontend)).
- `notifications/*` owns browser-local notification history, the bell/panel and job progress,
  with optional OS delivery and app/favicon badges (see [notifications](notifications.md)).
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
| [Features](features.md) | Daily library, notes, PDF and plugin workflows; command palette and sidebar. |
| [Adding a feature](adding-a-feature.md) | Where the pieces of a new feature go, in the order you write them. |
| [Storage](storage.md) | The data folder, who owns each file, the `library.db` schema and its migrations, settings and backups. |
| [Tasks](tasks.md) | Tasks: the records, the `{{task:id}}` lines in notes, the Tasks page and the task tools for AI apps. |
| [Notes](notes.md) | The notes format, the vault, the index, the parser and its frontend port, rekeying, Claude's edits, and the notes workspace. |
| [Notes history](notes-history.md) | Every version of the notes' text in Git: who made each change, checkpoints, milestones, restoring, backups. |
| [Full-text search](fulltext-search.md) | PDF text extraction, the full-text index and the library search with its query syntax. |
| [References](references.md) | Metadata lookup, finding PDFs, the citation graph, and what the Adding Items settings trigger. |
| [Sync and devices](sync-and-devices.md) | The change feed, the viewer and app tabs, the guard, LAN pairing and the security model. |
| [Remote access](remote-access.md) | Running Synapse behind an HTTPS tunnel (`--public-url`, Cloudflare Tunnel), `--pairing-link`, confirming administrative actions with a passkey. |
| [Updates](updates.md) | Settings → Updates: newer commits on the GitHub branch Synapse comes from, and installing them with the deployment's updater container. |
| [Docker](docker.md) | The image and `compose.yaml`: Synapse, its tunnel and Ollama in containers; `--container`, local services (`--local-service`) for Ollama in its own container, networks, two libraries on one server. |
| [The app window](desktop.md) | `--window`, the server that stops with its window, the desktop shortcut, and the app's own tabs for PDFs. |
| [MCP](mcp.md) | The MCP server, its tools and the rules it enforces. |
| [Plugins](plugins.md) | Using and writing plugins: the manifest, the `plugin` object, events, storage, and how the host runs them. |
| [Plugin authoring](plugin-authoring.md) | A runnable minimal plugin, interface choices, settings, storage, lifecycle cleanup and verification. |
| [Autocompletion](autocompletion.md) | Ghost-text suggestions in notes from a local model (Ollama), and the checks that guard against a missing Ollama or model. |
| [Browser extension](browser-extension.md) | The extension and the connector API it uses. |
| [Notifications](notifications.md) | The bell, unread history, progress, plugin delivery and OS opt-in. |
| [Frontend](frontend.md) | The build, the type checking, the pages, state and events, and the module map. |
| [Python dependencies](dependencies.md) | Runtime/development inputs, portable hashed locks, the pinned resolver, CI installation and dependency updates. |
| [Development](development.md) | One-time setup, targeted checks while editing, and final local verification with `tools/check.py`. |
| [Security checks](security-checks.md) | Secret/dependency scanners, CodeQL feedback and disposable container smoke checks. |
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
  - Anything that talks to the web goes in `online/` (using `online/http.py`) or a scholarly
    plugin (using `plugin.http` for contributed plugins).
  - A module that grows a second responsibility is split. As a guide, a module past roughly 400
    lines usually has one.
- **Names.** Helpers used only inside their own module start with `_`. Anything imported by
  another module has a public name.
- **Errors.** Components raise their own exception types (`LookupFailed`, `AnnotationError`,
  `VaultError` and so on) with a message that can be shown to the user. The API turns them into
  JSON errors with `api.failure(e)`, which shows only the one message an error was raised with
  (`api.message_of`; anything else, such as an `OSError`'s file name, answers a generic text). An
  answer can reach another device or an AI app, so a message never quotes the text of an unexpected
  exception: name its type, and leave the details to the log. When one exception is turned into
  another, chain it with `raise ... from e`.
