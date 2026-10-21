# Testing

Synapse has three kinds of tests:

| Kind | Where | Runs |
|---|---|---|
| Python unit and API tests | `tests/`, `tests/notes/` | `python -m pytest -q`, in CI |
| JavaScript unit tests | `tests/js/*.test.mjs` | by pytest with Node.js, in CI |
| Browser checklists | `tests/e2e/*_checklist.py` | by hand, with Playwright |

The first two are fast and need no network and no browser. The checklists drive the real app in
Chromium and cover what only a page can show: drawing, dragging, touch, several tabs.

## pytest

`pyproject.toml` sets `testpaths = ["tests"]` and keeps pytest out of `tests/e2e`, `frontend`,
`tools` and `.venv` (`norecursedirs`). The checklists are scripts, not pytest tests.

| Folder | Holds |
|---|---|
| `tests/` | One module per area, `test_<area>.py`. |
| `tests/notes/` | The notes tests. `tests/notes/fixtures/` has Markdown notes with a hand-written `.json` of what the parser must find in each. |
| `tests/fixtures/` | `equations.pdf` (built from `equations.tex` beside it) and `legacy_annotations.json` (annotations saved by the old viewer). |
| `tests/js/` | The JavaScript tests, and `scan_dump.mjs`, a helper for the scanner parity test. |
| `tests/e2e/` | The browser checklists, `_guard.py`, `run.py`, and `fixtures/` (`article.html`, `twocolumn.pdf` and its `.tex`, `tall.png`, `wide.png`). Some unit tests use these fixtures too. |

### Fixtures and helpers (`tests/conftest.py`)

- `app` builds a fresh app on a throw-away data folder:
  `create_app(Config.from_env(tmp_path / "data"), start_indexer=False)`. It turns off background
  reference fetching and automatic PDF finding (`references_auto`, `add_fetch_references`,
  `add_find_pdf`), because that work would outlive the test. A test that needs it turns the
  setting on itself.
- `client` is `app.test_client()`; `svc` is the `Services` object (`app.extensions["synapse"]`),
  for reaching components directly.
- `_no_arxiv_spacing` (autouse) sets `backend.online.http.ARXIV_SPACING` to 0, so tests do not
  wait between faked arXiv requests.
- `make_pdf(path, text)` writes a tiny one-page PDF containing `text`, with no dependencies. It is
  a plain function: `from tests.conftest import make_pdf`.
- `built_frontend` (session scope) makes sure `frontend/js` is built, by calling `ensure_built`
  from `backend/frontend.py`, and returns its path. It skips the test when Node.js is missing or
  the frontend cannot be built.

`conftest.py` puts the repository root on `sys.path`, so tests import `backend` and `tests` as
packages. Tests never use the network: they replace `requests.get` in the module under test with
`monkeypatch` (`test_pdf_finder.py`, `test_metadata.py`, `test_references.py`), or fake DNS as
well (`test_remote_pdf.py`).

### Test modules by area

| Area | Modules |
|---|---|
| Library | `test_api.py` (create, edit, trash, settings, import and export, attachments, schema), `test_list_payload.py` (lean lists, items by id), `test_organize.py` (folders, tags, smart folders, their migrations), `test_citekeys.py`, `test_search.py` (query syntax, accents, filters, notes in search), `test_identifiers.py` (DOI and arXiv ids), `test_annotations.py` (annotation records, conversion of old ones) |
| Formats | `test_formats.py`: BibTeX, RIS, EndNote and CSL-JSON round trips |
| Online and references | `test_metadata.py` (lookups), `test_pdf_finder.py` (Find Available PDF), `test_remote_pdf.py` (PDFs behind a web link: public addresses only, PDFs only, size cap), `test_references.py` (reference lists), `test_graph.py` (`/api/graph`) |
| Notes | `tests/notes/`: `test_vault.py` (safe paths, save conflicts, trash, move), `test_index.py`, `test_parser.py` (golden tests on the fixtures), `test_frontmatter.py`, `test_gallery.py` (gallery blocks for the MCP tools), `test_rewrite.py`, `test_rekey.py`, `test_attachments.py`, `test_api_notes.py`, `test_scanner_parity.py` |
| Access and sync | `test_guard.py` (other websites and extensions refused), `test_devices.py` (pairing, who manages devices, `--debug` refused on the network), `test_changes.py` (change feed), `test_connector.py` (browser extension API), `test_backup.py` |
| MCP | `test_mcp.py`: the protocol and every tool, in-process and over stdio |
| Frontend | `test_frontend_build.py` (`needs_build` and `ensure_built`), `test_frontend_js.py` (runs the JavaScript tests) |
| Architecture | `test_layers.py`, `test_offline.py`, `test_branding.py` (below) |

`tests/notes/test_scanner_parity.py` deserves a note. `frontend/src/views/notes/mdscan.ts` is a
port of the backend's note parser, used only for display. The test runs `tests/js/scan_dump.mjs`
(the built `mdscan.js`) and the Python parser on every fixture and on random documents, and
requires the same results, so the two cannot drift apart. It needs Node.js.

### Architectural tests

These check rules of the code base rather than behaviour:

- **`test_layers.py`** parses every module in `backend/` and fails on an import against the
  layers in [architecture](architecture.md#layers). `ALLOWED` lists what each package may import;
  `test_every_unit_is_listed` fails when a new package is missing from it. Change `ALLOWED` and
  the diagram in `architecture.md` together.
- **`test_offline.py`** scans the frontend's own `.html`, `.css`, `.ts` and `.js` files (and the
  vendored `.html` and `.css`) for anything that *loads* from the web: a remote `src` or `href`,
  `url(https://…)`, `@import`, a remote `import`, `importScripts` or worker. Links the user clicks
  are fine. `test_the_check_catches_remote_loads` makes sure the patterns still match. A library
  must be vendored under `frontend/vendor/` instead.
- **`test_branding.py`** keeps one name and one icon: no old name in user-facing files, the page
  title, the extension's name and ping, the data folder variable (`SYNAPSE_DATA_DIR`, with the old
  name still accepted), and valid icon files at the right sizes.

## JavaScript unit tests

`tests/js/*.test.mjs` test the frontend logic that needs no page: they use Node's built-in runner
(`node:test`) and `node:assert/strict`, and import the *built* modules from `frontend/js/`.

| Test | Covers |
|---|---|
| `citeTarget.test.mjs` | `views/notes/citeTarget.js` and `viewer/locate.js`: where a citation points in a PDF, finding a quote |
| `diff.test.mjs` | `views/notes/diff.js` |
| `embed.test.mjs` | `views/notes/embed.js` and `linkData.js`: what an embedded note shows, with its own link data |
| `equations.test.mjs` | `equations.js`, `mathClick.js`, `macros.js` and math in `render.js` |
| `figures.test.mjs` | `views/notes/figures.js`: which images are figures, numbering, labels; figures in `render.js` and the Markdown export |
| `crossref.test.mjs` | `views/notes/crossref.js`: `\ref`, `\eqref`, `\cref`, `\Cref`: kinds, plurals, ranges, unknown names |
| `gallery.test.mjs` | `views/notes/gallery.js`: reading gallery blocks, the figure caption, Markdown export; galleries in `render.js` |
| `extract.test.mjs` | `extractCitation` in `browser_extension/extract.js`, with a faked `document` and `location` |
| `graph.test.mjs` | `views/graph/model.js` |
| `library.test.mjs` | `views/notes/library.js`: autocomplete ranking |
| `linkNav.test.mjs` | `viewer/linkNav.js`: link targets and previews in the PDF viewer |
| `lookupJob.test.mjs` | `LookupJob` in `views/metadataReview.js` |
| `noteSession.test.mjs` | `views/notes/noteSession.js`: saving, conflicts, drafts |
| `render.test.mjs` | `views/notes/render.js` |
| `store.test.mjs` | `sortedItems` in `store.js`, `hitBadge` in `util.js` |
| `tabs.test.mjs` | `tabs/model.js`: the app's tabs (opening, reuse, closing and where it goes, reopening, order, saving and restoring; note tabs: the preview tab, keeping, renamed and deleted notes, what a dragged tab may be); `viewer/jumpHistory.js`; `windows.js`: the address of a new window |

`embed.test.mjs`, `equations.test.mjs`, `figures.test.mjs`, `gallery.test.mjs` and `render.test.mjs` load the real markdown-it and KaTeX from
`frontend/vendor/md/md.js`.

**How pytest runs them.** `tests/test_frontend_js.py` has one test, `test_js_units`. It takes the
`built_frontend` fixture (which builds `frontend/js` from `frontend/src` when it is out of date),
then runs `node --test` on all the files at once and fails with the end of Node's output. Without
Node.js the test is skipped. To run them alone:

```bash
(cd frontend && npm run build)
node --test tests/js/*.test.mjs
```

## Browser checklists

Each `tests/e2e/<name>_checklist.py` is a script that starts its own server on a fixed port with a
throw-away data folder (`tempfile.mkdtemp`), so your library is never touched. It drives Chromium
with Playwright and prints `PASS` or `FAIL` for each check, then `n/m checks passed`. It exits
with status 1 when a check failed.

**Requirements.** `pip install playwright` and, once, `playwright install chromium`. The
checklists also use `requests` (in `requirements.txt`). They run the built frontend; a checklist
that starts the app through `server.py` builds it, but some start `create_app` in a small script
of their own (to fake online services, for example), so build first
(`cd frontend && npm run build`).

**Running.**

```bash
python tests/e2e/notes_checklist.py                 # one checklist
SHOTS=/tmp/shots python tests/e2e/graph_checklist.py   # and save screenshots
python tests/e2e/run.py                             # all, one after another, with a summary
python tests/e2e/run.py notes render --retries 1    # some, retrying a failed one once
```

`SHOTS` names a folder for screenshots; the checklists that take them say so in their docstring.
`run.py` runs the checklists one at a time (`--list` prints their names), shows the last lines of
a failed one, and exits with status 1 if any failed.

**`_guard.py`.** Every checklist calls `guard(PORT)` before it starts its server. It refuses to
run when the port is already in use, since a server already listening there would be someone
else's data folder. From then on it tracks every process started through `subprocess.Popen` and
kills those still running when the script exits, so a checklist that stops on an error leaves no
server behind.

**The extension checklist** loads `browser_extension/` unpacked with
`launch_persistent_context(..., channel="chromium")`. Extensions need the full Chromium in its new
headless mode; the default headless shell cannot load them.

**Timing-sensitive checks.** "The save is answered at once, even after racing through the list"
in `list_perf_checklist.py` (a save must be answered within 2 s) sometimes fails on a slow or busy
machine and passes on a second run. Use `run.py --retries 1`, or run the checklist again, before
looking for a bug.

| Checklist | Port | Covers |
|---|---|---|
| `adding` | 8771 | Retrieve Metadata in bulk (side by side, review, background) and Settings → Adding Items, with slow fake lookups |
| `annotations` | 8777 | Each annotation kind saved on its own, redrawn after reload and zoom; eraser, Delete, undo and redo; two PDFs and two tabs; old annotations converted |
| `assist` | 8793 | Autocompletion: nothing asked without Ollama or the model, Settings → Autocompletion, ghost text keys and where it never appears (a stand-in for Ollama, `tests/fake_ollama.py`) |
| `attachments` | 8772 | Note attachments: the attachment trash, the Attachments dialog, retention, deletion dates |
| `backups` | 8775 | Settings → Backups: backup at start, Back Up Now, changing the folder, Restore |
| `branding` | 8776 | The toolbar icon in light and dark theme, the tab title, the favicons |
| `changes` | 8784 | Computer and iPad each see the other's changes (items, notes, annotations) without a reload; typing is not interrupted |
| `citation_spot` | 8791 | A quote's or snapshot's citation opens the PDF at the exact spot and flashes it |
| `claude_edits` | 8785 | An edit by the real MCP server appears in the open note; Undo, Show changes, restoring a version |
| `devices` | 8781 | Pairing under `--lan`: the unpaired page, the QR code, pairing, renaming and removing a device |
| `equations` | 8786 | Editing notes with equations |
| `export` | 8792 | Exporting a note: a self-contained HTML page that works offline, portable Markdown, PDF through the print dialog |
| `extension` | 8789, 8790 | The browser extension: options page connects, the popup reads an article's citation tags and saves it with a tag |
| `gallery` | 8794 | Figure numbers and `\cref` (read mode, live preview, exports); galleries: the carousel in read mode and the live preview, editing its source, autocomplete in captions, exported as one figure with panels (HTML, PDF, Markdown) |
| `graph` | 8787 | Local and global graph in Notes, Show in Graph, Cmd/Ctrl+Shift+G, Back/Forward |
| `lazy_viewer` | 8778 | A 300-page PDF: pages drawn only near the viewport, fast jumps and zoom, annotations on far pages |
| `library` | 8768 | Citation key rename with preview, notes in search, trashed items, Quote and Snapshot to note |
| `links` | 8767 | Note links: autocomplete, citation cards, side panels, the item pane |
| `list_perf` | 8779 | 10,000 items: fast list, few rows in the DOM, quick selection, small changes without a full reload |
| `multipdf` | 8788 | Items with several PDFs: citations, cards and menus |
| `notes` | 8765 | The notes editor |
| `passkeys` | 8794 | Over HTTPS: adding a passkey, signing an unpaired iPad in with it (Chromium's virtual authenticator), removing it; Settings → AI Access makes a token that reaches `/mcp` from another computer, and removing it locks the app out |
| `pdf_tabs` | 8773 | One viewer tab per PDF; Cmd/Ctrl+click or middle click opens a new one |
| `remote_pdf` | 8774 | Attachments stored as web links open through `/api/attachments/<id>/remote`; local-network links refused |
| `render` | 8766 | Note rendering (uses `tests/e2e/kitchen-sink.md`) |
| `tabs` | 8796, 8797 | The app's own tabs: a PDF opens in a tab, one per PDF, pointed at a cited page; Ctrl+Tab and Alt+W/1–9/⇧T from inside a PDF; closing goes back to where it was opened; middle click, dragging, the tab menu; Back/Forward inside a PDF tab stay inside it; tabs come back after a reload; the first tab (Library or Tasks) and the switch; note tabs (preview, kept by double-click on the tab or in the list or by editing, undo kept across tabs, each tab's own mode, switching back without waiting for the server, renamed and deleted notes, reload); folder colours on a note's tab and the title bar (neutral by default, and in the dark theme); each window with its own tabs; a tab dropped on another window or outside every window; Move to New Window, New Window, Open in New Window from the menus; Settings → Open PDFs in → Browser Tabs. Then a `--quit-when-closed` server stops once its page closes |
| `tasks` | 8795 | Tasks: task lines in a note (ticking, the task popup, the New task popup with a new or existing task, the `{{task` autocomplete, a wrapped task on an indented line, ticking a parent, Backspace), read mode, the side panel, the Tasks page (views, search, quick add, keyboard, delete and Undo, changes from elsewhere) |
| `touch_app` | 8783 | The library on an iPad profile: drawer, long press for menus and cards, hover tools shown, resizing, Home Screen |
| `touch` | 8782 | The PDF viewer on an iPad profile: pen tools, finger scrolling, pinch zoom, snapshots |
| `usability` | 8769 | Several notes selected, delete with Undo, folder operations, Back/Forward, the Library/Notes switch, note markers |
| `viewer_links` | 8770 | Links in the PDF viewer: jumps, external links, hover previews, Back/Forward |
| `zoom` | 8780 | Zoom keeps the spot under the pointer in place; +/- keep the middle |

`citation_spot`, `links`, `multipdf` and `pdf_tabs` check PDFs opening in browser tabs, so they
write `{"pdf_open_in": "browser"}` into their data folder's `settings.json` before starting;
`tabs` covers the app's own tabs.

Each checklist has a port of its own, so a new one needs an unused port. The touch checklists
simulate Pencil, palm and pinch input; the real behaviour still needs a try on an iPad.

## CI

`.github/workflows/ci.yml` runs on every push to `main` and every pull request, on Ubuntu with
Python 3.10 and 3.13 and Node.js 22:

1. `npm ci`, `npm run check` (type check) and `npm run build` in `frontend/`;
2. `pip install -r requirements-dev.txt`;
3. `ruff check .`, `ruff format --check .` and `mypy`;
4. `python -m pytest -q --cov`, which includes the JavaScript tests and prints the coverage
   report in the log (nothing is uploaded).

The browser checklists do not run in CI.

## Coverage

`pytest-cov` is in `requirements-dev.txt`, and `pyproject.toml` configures it
(`[tool.coverage.run]`, `[tool.coverage.report]`): `python -m pytest --cov` measures branch
coverage of `backend/` and lists the missing lines of each file that is not fully covered, least
covered first. `--cov-report=html` also writes `htmlcov/`. Only the pytest suite counts; the
browser checklists and the frontend are not measured.

## Lint and format

Ruff is configured in `pyproject.toml`: line length 120, target Python 3.10, and
`frontend/vendor` and `tools/*/node_modules` excluded. The lint rules are `F` (pyflakes), `E9`
(syntax errors), `W` (whitespace), `B` (bugbear), `UP` (pyupgrade) and `I` (import order, with
`backend` as first-party). `B023` and `B905` are ignored, and `tests/test_formats.py` may have
trailing spaces (`W291`) because RIS records end with `"ER  - "`. `ruff check --fix .` fixes most
findings and `ruff format .` formats.

## Type checking

The frontend and the browser extension are type-checked with `tsc` (`npm run check`; see
[frontend](frontend.md)). The Python code is type-checked with **mypy**, configured in
`pyproject.toml` (`[tool.mypy]`): it checks `backend/`, `server.py` and `mcp_server.py`. Code
without annotations is not checked inside, so annotations can be added one module at a time, but
everything that is annotated must type-check. Two things it asks for that are easy to forget: an
empty container that will be filled needs its type (`out: list[str] = []`), and a value that may
be `None` (a regex match, `dict.get`) must be checked before use. A class with a method named
`list` must write the built-in type as `builtins.list[...]` in its annotations.

`.pre-commit-config.yaml` runs `ruff check --fix`, `ruff format`, mypy and the frontend type
check on each commit, once installed with `pre-commit install`. It uses the tools already
installed, so committing needs no network.

## Before committing

From `CLAUDE.md`:

```bash
pip install -r requirements-dev.txt
(cd frontend && npm ci && npm run check)   # the frontend and the browser extension
ruff check .
ruff format .
mypy
python -m pytest -q
```

Then run the browser checklists that cover the UI you changed, for example
`python tests/e2e/run.py notes render`.

## Writing tests

**A Python test.** Put it in the module for its area, or a new `tests/test_<area>.py`. Test a
component directly when you can: build it on `tmp_path`, or reach it through the `svc` fixture.
Test an endpoint through `client`. Fake every network call with `monkeypatch` as the existing
tests for `online/` and `references/` do; a test must pass offline. Use `make_pdf` when you need a
PDF with known text.

**An architectural rule.** A new backend package goes into `ALLOWED` in `tests/test_layers.py`
and into the layers diagram in `architecture.md`. New frontend code needs nothing: `test_offline.py`
scans it already.

**A JavaScript test.** Keep the logic in a module that does not touch the DOM when it is imported
(the tested modules say so at the top, for example "No DOM here"). Add
`tests/js/<name>.test.mjs`:

```js
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { changedSpan } from '../../frontend/js/views/notes/diff.js';

test('changedSpan replaces only what differs', () => {
  assert.equal(changedSpan('same', 'same'), null);
  assert.deepEqual(changedSpan('abc', ''), { from: 0, to: 3, insert: '' });
});
```

(from `diff.test.mjs`). The import names the built `.js` file, so build before running the test
on its own.

pytest picks it up through the `*.test.mjs` glob in `test_frontend_js.py`.

**A browser checklist.** Copy a short one, such as `branding_checklist.py`, and keep its shape:

- a docstring saying what it covers, how to run it and which port it uses;
- an unused `PORT`, and `guard(PORT)` before the server starts;
- a data folder from `tempfile.mkdtemp`, and `server.py --port … --no-browser --data-dir …`
  (or `create_app` in a script of its own when online services must be faked);
- `check(name, cond, extra)` for each check, page errors collected with `page.on("pageerror", …)`
  and checked at the end;
- `n/m checks passed` printed last and `sys.exit(0 if all(results) else 1)`, which `run.py`
  reads.

Name it `<name>_checklist.py` so that `run.py` finds it, and add it to the table above. Read state
through the `window.zc…` hooks in `frontend/src/globals.d.ts` rather than through the DOM where
one exists.
