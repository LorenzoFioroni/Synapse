# Architecture

Synapse is a local reference manager. It has a Python backend (Flask, SQLite) and a browser
frontend. Everything runs on your own computer, and the app works fully offline. Network access
is used only for features that need the web by nature, such as metadata lookup, finding PDFs and
fetching reference lists.

This page is the map. Each subsystem gets its own page under `docs/` (see
[Subsystem pages](#subsystem-pages)), and the [conventions](#conventions) say where new code goes.

> **Status:** skeleton, written at the start of the maintainability work (#51). Sections marked
> *TODO* are filled in as each area is restructured.

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
              │ data/  library.db · fulltext.db · notes/ · notes_index.db ·     │
              │        citations.db · pdfs/ · backups/ · settings.json · auth.db│
              └─────────────────────────────────────────────────────────────────┘
```

| Entry point | What it is |
|---|---|
| `server.py` | Starts the web app: parses the flags, builds a `Config`, calls `backend.create_app`, and serves with Flask (local) or waitress (`--lan`). |
| `mcp_server.py` | The MCP server that Claude Desktop and Claude Code start themselves. It reads the same `data/` folder and writes notes only into `notes/ai/`. |
| `frontend/index.html` | The single-page app. It loads `js/main.js` as an ES module. |
| `frontend/viewer.html` | The PDF viewer, opened in its own tab. It loads pdf.js and `js/viewer/viewer.js` (the viewer), then its add-ons `annotations`, `links` and `notesBridge`. |
| `frontend/pair.html` | The page that unpaired devices see under `--lan`. |
| `browser_extension/` | The Chrome extension that saves the page you are on to the library. |

## Backend

Two objects tie the backend together:

- **`app.py`** has `create_app(config)`, the Flask factory. It installs the guard, registers the
  API blueprints and `frontend.py` (which serves the pages and static files), and starts the
  background work (the full-text indexer, the backup scheduler and the cleanup of the attachment
  trash). `Services` is stored as `app.extensions["synapse"]`.
- **`services.py`** has `Services`, the one object that owns every component (repositories,
  indexes, the notes vault and service, backups, devices, the change feed) and wires their
  callbacks together. The API reaches it through `api.svc()`.

| Package / module | Responsibility | Storage |
|---|---|---|
| `library/` | Items, attachments, folders, tags, smart folders, PDF annotations, citation keys and search. `fields.py` is the single source of truth for item fields and types, and `identifiers.py` for DOIs and arXiv ids (cleaning, normalising, matching). | `library.db` (schema and migrations in `library/db.py`) |
| `notes/` | Markdown notes. The files are the source of truth; the index is disposable. | `notes/`, `notes_index.db` |
| `formats/` | Import and export for BibTeX, RIS, EndNote and CSL-JSON, behind one registry. | none |
| `api/` | One Flask blueprint per area. Endpoints only parse requests and shape responses. | none |
| `mcp/` | The MCP server: the JSON-RPC layer (`protocol.py`), the library tools (`tools/`, one mixin per family), their schemas (`server.py`) and the instructions given to the AI app (`instructions.py`). | none (it uses the other packages) |
| `pdf/` | PDF files on disk: text extraction (`text.py`), the full-text index (`fulltext.py`), and for the MCP tools page rendering (`render.py`), finding figures and equations (`regions.py`) and PNG encoding (`png.py`). | `fulltext.db` |
| `online/` | Lookups on the web: item metadata (`metadata.py`), open-access PDFs (`pdf_finder.py`) and PDFs behind an attachment's web link (`remote_pdf.py`). `http.py` has what they share: timeout, User-Agent, the error type and the arXiv request spacing. | none |
| `references/` | The citation graph: reference lists fetched from Semantic Scholar, OpenAlex or Crossref (`sources.py`), stored (`store.py`), fetched in the background and matched to library items (`service.py`), and the Graph view's network (`graph.py`). | `citations.db` |
| `access/` | Who may use the server: other websites never (`guard.py`), other devices only once paired (`devices.py`); `lan.py` finds this computer's network addresses. | `auth.db` |
| `config.py`, `settings.py` | Paths of the data folder, and the user's preferences. | `settings.json` |
| `changes.py` | The change feed that tells other windows and devices what changed. | none |
| `backup.py` | Automatic backups of the library, the settings and the notes. | `backups/` |

Only `online/` and `references/` use the network.

### Layers

Packages depend on each other in one direction only. A package may import from those below it,
never from those above:

```
  app · frontend · api · services        composition: wires and exposes everything
  mcp                                    (library, notes, pdf, references)
  references                             (library, formats, online)
  online                                 (library, formats, pdf)
  formats · notes · pdf                  (library)
  library · backup · access              (base only)
  config · settings · changes            base: no backend imports
```

`tests/test_layers.py` checks this. When a change needs an import against the layers, the code
usually belongs in a lower package (shared helpers moved to `library/identifiers.py` and
`online/http.py` for this reason). If the layering itself must change, update the diagram above
and `ALLOWED` in the test together.

Components that need something from a higher layer get it passed in. For example, `Services`
gives `NotesService` the paper repository, and `backup.Backups` receives the services object.

## Frontend

*In progress (step 3): the code is TypeScript now. Files are being typed one by one: a file that
still starts with `// @ts-nocheck` is not type-checked yet.*

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
| `npm run check` | Type-checks with `tsc` (`tsconfig.json`, strict mode). CI runs it. |

The JavaScript unit tests (`tests/js/*.test.mjs`) run on the built `js/`; pytest builds it first.

### Modules

- `main.ts` is the entry point. It loads the schema and settings, builds the views and wires the
  toolbar.
- `store.ts` holds the application state, with a small pub/sub that views subscribe to.
- `actions.ts` has the user-level operations. Views call these, and these call `api.ts` (the thin
  JSON client) and update the store.
- `changes.ts` polls `/api/changes` and refreshes whatever another window, device or background
  job changed.
- `views/*` holds the screens and dialogs, and `views/notes/*` the notes workspace (a CodeMirror 6
  editor, live preview and Markdown rendering).
- `ui/*` holds the generic widgets (dialogs, menus, toasts, long press).
- `viewer/*` holds the PDF viewer (`viewer.html`): `viewer.ts` lays out, draws and zooms the pages
  and publishes `window.zcViewer`; `annotations.ts`, `links.ts` and `notesBridge.ts` add to it
  through the `zc:pagecreated` and `zc:pagerendered` events. The annotations are split three ways:
  `annotations/model.ts` (the records, drawing them, saving each change, undo and redo),
  `annotations/input.ts` (the tools: pointer, Pencil and touch input) and the entry
  `annotations.ts` (a layer on each page, loading, and changes made elsewhere).
- `vendor/` holds third-party libraries, committed as built files and loaded from disk. See
  [`frontend/vendor/README.md`](../frontend/vendor/README.md).

## Data flow of a typical change

*TODO (step 4)*: follow one edit from the item pane through `actions` → `api` → blueprint →
repository → `library.db`, and back to the other windows through the change feed.

## Subsystem pages

To be written in step 4 (one page each):

- `storage.md`: the databases, the schema and migrations, and what can be rebuilt.
- `notes.md`: the vault, the index, the parser, rekeying, and history of AI edits.
- `fulltext-search.md`: PDF text extraction, the FTS index and the search syntax.
- `references.md`: metadata lookup, the citation graph and PDF finding.
- `sync-and-devices.md`: the change feed, the guard, LAN pairing and the security model.
- `mcp.md`: the MCP server and its tools.
- `browser-extension.md`: the connector API and the extension.
- `frontend.md`: the module map, the state and actions contract, and the build.
- `testing.md`: unit tests, JS tests and the browser checklists.

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
