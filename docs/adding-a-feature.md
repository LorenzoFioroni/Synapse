# Adding a feature

This page walks through where the pieces of a typical feature go, in the order you usually write
them. [Architecture](architecture.md) has the map and the layering rules. The subsystem pages it
links to describe each area in depth.

## Before you start

- **Find the area.** The package table in [architecture](architecture.md#backend) says which
  package owns what. New logic goes in the lowest layer that fits; `tests/test_layers.py` fails if
  an import goes against the layers.
- **Check the contracts.** The `data/` layout, the database schemas, the notes format and the
  HTTP API used by the browser extension and the MCP server must keep working for existing users
  and clients. If your feature changes one of them, plan the migration first.
- **Offline first.** A new library must work offline once installed (Python) or be vendored
  under `frontend/vendor/` with its licence, version and build script listed in
  `frontend/vendor/README.md`. Nothing may load from a CDN.

## The backend

### 1. The component

Put the logic in a component class in its package, for example a method on `PaperRepository`
(`backend/library/repository.py`) or a new module in `backend/notes/`. A component:

- takes what it needs in its constructor (the database, the settings, other components), so it
  can be tested without Flask;
- raises its own exception type with a message that can be shown to the user (`NotFound`,
  `AnnotationError`, `VaultError`, `LookupFailed` …), chained with `raise … from e`;
- talks to the web only from `backend/online/` or `backend/references/sources.py`, through
  `backend/online/http.py` (timeout, User-Agent, contact e-mail).

If a module grows a second responsibility, split it rather than adding to it.

### 2. Wiring

A new component is created in `Services.__init__` (`backend/services.py`), which also connects
callbacks between components (for example, the paper repository tells the notes service when a
citation key changes). Background work started at start-up goes in `create_app`
(`backend/app.py`).

### 3. The endpoint

Add a route to the blueprint for the area in `backend/api/` (or a new blueprint module, added to
`register_blueprints` in `backend/api/__init__.py`). An endpoint only:

1. reads the request (`body()`, `request.args`, `ids_from_body()`),
2. calls a component through `svc()`,
3. returns JSON (`jsonify(...)`, or `error(message, status)`).

`NotFound` becomes a 404 everywhere (`backend/app.py`). Other component errors are turned into
JSON errors by the blueprint, as `guarded` does in `backend/api/notes.py`.

Writes to `library.db` reach the other windows by themselves: every commit is recorded in the
change feed (see [the data flow](architecture.md#data-flow-of-a-typical-change)). If your feature
changes notes or files outside `library.db`, record the change yourself
(`svc().changes.record({...}, client)`), as `backend/api/notes.py` does for notes.

### Common variations

- **A new item field.** Add a `Field` to `FIELDS` in `backend/library/fields.py`. The `papers`
  table gets the column on the next start (`Database.migrate` adds missing columns), the API
  accepts it, search scopes follow the `basic_search` and `searchable` flags, and the item pane
  shows it (it reads `GET /api/schema`). Add it to `Item` in `frontend/src/types.ts`, and to the
  import and export formats in `backend/formats/` if it has a counterpart there.
- **A new table or column elsewhere.** Add it to the schema code of that database
  (`Database.migrate` in `backend/library/db.py` for `library.db`), written so that it is safe to
  run on every start: `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN` only when missing,
  and data migrations that do nothing the second time. Never drop or rewrite users' data. See
  [storage](storage.md).
- **A new setting.** Add it to `DEFAULTS` in `backend/settings.py` (with a comment), to `CHOICES`
  if it takes one of a few values, and to `_migrate` if it replaces an older setting. Add it to
  `Settings` in `frontend/src/types.ts` and a row in `frontend/src/views/settings/page.ts`. Rows
  with a `data-key` attribute are saved by `settingsView.ts` without further code.
- **A new MCP tool.** See [MCP](mcp.md#adding-a-tool).

## The frontend

### 4. Types and the API call

Describe the JSON the endpoint returns in `frontend/src/types.ts`, and add a typed call to `api`
in `frontend/src/api.ts`. For a made-up "star an item" endpoint:

```ts
starItem: (id: string, starred: boolean) => request<Item>('POST', `/api/items/${id}/star`, { starred }),
```

### 5. The action

User-level operations live in `frontend/src/actions.ts`: call `api`, update `state` in
`store.ts`, and `emit` the event that tells the views what changed (`item-updated` for one item,
`items` for the list). Show failures with `toastError`. Views call actions; they do not change
the store directly.

### 6. The view

Put the UI in the view that owns that part of the screen (`frontend/src/views/`). A new dialog
gets its own module and uses `openDialog` from `frontend/src/ui/dialog.ts`; menus use `openMenu`
from `ui/menu.ts`. A view subscribes with `on('event', …)` and repaints. See
[frontend](frontend.md) for the module map, the events and the patterns used to split large
modules.

Run `npm run check` in `frontend/` while you work: everything is type-checked in strict mode.
`npm run watch` rebuilds on every change.

## Tests and docs

- **Unit tests** for the component and the endpoint, in `tests/` (pytest, with the fixtures in
  `tests/conftest.py`). Fake the network as the existing tests for `online/` and `references/` do.
- **JavaScript unit tests** in `tests/js/` for frontend logic that does not need a page.
- **A browser checklist** in `tests/e2e/` for UI that is hard to test otherwise, or extend the
  one that covers that area. Run the checklists for the UI you changed.
- **Docs in the same commit.** Update the subsystem page for the area, `docs/architecture.md` if
  the structure changed, and `README.md` for anything users see.

See [testing](testing.md) for how each kind of test is run, and `CLAUDE.md` for the checks to
run before committing.
