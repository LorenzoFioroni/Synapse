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
  (`plugins/core/graph/forceGraph.ts`) by adding a `<script>` tag; they put `window.mermaid` and
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
| `vendor/pdfjs/pdf.min.mjs` | `vendor/pdfjs/pdf.min.d.mts`, resolving to `pdfjs-dist` 6.4.299; the viewer's modules reach it through `window.pdfjsLib` (`globals.d.ts`), set by `viewer/pdfjs.ts`. |
| `vendor/force-graph/` | `force-graph` 1.51.4, through `window.ForceGraph`. |
| `vendor/mermaid/` | No package: `globals.d.ts` declares the two calls the app makes (`Mermaid`). |

`tools/cm6/build.sh` and `tools/md/build.sh` write the `.d.ts` files when they rebuild a bundle.
When a library is updated, change its version in the `tools/*/package.json` and in
`frontend/package.json` together, or the types and the running code disagree.

## The pages

| Page | Loads | Notes |
|---|---|---|
| `index.html` | the CSS files, `vendor/md/katex/katex.min.css`, then `js/main.js` as a module | An inline script applies the cached theme (`zc.themeHint` in `localStorage`) before the first paint, to avoid a white flash. |
| `viewer.html` | `css/tokens.css`, `vendor/pdfjs/pdf_viewer.css`, then the modules `js/viewer/pdfjs.js` (loads pdf.js, an ES module), `viewer.js`, `notesBridge.js`, `links.js`, `annotations.js` | The PDF viewer, one tab per PDF: a frame in one of the app's tabs (`tabs/tabs.ts`), or a browser tab of its own (Settings → PDF Viewer). Its own styles and toolbar markup are inline. |
| `pair.html` | nothing else | Opened from the pairing QR code under `--lan`. Self-contained (inline style and script, no build) because the guard lets an unpaired device fetch only `PAIRING_PATHS` (`backend/access/guard.py`). |
| `signin.html` | nothing else | Sent by the guard, with status 401, instead of any page a device that is not signed in asks for: how to pair, and **Sign In with a Passkey** where passkeys can work. Self-contained for the same reason. |

In `viewer.html` the order matters. pdf.js is a classic script, so it has run before any module.
Modules run in document order, so `viewer.ts` publishes `window.zcViewer` before the add-ons run.
The add-ons import nothing from `viewer.ts` at run time (only its types). They hook in through
`window.zcViewer` and the `zc:pagecreated` / `zc:pagerendered` events, so each can change
without touching the others. `viewer.ts` owns the toolbar and shows the colour and size inputs a
tool has; it fires `zc:toolchange`, and `annotations/prefs.ts` then sets their values. `notesBridge.ts` also defines `window.zcViewerGoto`, which other app
tabs call to point an open viewer tab at a new spot (`api.ts`, `openAttachment`; `tabs.ts` for a viewer in
one of the app's tabs). Inside the app's tabs, `links.ts` keeps the viewer's Back/Forward in memory
(`jumpHistory.ts`) instead of the browser history the page around shares, and `inTab.ts` passes
the tab keys to the page around (`window.zcTabs`); see [the app window](desktop.md).

The viewer modules do not use `api.ts`: `viewer/annotations/model.ts` has a small `api()` of its
own (with its own `X-Synapse-Client` id), and `notesBridge.ts` calls `fetch` directly.

## State and actions

### `store.ts`: the state

`state` is one plain object of type `AppState`. Views read it freely.

| Field | What it holds |
|---|---|
| `schema` | `GET /api/schema`: item types and fields. |
| `settings`, `settingsMeta` | The user's settings and the rest of the `GET /api/settings` answer. |
| `view` | `'library'`, `'trash'`, `'duplicates'`, `'unfiled'`, `'folder:<id>'`, `'smart:<id>'`, `'source:<plugin>.<list>'` (a plugin's list of papers). |
| `query`, `mode` | The search text and scope (`SearchMode`). |
| `items`, `hits`, `counts`, `sidebar` | The current list (already filtered by the server), full-text snippets by id, the counts per view, and folders, tags and smart folders. |
| `tagFilter` | Tag names; items must have all of them. |
| `selection`, `anchor`, `cursor` | Selected ids, the anchor for Shift+click ranges, the keyboard row. |
| `sort`, `columns`, `openFolders` | Saved in `localStorage` (`zc.sort`, `zc.columns`, `zc.openFolders`). |
| `sourceSort` | The sort of a plugin's list; `null` (the start) is the plugin's own order (`_rank`). Not saved. `viewSort()` gives the one the view uses, `sortBy(col)` is a click on a column. |
| `fieldDefs`, `sources` | The per-item fields and the lists of papers running plugins offer (`plugins/host.ts` loads both). |
| `expanded` | Items whose attachment rows are shown. |
| `loading`, `indexer` | A list request is running; the last `/api/stats` answer. |
| `noteKeys` | Citation key → its paper note and how many notes cite it. |
| `order` | The list's display order, set by `views/itemlist/rows.ts` (only the rows on screen are in the DOM). |

`store.ts` also has the helpers that derive from the state: `byId`, `selectedItems`,
`currentSource`, `isOutside`, `sortedItems`, `folderTree`, `folderPath`, `folderTotal`, `folderDescendants`, `tagColor` and the
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
| `show-item` | item id | `plugins/core/graph/notesGraph.ts`, `citeCard.ts`, `notesView.ts`, `referencesSection.ts` | `main.ts` |
| `open-import` | optional `File` | `itemlist/empty.ts`, `itemlist.ts`, `settingsView.ts` | `main.ts` |
| `open-identifier`, `clear-search` | none | `itemlist/empty.ts` | `main.ts` |
| `set-search-mode` | `SearchMode` | `itemlist/empty.ts`, `settingsView.ts` | `main.ts` |
| `sources` | none | `plugins/host.ts` (`syncSources`) | sidebar (the Plugins section) |
| `open-view` | a view (`source:<id>`, `folder:<id>` …) | `plugins/host.ts` (`p.sources.open`) | `main.ts` (leaves Notes, Tasks, Settings or a page) |

The events `main.ts` hears are how a view reaches code it must not import (opening Notes from the
item pane, for example), which keeps the views free of import cycles.

### `actions.ts`: user-level operations

Views call these for anything that changes the item list. Each one calls `api`, updates `state`
and emits the events above; failures become a toast (`toastError`). The groups:

- **The list.** `loadItems` (a sequence number drops answers that a newer request superseded; a
  404 for a deleted folder falls back to the library; when every selected item left the list, the
  one after them is selected, as trashing does), `setView`, `setSearch`, `select`.
- **Small updates without a reload.** `patchLocal` replaces one item; `refreshItems(ids)` fetches
  only those items, and reloads the list instead when the view is filtered, because then items
  may leave or join it.
- **Items.** `updateField`, `newItem`, `trashSelected` (with Undo), `keepOnly`,
  `restoreSelected`, `deleteForever`, `emptyTrash`, `regenerateCitekeys`.
- **PDFs.** `openPdf`, `findPdf`, `findPdfs` (one at a time, cancellable), `uploadAttachments`,
  `addPdfsAsItems`.
- **Papers from a plugin's list.** `addOutside(items, folderId)` adds those not in the library
  (one request each, with a toast to undo), reloads the list (they come back as library items) and
  runs what Settings → Adding Items says. Items with `_outside` (id `x:…`) are not in the
  library: trashing, tags, dragging and copying BibTeX leave them out, and the item pane and the
  item menu have their own versions for them (`views/itempane/outside.ts`, `outsideMenu` in
  `itemlist/menus.ts`).
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
click (popup blockers require that) for callers that know the page only after an `await`. When
PDFs open in the app's own tabs (the default), all three hand the PDF to `tabs/tabs.ts`, which
registers itself with `setPdfTabOpener` so that `api.ts` does not import it.

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
| `tabs/model.ts`, `tabs/tabs.ts` | The app's own tabs: the first tab (Library or Tasks), note tabs and PDF tabs. `model.ts` has the tabs without a DOM (opening, the preview tab, closing and where it goes, reopening, renamed and deleted notes, order, saving); `tabs.ts` draws the bar (`#tabbar`), holds a viewer frame per PDF tab (`#tab-views`), has the notes workspace show a note tab's note (`TabsHost`, from `main.ts`), the tab keys (`tabShortcut`), the menus and dragging tabs between windows. See [the app window](desktop.md). |
| `presence.ts` | Says hello to the server while the page is open, for a server that stops with its last window. |
| `windowColor.ts` | The window's title bar colour (`theme-color`): neutral, or the shown note's folder colour (see [the app window](desktop.md#folder-colours)). |
| `windows.ts` | Opening a place of the app in a new window (`openNewWindow`) and the address that names it (`placeHash`, `readPlace`); `main.ts` opens it (`openFromHash`). See [the app window](desktop.md#windows). |
| `extend.ts` | The extension points: registries of core workspace navigation, commands, pages, item-pane sections and item actions that the views read and plugins (and `main.ts`, for Synapse's own commands) register into; keys (`parseKey`, `keyMatches`, `keyLabel`, `keyProblem`) and the palette's ranking. No DOM, unit-tested. |
| `plugins/host.ts` | Plugins' frontends: loads each running plugin's module and styles, gives it its `PluginContext`, takes everything back when it stops (see [plugins](plugins.md#the-frontend)). |
| `plugins/api.ts` | The `PluginContext` and the other types promised to plugins (types only; plugin authors copy it). |
| `icons.ts` | Inline SVG icons: `icon(name, cls)` and `typeIcon(itemType)`. |
| `util.ts` | Small helpers: `escapeHtml`, `h`, `debounce`, dates and sizes, `storage` / `store` (`localStorage` that never throws), `modKey`, `copyText`, `diffWords`, `hitBadge` (the "full text" or "note" badge of a search match). |

**`views/`: the library screens and dialogs.**

| Module | Role |
|---|---|
| `itemlist.ts` | The items table: selection, keyboard, dragging items out, file drops, copying BibTeX. |
| `itemlist/columns.ts` | The header: visible columns, order, widths, sorting. Plugins' per-item fields are columns `f:<plugin>.<name>` (`colDef`, `syncFieldColumns` and `fieldText` in `store.ts`), kept in the saved columns while their plugin is off. |
| `itemlist/rows.ts` | The virtualised rows: only rows near the viewport are in the DOM, between two spacers. |
| `itemlist/empty.ts` | What an empty list says, with buttons for what to do. |
| `itemlist/menus.ts` | Context menus for items and attachments, shared with the item pane. |
| `itempane.ts` | The right-hand pane: editable fields, authors, abstract, attachments; *From Plugins* (values plugins attach, from `/api/items/<id>/fields`) and the sections plugins add. |
| `referencesSection.ts` | The item pane's References section (see [references](references.md)). |
| `settings/pluginSections.ts` | Mounts and disposes plugin Settings sections as their registry changes, preserving unrelated controls and removing stale navigation. |
| `sidebar.ts` | Plugin-owned workspace navigation (Tasks and Graph), library views, folder tree, smart folders, tag pane, the pages plugins add, and drag and drop onto them. |
| `pages.ts` | A plugin's page in place of the library (`openPage`, `closePage`; `body[data-workspace="page"]`). |
| `palette.ts` | The command palette (⌘⇧P / Ctrl+Shift+P, F1): every command in `extend.ts`, best match first. |
| `organizeDialogs.ts` | Folder picker, tag editor and input, smart folder dialog. |
| `importDialog.ts`, `exportDialog.ts` | Import with preview and duplicate flags; export to the formats. |
| `identifierDialog.ts` | Add items by DOI, arXiv id or ISBN. |
| `metadataReview.ts` | Retrieve Metadata: the `LookupJob` (parallel, cancellable, can run in the background) and the review dialog. |
| `settingsView.ts` | The Settings page: binds the controls and saves each change; `applyAppearance`. |
| `settings/page.ts` | The Settings markup (`settingsHtml`). |
| `settings/backups.ts`, `settings/devices.ts` | Settings → Backups and Settings → Devices (pairing). |
| `settings/passkeys.ts` | Settings → Devices → Passkeys: adding one with `navigator.credentials.create`, removing one. |
| `settings/aiAccess.ts` | Settings → AI Access: access tokens for `/mcp`, with the `claude mcp add` command shown once. |
| `settings/plugins.ts` | Settings → Plugins: the plugins found, turning them on (after a confirmation listing their access) and off, their settings drawn from each manifest, tools for other computers, Restart and the log. `pluginsChanged()` redraws it when the change feed names `plugins`. See [plugins](plugins.md). |

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

**`plugins/core/tasks/`: tasks.** [Tasks](tasks.md#frontend-frontendsrcviewstasks) covers it. In short:
`taskData.ts` holds the tasks and makes the changes; `taskQuery.ts` and `dates.ts` are the logic
without a DOM (views, search, sorting, groups; reading dates); `taskRender.ts` draws a task;
`taskForm.ts` is the form of the detail pane and the popup; `tasksView.ts`, `taskList.ts` and
`tasksState.ts` the Tasks page; `noteTasks.ts` tasks inside notes; `notePicker.ts` chooses a note.

**`plugins/core/graph/`: the graph in Notes** (the Graph core plugin, `plugins/graph`). `index.ts`
gives the notes view the graph through `views/notes/hooks.ts` (`setNoteHook('graph', …)`: the notes view puts the
local graph in its side panel and shows the graph button only while it is set) and adds the
command (Cmd/Ctrl+Shift+G) and Show in Graph for items. `model.ts` turns the plugin's `/data` into
what is shown (no DOM, unit-tested); `forceGraph.ts` draws it with force-graph; `notesGraph.ts` is the local graph
beside a note and the global graph in place of the editor. Both show a neighbourhood through
`buildView`'s `focus` and `depth`: the local graph around the open note (depth 1 or 2), the global
graph around the node it was opened for (`globalFocus`, depth 1, 2, 3 or 0 for everything, kept
with the other options in `localStorage` under `zc.graph.global`).

**`plugins/core/extended-markdown/figures/`: galleries and figures** (an extension of the Extended
Markdown core plugin, `plugins/extended-markdown`, with its own switch there). Its `index.ts`
installs the carousels' listeners (`galleryView.ts`) and sets the `figures` hook
(`hook.ts`) in `views/notes/hooks.ts`, which the notes code asks when it needs it: the renderer
adds `markdown.ts`'s rules (it rebuilds itself when a hook changes, `hooksVersion`), the live
preview asks `figuresOf` for numbers and labels, and the exports call `portable.ts` (Markdown)
and `exportStatic.ts` (HTML, PDF). `gallery.ts` reads a block and `figures.ts` finds and numbers
figures (no DOM, unit-tested). See [notes](notes.md#galleries).

**`ui/`: generic widgets.** `dialog.ts` (`openDialog`, `confirmDialog`, `promptDialog`),
`menu.ts` (`openMenu`, `closeMenu`, with keyboard navigation; an entry may show a colour `swatch`),
`colors.ts` (the palette of tags and notes folders), `toast.ts` (`toast`,
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
tool's colours and size. `inTab.ts` and `jumpHistory.ts` are for a viewer inside one of the app's
tabs (see [the app window](desktop.md)).

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
  `taskHooks` in the same file works the other way round: the tasks code
  (`plugins/core/tasks/noteTasks.ts`) fills it in, and the notes code calls it to draw and add tasks
  without importing the tasks code.
  `viewer/annotations/model.ts` does the same with `hooks.onSelect`, which
  `annotations/input.ts` sets to show its selection controls.
- **Dependency objects.** A module that needs a few things from its caller takes them as an
  argument: `attachCiteCards(container, deps: CiteCardDeps)` in `citeCard.ts`,
  `livePreview(cm, deps: PreviewDeps)` in `livePreview.ts`, `GraphHook.attach(ctx:
  NotesGraphContext)` (`views/notes/hooks.ts`). The module then does not import the notes view at all.
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
| `layout.css` | The shell: the tab bar, toolbar, sidebar, panes, resizers, status bar, narrow screens. |
| `items.css`, `itempane.css` | The items table; the item pane and the metadata review. |
| `settings.css`, `notes.css`, `tasks.css`, `graph.css` | Settings, the notes workspace, tasks (the Tasks page, the task form and popups, task lines in notes), the graph. |
| `export.css` | Not loaded by the app: the page and print rules of an exported note, added after `tokens.css`, `notes.css` and `tasks.css` (`views/notes/exportNote.ts`). |

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

The Tasks core plugin (`plugins/core/tasks/index.ts`) registers its page through `tasksPage.ts`
and installs the existing note `taskHooks`. On unload it removes its DOM, listeners, popups and
renderers and invalidates pending loads. Plugin startup completes before task deep links open;
plugin changes are applied before the change feed dispatches task or note refreshes.
