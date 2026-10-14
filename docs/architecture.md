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
| `frontend/viewer.html` | The PDF viewer, opened in its own tab. It uses pdf.js plus `js/viewer/*`. |
| `frontend/pair.html` | The page that unpaired devices see under `--lan`. |
| `browser_extension/` | The Chrome extension that saves the page you are on to the library. |

## Backend

*TODO (step 2): update after the package regrouping.*

- **`app.py`** has `create_app(config)`, the Flask factory. It installs the guard, registers the
  blueprints, serves the frontend, and starts the background work (the full-text indexer and the
  backup scheduler, the cleanup of the attachment trash).
- **`services.py`** has `Services`, the one object that owns every component (repositories,
  indexes, the notes vault and service, backups, devices, the change feed) and wires their
  callbacks together. The API reaches it through `api.svc()`.
- **`api/`** has one Flask blueprint per area. Endpoints only parse requests and shape responses.
  Business logic lives outside this package.
- **Storage.** Four databases make up the storage layer:
  - `db.py` manages `library.db` and its schema migrations. `repository.py`, `organize.py` and
    `annotations.py` give access to its data.
  - `fulltext.py` (`fulltext.db`), `notes/index.py` (`notes_index.db`) and `references.py`
    (`citations.db`) each keep their own database, and each of these can be rebuilt or re-fetched.
- **Notes.** In `notes/`, the Markdown files in `data/notes/` are the source of truth, and the
  index is disposable.
- **Formats.** `formats/` holds import and export for BibTeX, RIS, EndNote and CSL-JSON, behind
  one registry.
- **Online lookups.** `metadata.py`, `pdf_finder.py`, `remote_pdf.py` and `references.py` are the
  only modules that use the network.
- **Cross-cutting concerns:**
  - `guard.py` decides who may use the server.
  - `changes.py` tells other windows and devices what changed.
  - `backup.py` handles backups.
  - `settings.py` stores the preferences.
  - `fields.py` is the single source of truth for item fields and types.

## Frontend

*TODO (step 3): update after the TypeScript migration and the module splits.*

- `js/main.js` is the entry point. It loads the schema and settings, builds the views and wires
  the toolbar.
- `js/store.js` holds the application state, with a small pub/sub that views subscribe to.
- `js/actions.js` has the user-level operations. Views call these, and these call `js/api.js`
  (the thin JSON client) and update the store.
- `js/changes.js` polls `/api/changes` and refreshes whatever another window, device or
  background job changed.
- `js/views/*` holds the screens and dialogs, and `js/views/notes/*` the notes workspace (a
  CodeMirror 6 editor, live preview and Markdown rendering).
- `js/ui/*` holds the generic widgets (dialogs, menus, toasts, long press).
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
- **Where code goes.** *TODO (step 2)*: the package layout and the rule for each package.
