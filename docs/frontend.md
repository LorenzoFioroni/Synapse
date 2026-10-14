# Frontend

The web app is TypeScript in `frontend/src/`, compiled to plain ES modules in `frontend/js/`. It
has no framework: each view builds its own DOM, reads the shared state in `store.ts` and repaints
when an event tells it something changed. [Architecture](architecture.md#frontend) has the short
version of this page; this one goes into the build, the state contract, every module and the
recipes for adding to it.

## The build

| File | Role |
|---|---|
| `frontend/package.json` | The build tools (esbuild, TypeScript) and the type-only packages, pinned in `package-lock.json`. Scripts: `build`, `watch`, `check`. |
| `frontend/build.mjs` | Compiles `src/**/*.ts` to `js/` with esbuild: `format: 'esm'`, `target: 'es2022'`, `sourcemap: true`. |
| `frontend/tsconfig.json` | Type checking only (`noEmit`). esbuild does not type-check. |
| `backend/frontend.py` | Serves the pages and static files, and rebuilds `js/` when it is out of date. |

**One output file per source file.** esbuild runs without bundling, with `outbase: 'src'`, so
`src/views/notes/editor.ts` becomes `js/views/notes/editor.js`. The browser loads the same module
graph as the sources. Lazy `import()`s stay lazy, and relative paths into `vendor/` stay valid:
`editor.ts` loads `../../../vendor/cm6/cm6.js`, which resolves from `js/views/notes/` exactly as
it does from `src/views/notes/`. Imports in the sources therefore name `.js` files;
`moduleResolution: "bundler"` lets TypeScript map them to the `.ts` sources.

**Clean builds.** `build.mjs` deletes `js/` before each build, so a deleted source leaves no stale
output behind. After a build without errors it writes `js/.build-stamp` (the time of the build).

**Source maps.** Every `.js` file has a `.js.map` next to it, so the browser's debugger shows and
steps through the TypeScript.

**Lazy loading.** Large or rarely used code is loaded on first use with `import()`:

- CodeMirror (`vendor/cm6/cm6.js`) and markdown-it with KaTeX (`vendor/md/md.js`) in
  `views/notes/editor.ts`, only when Notes is opened;
- `views/metadataReview.ts` from `afterAdd.ts`, `views/exportDialog.ts` from `settingsView.ts`;
- the classic UMD scripts Mermaid (`views/notes/richRender.ts`) and force-graph
  (`views/graph/forceGraph.ts`) by adding a `<script>` tag; they put `window.mermaid` and
  `window.ForceGraph` on the page.

### Why `js/` is not committed

`frontend/js/` and `frontend/node_modules/` are in `.gitignore`. `js/` is generated from `src/`,
so only the sources are kept in git and a commit can never hold the two out of step. The server
builds it instead: `server.py` calls `ensure_built(config.frontend_dir)` before it creates the
app.

- `needs_build(frontend)` is true when `js/.build-stamp` is missing, or when any file in
  `BUILD_INPUTS` (`src/`, `build.mjs`, `package.json`, `tsconfig.json`) is newer than it.
- `ensure_built` then runs `node build.mjs` if Node.js is on the `PATH` and
  `node_modules/esbuild` exists. A failed build raises `BuildError` with the compiler output.
- Without Node.js or without `npm ci`, an existing old build is served with a warning in the log
  (so `git pull` never stops a working install), and a missing build raises `BuildError` with the
  commands to run. `server.py` turns it into `SystemExit`.

`tests/test_frontend_build.py` covers these cases. Pages, scripts and styles are sent with
`Cache-Control: no-store` (`NO_CACHE`), so a rebuilt frontend is picked up on the next reload.

### Type checking

`npm run check` runs `tsc --noEmit` for `frontend/` and then for `../browser_extension` (see
[browser extension](browser-extension.md)). CI and the pre-commit hook run it. The settings that
matter:

- `strict` and `noUnusedLocals`: no implicit `any`, null checks, no unused variables or imports.
- `isolatedModules` and `verbatimModuleSyntax`: each file must compile on its own, because
  esbuild compiles files one at a time. Type-only imports are written `import type` (or with an
  inline `type`), so esbuild can drop them.
- `types: []`: no global `@types` package leaks into the app (`@types/chrome` is for the
  extension only).

Two files hold the types that are not in the code itself:

- `src/types.ts` describes what the JSON API answers (`Item`, `Attachment`, `Settings`,
  `ItemList`, `NotesListing` and so on). It names its backend sources: item fields from
  `backend/library/fields.py`, folders, tags and smart folders from `backend/library/organize.py`,
  settings from `DEFAULTS` in `backend/settings.py`. Keep it in step when an endpoint changes.
- `src/globals.d.ts` types what the pages put on `window` (`zcViewer`, `zcViewerGoto`, `zcLinks`,
  `pdfjsLib`, `mermaid`, `ForceGraph`, and the read-only hooks `zcNotes`, `zcGraph`, `zcChanges`,
  `zcAnnotations` used by the browser checklists) and the custom DOM events: `zc:pagecreated` and
  `zc:pagerendered` on `document`, and `zc:longpress` on elements.

### Typing the vendored libraries

The app loads third-party code from `frontend/vendor/` (see
[`frontend/vendor/README.md`](../frontend/vendor/README.md) for versions, licences and how to
update). The types come from npm packages that `frontend/package.json` installs as
devDependencies at the same versions as the vendored builds. They are never bundled into the app.

| Vendored | Types from |
|---|---|
| `vendor/cm6/cm6.js` | `vendor/cm6/cm6.d.ts`, which repeats the re-exports of `tools/cm6/entry.js`; the types resolve to the `@codemirror/*` and `@lezer/*` packages. |
| `vendor/md/md.js` | `vendor/md/md.d.ts` (`MarkdownIt`, `katex`, `footnote`), resolving to `katex` and `@types/markdown-it`. |
| `vendor/pdfjs/pdf.min.js` | `pdfjs-dist` 3.4.120, through `window.pdfjsLib` in `globals.d.ts`. |
| `vendor/force-graph/` | `force-graph` 1.51.4, through `window.ForceGraph`. |
| `vendor/mermaid/` | No package: `globals.d.ts` declares the two calls the app makes (`Mermaid`). |

`tools/cm6/build.sh` and `tools/md/build.sh` write the `.d.ts` files when they rebuild a bundle.
When a library is updated, change its version in the `tools/*/package.json` and in
`frontend/package.json` together, or the types and the running code disagree.

## The pages

| Page | Loads | Notes |
|---|---|---|
| `index.html` | the CSS files, `vendor/md/katex/katex.min.css`, then `js/main.js` as a module | An inline script applies the cached theme (`zc.themeHint` in `localStorage`) before the first paint, to avoid a white flash. |
| `viewer.html` | `css/tokens.css`, `vendor/pdfjs/pdf_viewer.css`, the classic script `vendor/pdfjs/pdf.min.js`, then the modules `js/viewer/viewer.js`, `notesBridge.js`, `links.js`, `annotations.js` | The PDF viewer, one tab per PDF. Its own styles and toolbar markup are inline. |
| `pair.html` | nothing else | Shown to unpaired devices under `--lan`. Self-contained (inline style and script, no build) because the guard lets an unpaired device fetch only `PAIRING_PATHS` (`backend/access/guard.py`). |

In `viewer.html` the order matters. pdf.js is a classic script, so it has run before any module.
Modules run in document order, so `viewer.ts` publishes `window.zcViewer` before the add-ons run.
The add-ons import nothing from `viewer.ts` at run time (only its types). They hook in through
`window.zcViewer` and the `zc:pagecreated` / `zc:pagerendered` events, so each can change
without touching the others. `viewer.ts` owns the toolbar and shows the colour and size inputs a
tool has; it fires `zc:toolchange`, and `annotations/prefs.ts` then sets their values. `notesBridge.ts` also defines `window.zcViewerGoto`, which other app
tabs call to point an open viewer tab at a new spot (`api.ts`, `openAttachment`).

The viewer modules do not use `api.ts`: `viewer/annotations/model.ts` has a small `api()` of its
own (with its own `X-Synapse-Client` id), and `notesBridge.ts` calls `fetch` directly.

## State and actions

### `store.ts`: the state

`state` is one plain object of type `AppState`. Views read it freely.

| Field | What it holds |
|---|---|
| `schema` | `GET /api/schema`: item types and fields. |
| `settings`, `settingsMeta` | The user's settings and the rest of the `GET /api/settings` answer. |
| `view` | `'library'`, `'trash'`, `'duplicates'`, `'unfiled'`, `'folder:<id>'`, `'smart:<id>'`. |
| `query`, `mode` | The search text and scope (`SearchMode`). |
| `items`, `hits`, `counts`, `sidebar` | The current list (already filtered by the server), full-text snippets by id, the counts per view, and folders, tags and smart folders. |
| `tagFilter` | Tag names; items must have all of them. |
| `selection`, `anchor`, `cursor` | Selected ids, the anchor for Shift+click ranges, the keyboard row. |
| `sort`, `columns`, `openFolders` | Saved in `localStorage` (`zc.sort`, `zc.columns`, `zc.openFolders`). |
| `expanded` | Items whose attachment rows are shown. |
| `loading`, `indexer` | A list request is running; the last `/api/stats` answer. |
| `noteKeys` | Citation key → its paper note and how many notes cite it. |
| `order` | The list's display order, set by `views/itemlist/rows.ts` (only the rows on screen are in the DOM). |

`store.ts` also has the helpers that derive from the state: `byId`, `selectedItems`,
`sortedItems`, `folderTree`, `folderPath`, `folderTotal`, `folderDescendants`, `tagColor` and the
column definitions `COLUMNS`.

The list, the view, the search and the selection change through `actions.ts`. A few slices
belong to one view, which writes them itself: `settings` (`settingsView.ts`), `sort`
(`itemlist/columns.ts`), `noteKeys` (`itemlist.ts`), `order` (`itemlist/rows.ts`) and `indexer`
(`main.ts`).

### Events

`on(event, fn)` subscribes and `emit(event, payload)` calls every listener in order. There is no
unsubscribe: views subscribe once, in their `init…` function, for the life of the page.

The events are typed: `AppEvents` in `store.ts` lists every event name with its payload type
(`void` for none). `tsc` rejects an unknown name, a missing or wrong payload, and a listener that
expects something else. To add an event, add it to `AppEvents` and to the table below.

| Event | Payload | Emitted by | Heard by |
|---|---|---|---|
| `items` | none | `actions.ts` (load, refresh, remove, find PDF), `referencesSection.ts` | item list rows, item pane, sidebar, status bar, `main.ts` (stats), `itemlist.ts` (BibTeX cache), `notesView.ts` |
| `selection` | none | `actions.ts` | item list rows, item pane, status bar, `itemlist.ts`, `main.ts` (history entry) |
| `item-updated` | item id | `actions.patchLocal` | item list rows (that row), item pane (its header) |
| `sidebar` | none | `actions.ts` | sidebar |
| `view` | none | `actions.setView`, `actions.loadItems` | sidebar, `main.ts` (history entry) |
| `search` | none | `actions.setSearch` | `notesView.ts` (a search closes Notes) |
| `loading` | boolean | `actions.loadItems` | status bar |
| `tag-filter` | none | `actions.ts` | sidebar (tag pane) |
| `focus-field` | `{ id, field }` | `actions.newItem`, `itemlist.ts` | item pane |
| `columns` | none | `itemlist/columns.ts` | `itemlist.ts` (head and body) |
| `settings` | none | `settingsView.ts` | sidebar, rows (density), `notesView.ts`, `main.ts` |
| `indexer` | none | `main.ts` | sidebar, status bar |
| `workspace` | none | `notesView.ts` (Notes opened or closed) | sidebar, `itemlist.ts`, `notes/panels.ts`, `main.ts` |
| `notes-count` | number or null | `notesView.ts`, `notes/tree.ts` | sidebar, `itemlist.ts` |
| `focus-list` | none | `notesView.ts`, `settingsView.ts` | `itemlist.ts` |
| `open-note` | `{ path, line? }` | item pane, `itemlist/menus.ts` | `main.ts` |
| `open-notes`, `close-notes` | none | sidebar | `main.ts` |
| `show-item` | item id | `notesGraph.ts`, `citeCard.ts`, `notesView.ts`, `referencesSection.ts` | `main.ts` |
| `show-graph` | focus (`item:<id>`) | `itemlist/menus.ts` | `main.ts` |
| `open-import` | optional `File` | `itemlist/empty.ts`, `itemlist.ts`, `settingsView.ts` | `main.ts` |
| `open-identifier`, `clear-search` | none | `itemlist/empty.ts` | `main.ts` |
| `set-search-mode` | `SearchMode` | `itemlist/empty.ts`, `settingsView.ts` | `main.ts` |

The events `main.ts` hears are how a view reaches code it must not import (opening Notes from the
item pane, for example), which keeps the views free of import cycles.

### `actions.ts`: user-level operations

Views call these for anything that changes the item list. Each one calls `api`, updates `state`
and emits the events above; failures become a toast (`toastError`). The groups:

- **The list.** `loadItems` (a sequence number drops answers that a newer request superseded; a
  404 for a deleted folder falls back to the library), `setView`, `setSearch`, `select`.
- **Small updates without a reload.** `patchLocal` replaces one item; `refreshItems(ids)` fetches
  only those items, and reloads the list instead when the view is filtered, because then items
  may leave or join it.
- **Items.** `updateField`, `newItem`, `trashSelected` (with Undo), `keepOnly`,
  `restoreSelected`, `deleteForever`, `emptyTrash`, `regenerateCitekeys`.
- **PDFs.** `openPdf`, `findPdf`, `findPdfs` (one at a time, cancellable), `uploadAttachments`,
  `addPdfsAsItems`.
- **Folders and tags.** `createFolder`, `renameFolder`, `moveFolder`, `deleteFolder`,
  `addToFolder`, `removeFromFolder`, `moveBetweenFolders`, `removeFolderMembership`, `addTags`,
  `removeTags`, `toggleTagFilter`, `clearTagFilter`, `updateTag`, `deleteTag`, `deleteSmart`.

Views also call `api` directly for data that only they show or that is not in `state`: the
full item in the item pane, Notes, Settings, the references section and most dialogs. When such
a call changes items in the list, the view still goes through `refreshItems` or `loadItems` so
the store stays right.

### `api.ts`: the client

`api` is an object of one-line typed calls, one per endpoint, built on `request<T>(method, url,
body)`. It sends JSON (or `FormData` for uploads) and throws `ApiError` (with `status` and `data`)
whose message is the server's `error` or a readable fallback, so callers can show it as it is. A
network failure is `ApiError` with status 0. A 401 with `paired: false` reloads the page, which
then shows the pairing message.

Every request carries `X-Synapse-Client: CLIENT_ID`, a random id per page load. The change feed
uses it to leave a page's own edits out of what it reports back to that page.

`api.ts` also opens PDFs: `fileUrl`, `viewerUrl` and `openAttachment` keep one viewer tab per PDF
and reuse it through its `zcViewerGoto`; `pdfTabFor` and `pointPdfTab` take the tab inside the
click (popup blockers require that) for callers that know the page only after an `await`.

### `nav.ts` and `changes.ts`

`nav.ts` turns moves between the library and Notes into browser history entries, kept in
`history.state.zc` as a `NavEntry`: `{ ws: 'notes', path }`, `{ ws: 'notes', path, graph: true,
focus }` or `{ ws: 'library', view, sel }`. `navPush` adds an entry (or updates the current one if
it is the same place), `navUpdate` keeps the library entry's view and selection current,
`navSnapshot` refreshes the entry being left, and `initNav` calls back on `popstate`. While going
Back or Forward nothing is pushed.

`changes.ts` polls `/api/changes` every 4 s while the page is visible, and reloads the library
(`actions.loadItems`) or the notes (`notesChanged`) when another page, device or process changed
them. It waits while a field, a dialog or the editor is in use. See
[sync and devices](sync-and-devices.md).

## Module map

| Module | Role |
|---|---|
| `main.ts` | Entry point: loads the schema and settings, calls each view's `init…`, the toolbar, search, status bar, resizer, drawer, shortcuts, and the handlers for the events above. |
| `afterAdd.ts` | What runs after items are added (metadata, PDF, references, tags), per the never / always / ask settings. |
| `icons.ts` | Inline SVG icons: `icon(name, cls)` and `typeIcon(itemType)`. |
| `util.ts` | Small helpers: `escapeHtml`, `h`, `debounce`, dates and sizes, `storage` / `store` (`localStorage` that never throws), `modKey`, `copyText`, `diffWords`, `hitBadge` (the "full text" or "note" badge of a search match). |

**`views/`: the library screens and dialogs.**

| Module | Role |
|---|---|
| `itemlist.ts` | The items table: selection, keyboard, dragging items out, file drops, copying BibTeX. |
| `itemlist/columns.ts` | The header: visible columns, order, widths, sorting. |
| `itemlist/rows.ts` | The virtualised rows: only rows near the viewport are in the DOM, between two spacers. |
| `itemlist/empty.ts` | What an empty list says, with buttons for what to do. |
| `itemlist/menus.ts` | Context menus for items and attachments, shared with the item pane. |
| `itempane.ts` | The right-hand pane: editable fields, authors, abstract, attachments. |
| `referencesSection.ts` | The item pane's References section (see [references](references.md)). |
| `sidebar.ts` | Library views, folder tree, smart folders, tag pane, and drag and drop onto them. |
| `organizeDialogs.ts` | Folder picker, tag editor and input, smart folder dialog. |
| `importDialog.ts`, `exportDialog.ts` | Import with preview and duplicate flags; export to the formats. |
| `identifierDialog.ts` | Add items by DOI, arXiv id or ISBN. |
| `metadataReview.ts` | Retrieve Metadata: the `LookupJob` (parallel, cancellable, can run in the background) and the review dialog. |
| `settingsView.ts` | The Settings page: binds the controls and saves each change; `applyAppearance`. |
| `settings/page.ts` | The Settings markup (`settingsHtml`). |
| `settings/backups.ts`, `settings/devices.ts` | Settings → Backups and Settings → Devices (pairing). |

**`views/notes/`: the notes workspace.** [Notes](notes.md) covers it in depth. In short:
`notesView.ts` sets the workspace up and opens and closes notes; `workspace.ts` is the shared
state; `noteSession.ts` saves (hash-checked, with drafts and retries); `editor.ts`,
`livePreview.ts`, `previewWidgets.ts`, `completion.ts` and `mathClick.ts` are the CodeMirror editor; `render.ts`,
`richRender.ts`, `embed.ts`, `linkData.ts`, `mdscan.ts`, `equations.ts`, `macros.ts` and `mathFit.ts` render Markdown and
math; `tree.ts`, `treeInput.ts` and `fileOps.ts` are the notes list; `readView.ts`,
`sidePanel.ts` and `panels.ts` the views around a note; `citeCard.ts`, `citeTarget.ts` and
`library.ts` connect citations to the library; `aiBar.ts` and `aiEdits.ts` show Claude's edits;
`conflict.ts`, `diff.ts`, `rekeyDialog.ts` and `attachmentsDialog.ts` are dialogs; `exportNote.ts`
and `portable.ts` export a note as PDF, HTML or Markdown.

**`views/graph/`: the graph in Notes.** `model.ts` turns `/api/graph` into what is shown (no
DOM, unit-tested); `forceGraph.ts` draws it with force-graph; `notesGraph.ts` is the local graph
beside a note and the global graph in place of the editor.

**`ui/`: generic widgets.** `dialog.ts` (`openDialog`, `confirmDialog`, `promptDialog`),
`menu.ts` (`openMenu`, `closeMenu`, with keyboard navigation), `toast.ts` (`toast`,
`toastError`, `progressToast`), `longpress.ts` (`installLongPress`: a long press sends
`zc:longpress` and then `contextmenu`, since a tablet has neither right-click nor hover).

**`viewer/`: the PDF viewer.** `viewer.ts` lays out the pages, draws those near the viewport and
zooms. `links.ts` makes links clickable with hover previews and Back/Forward, using the pure
helpers in `linkNav.ts`. `notesBridge.ts` is Quote to note and Snapshot to note, and finds a
quote on the page with `locate.ts`. `annotations.ts` adds a layer per page, loads the records and
picks up changes made elsewhere; `annotations/model.ts` holds the records, draws them, saves each
change and keeps undo and redo; `annotations/input.ts` is the drawing tools (boxes, ink, text
boxes, the eraser) and the controls of a selected annotation (its colours, Delete);
`annotations/colorMenu.ts` is choosing a colour (swatches, None, any other colour), in the toolbar
and for a selected annotation; `annotations/textMarks.ts` highlights and underlines selected text;
`annotations/pointer.ts` tells mouse, Pencil and finger apart; `annotations/prefs.ts` keeps each
tool's colours and size.

How the annotation tools behave:

- **Colours.** Each tool starts with the colour and size it was last used with on this device
  (`localStorage`, key `zc.toolStyle`), or else with its colour from Settings → PDF Viewer
  (`annotation_color_…`, read from `GET /api/settings` when the viewer opens and when its tab
  gets the focus again). A remembered colour is stored with the default it was chosen over, so
  a new default in Settings replaces it on every device. The toolbar's colours open
  `openColorMenu`; the inputs `#tool-color` and `#tool-fill` only hold the values.
- **Whole words.** A highlight or underline of text is widened to whole words
  (`snapToWords`, with `wordStart` / `wordEnd` from `annotations/lines.ts`) unless
  `annotation_snap_words` is off or Alt is held when the mouse button or the Pencil is lifted.
  Only the text of the runs where the selection starts and ends is looked at. On a touch screen
  the preview drawn while selecting is snapped the same way.
- **One rect per line.** The text layer gives a box per run of text, and inline math, sub- and
  superscripts sit higher or lower than the words around them. `mergeLines`
  (`annotations/lines.ts`, unit-tested in `tests/js/annotationLines.test.mjs`) merges the boxes
  into one per line at the height most of the line's text has, so an underline does not break at
  every formula. Boxes level with each other but more than a line height apart (two columns)
  stay apart.
- **Boxes.** A box has a border colour (`color`, with `size` its width) and a fill colour
  (`fill`), and either may be left out, not both: no border is `size: 0`, no fill is
  `fill: null`. The fill is drawn like a highlight (`boxRect` in `annotations/model.ts`), so a
  filled box without a border highlights an area of the page. In the toolbar a part left out is
  marked with `data-none` on its colour (`#color-wrapper`, `#fill-wrapper`), which `border()` and
  `fill()` in `model.ts` read; `prefs.ts` remembers both, with the colour of each. A box's `text`
  is the words of the text layer inside it (`textIn`).
- **Changing an annotation.** Selecting an annotation (Select tool) shows a pill with colour
  swatches, a custom colour and Delete; a box has a row for its border and one for its fill,
  each with None. A new colour is one `change` (one undo step); the custom picker shows the
  colour while it is open and saves when it closes.

### Patterns for splitting a module

When a module grows a second responsibility it is split, and the parts need a way to share
state and call each other without import cycles. Three patterns are used:

- **A shared state object and `hooks`.** `views/notes/workspace.ts` exports `ws` (the open note,
  editor, listing, link data) and `hooks` (`openNote`, `doOpen`, `closeCurrent`, `setMode`,
  `openNotesGraph`) with do-nothing defaults. `notesView.ts` fills them with
  `Object.assign(hooks, …)`. The other parts import `workspace.ts`, never `notesView.ts`.
  `viewer/annotations/model.ts` does the same with `hooks.onSelect`, which
  `annotations/input.ts` sets to show its selection controls.
- **Dependency objects.** A module that needs a few things from its caller takes them as an
  argument: `attachCiteCards(container, deps: CiteCardDeps)` in `citeCard.ts`,
  `livePreview(cm, deps: PreviewDeps)` in `livePreview.ts`, `initNotesGraph(ctx:
  NotesGraphContext)` in `notesGraph.ts`. The module then does not import the notes view at all.
  `livePreview` also takes the CodeMirror library `cm` as an argument, because it is loaded
  lazily.
- **ES module live bindings.** `annotations/model.ts` exports `let` variables (`attId`,
  `selectedId`, `redoStack`) that only it reassigns; importers always see the current value. Code
  outside the module must not assign to them; it calls a function of the module instead.

## Styling

`frontend/css/` is plain CSS, loaded by `index.html` in this order:

| File | Holds |
|---|---|
| `tokens.css` | The design tokens as CSS variables: colours, spacing, radii, shadows, fonts and sizes. |
| `base.css` | Reset and base elements. |
| `components.css` | Buttons, inputs, menus, dialogs, toasts, empty states, chips. |
| `layout.css` | The shell: toolbar, sidebar, panes, resizers, status bar, narrow screens. |
| `items.css`, `itempane.css` | The items table; the item pane and the metadata review. |
| `settings.css`, `notes.css`, `graph.css` | Settings, the notes workspace, the graph. |
| `export.css` | Not loaded by the app: the page and print rules of an exported note, added after `tokens.css` and `notes.css` (`views/notes/exportNote.ts`). |

**Themes.** Every colour is a variable in `tokens.css`. Light is the default; dark applies under
`@media (prefers-color-scheme: dark)` unless `<html data-theme="light">`, and always with
`data-theme="dark"`. `applyAppearance` in `settingsView.ts` sets `data-theme` from the setting,
caches it as `zc.themeHint` for the next first paint, sets `data-density` and the base font size
`--fs`. Use the variables (`var(--text)`, `var(--bg-elev)`, `var(--border)` …) rather than
fixed colours, and both themes work. The notes editor takes its colours from them too.

**Density.** `:root[data-density="compact"]` lowers `--row-h`, the item list's row height.

**Touch.** `@media (pointer: coarse)` rules give bigger targets and turn off the long-press
callout on the app's own controls (`layout.css`, `notes.css`). `@media (hover: none)` rules show
tools that otherwise appear on hover (`itempane.css`, `notes.css`).

**Icons.** `icons.ts` keeps each icon as SVG path data on a 16 px grid, drawn with
`currentColor`, so it takes the colour of the text around it. `icon('search')` returns the
markup; an unknown name falls back to `doc`. To add one, add its paths to `P`.

## Recipes

[Adding a feature](adding-a-feature.md) walks through a whole feature, backend included.

### Adding an API call

1. Describe the answer in `src/types.ts`.
2. Add a typed call to `api` in `src/api.ts`, built on `request<T>`. Use `qs()` for query
   strings and `FormData` for files.
3. If the call changes the item list, wrap it in an action in `actions.ts`: call `api`, update
   `state`, `emit('item-updated', id)` for one item or `emit('items')` for the list, and catch
   errors with `toastError`.
4. Run `npm run check`.

### Adding a view or dialog

1. A dialog gets its own module in `src/views/` that exports one `open…` function built on
   `openDialog` from `ui/dialog.ts`; menus use `openMenu` from `ui/menu.ts`.
2. A view that stays on screen exports an `init…` function, called once from `boot()` in
   `main.ts`. It subscribes with `on(...)` to the events it needs and repaints from `state`.
3. If something elsewhere must open it and cannot import it without a cycle, add an event that
   `main.ts` handles, and add it to the table above.
4. Put its styles in the CSS file of its area, using the variables in `tokens.css`, and add
   icons to `icons.ts`.
5. If it is large, split it with one of the patterns above, and add it to the module map here and
   in [architecture](architecture.md#modules).
6. Cover what you can with a JavaScript unit test (keep the logic in a DOM-free module) and the
   rest with a browser checklist. See [testing](testing.md).
