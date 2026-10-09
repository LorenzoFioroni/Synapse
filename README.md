# Zotero Clone

A local, web-based reference manager: a Flask backend with SQLite storage and a
dependency-free JavaScript frontend. Everything runs on your machine.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py                  # http://127.0.0.1:8000 opens in your browser
python server.py --port 5050 --no-browser
python server.py --data-dir ~/MyLibrary
```

Your library lives in `data/` (or `--data-dir` / `ZCLONE_DATA_DIR`):

| File | What |
| --- | --- |
| `library.db` | items, attachments, annotations |
| `fulltext.db` | extracted PDF text (FTS5); safe to delete, it is rebuilt |
| `pdfs/` | attached files |
| `notes/` | your Markdown notes (the files are the source of truth) |
| `notes_index.db` | links and search index of the notes; safe to delete, it is rebuilt |
| `citations.db` | reference lists fetched online (the citation graph); if deleted they can be fetched again |
| `backups/` | automatic note backups taken before a citation-key rename |
| `settings.json` | preferences from the Settings page |

Back up the `data/` folder to keep everything. It is git-ignored: code and data
never mix.

## Features

- **Search** in three scopes, picked from the search box (the default is a setting):
  *Title, Abstract & Authors* · *All Fields* · *All Fields & Full Text*.
  Terms are ANDed, `"exact phrase"` and `-exclude` work, and accents are ignored
  (`banuls` finds *Bañuls*). Full-text hits show a highlighted snippet.
- **Full-text index** of attached PDFs, kept in its own database so the library
  stays small. New attachments are indexed in the background; progress is shown
  in the sidebar and Settings, which can also rebuild it.
- **Import / export**: BibTeX, RIS, EndNote (tagged `.enw` and XML) and CSL JSON.
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
  with a section per article, word-level diffs with a checkbox per field, and the
  article's unchanged metadata below. Nothing is saved until you apply; replacing an
  existing citation key is opt-in. Lookups run one by one and can be stopped.
- **References / citation graph**: when metadata is fetched (Retrieve Metadata, Add by
  Identifier, dropped PDFs, the browser button) the list of papers the item cites is fetched
  too, in the background (Semantic Scholar, then OpenAlex, then Crossref; optional API keys
  in Settings → Online Services). The item pane shows *References* — cited works that are
  in your library link to them (matched by DOI, arXiv ID, or else title and year), the others
  can be added with one click — and *Cited by in your library*. Fetch them for any selection
  from the item menu or the multi-selection pane, with progress and a summary. The whole graph (library
  items + every work they cite, with "cites" edges) is at `GET /api/graph/citations`.
- **Find Available PDF** (single or batch) looks for open-access copies (Unpaywall,
  arXiv, Scholar) and checks the file is really the right paper. Batch runs skip items
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
- **Notes**: Markdown notes with live preview, KaTeX, code, Mermaid and images; `[@key]`
  citations and `[[links]]` between notes; a paper note per item. Switch with the
  *Library | Notes* toggle or Cmd/Ctrl+Shift+L; the browser's Back/Forward (mouse buttons,
  Cmd+[ / Alt+←) go back to the note or library selection you came from. The pencil column
  in the item list marks items with a paper note (blue) or cited in notes (grey).
  In the notes list: Cmd/Ctrl-click and Shift-click (or Shift+arrows) select several,
  Delete/Backspace moves them to the notes trash (with Undo), F2 renames, Enter opens,
  drag to move; folders can be renamed, moved and deleted the same way.
- Sortable, resizable, re-orderable columns; light/dark theme; compact density;
  keyboard navigation (see Settings → Library & Shortcuts).
- Built-in PDF viewer with highlights, underlines, text boxes and drawings
  (pdf.js is bundled, so it works offline).

## Project layout

```
./
├── server.py              CLI entry point
├── backend/
│   ├── app.py             Flask app factory (serves frontend/ and the API)
│   ├── config.py          paths of the data folder
│   ├── db.py              SQLite connection + schema migrations
│   ├── fields.py          item fields and types (single source of truth)
│   ├── repository.py      all SQL for items/attachments, duplicate detection
│   ├── organize.py        folders, tags, smart folders
│   ├── search.py          search scopes and query parsing
│   ├── fulltext.py        separate FTS5 index + background indexer
│   ├── citekeys.py        citation-key patterns
│   ├── settings.py        preferences store with validation
│   ├── metadata.py        Crossref / OpenAlex / arXiv / Open Library lookups
│   ├── references.py      reference lists (citation graph) in citations.db + background fetching
│   ├── pdf_finder.py      open-access PDF search + verification
│   ├── pdftext.py         PDF text extraction
│   ├── formats/           bibtex, ris, endnote (+XML), csljson
│   ├── notes/             Markdown notes: vault (files), frontmatter, parser (links and
│   │                      citations), index (notes_index.db), service
│   └── api/               Flask blueprints: items, attachments, settings, io, metadata,
│                          organize (folders/tags/smart), connector (browser extension), notes
├── frontend/
│   ├── index.html, viewer.html
│   ├── css/               tokens (themes), base, components, layout, items, item pane, settings
│   ├── js/                api, store, actions, ui/ (toast, dialog, menu), views/
│   └── vendor/pdfjs/
├── browser_extension/     Chrome extension (Manifest V3) – load unpacked
├── tools/                 build scripts for the vendored editor and Markdown bundles
├── tests/                 pytest suite (python -m pytest tests), e2e/ browser checklists
└── data/                  your library (git-ignored)
```

Notes are plain Markdown files in `data/notes/` (paper notes in `data/notes/papers/`,
images in `data/notes/attachments/`, deleted notes in `data/notes/.trash/`). The files are
the source of truth; `data/notes_index.db` is derived from them and can be deleted at any
time — it is rebuilt on the next start.

The frontend never talks to external services directly; all lookups go through
the backend, and all SQL lives in `repository.py`.

## Tests

```bash
pip install pytest
python -m pytest tests          # backend + JavaScript unit tests (the JS part needs Node.js)
```

Browser checklists for notes (need Playwright; each uses a throw-away data folder):

```bash
pip install playwright requests && playwright install chromium
python tests/e2e/notes_checklist.py      # editor, saving, conflicts, files
python tests/e2e/render_checklist.py     # live preview and read mode (tests/e2e/kitchen-sink.md)
python tests/e2e/links_checklist.py      # autocomplete, citation cards, side panels, item pane
python tests/e2e/library_checklist.py    # key rename, notes in search, quote/snapshot from the PDF viewer
python tests/e2e/usability_checklist.py  # multi-select, Delete + Undo, folders, Back/Forward, item-list markers
```

The notes editor (CodeMirror 6) is vendored as a pre-built bundle in `frontend/vendor/cm6/`;
to rebuild it from the pinned versions: `cd tools/cm6 && npm ci && ./build.sh`. The renderer
(markdown-it, KaTeX, Mermaid) likewise lives in `frontend/vendor/md/` and `frontend/vendor/mermaid/`
(`tools/md`).
