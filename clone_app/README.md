# Zotero Clone

A local, web-based reference manager: a Flask backend with SQLite storage and a
dependency-free JavaScript frontend. Everything runs on your machine.

## Run it

```bash
cd clone_app
pip install -r requirements.txt
python server.py                  # http://127.0.0.1:8000 opens in your browser
python server.py --port 5050 --no-browser
python server.py --data-dir ~/MyLibrary
```

Your library lives in `clone_app/data/` (or `--data-dir` / `ZCLONE_DATA_DIR`):

| File | What |
| --- | --- |
| `library.db` | items, attachments, annotations |
| `fulltext.db` | extracted PDF text (FTS5); safe to delete, it is rebuilt |
| `pdfs/` | attached files |
| `settings.json` | preferences from the Settings page |

Back up the `data/` folder to keep everything. Libraries from the old layout
(`papers.db`, `pdf_storage/`) are moved into `data/` automatically on first start.

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
- **Find Available PDF** (single or batch) looks for open-access copies (Unpaywall,
  arXiv, Scholar) and checks the file is really the right paper. Batch runs skip items
  that already have a PDF, show progress, and can select the ones that weren't found.
- **Folders** (nested; an item can be in several folders but is stored once — a folder
  only holds a link to it), **tags** (many per item, optional colours) and **smart
  folders** (saved searches). Drag items onto a folder to add them (hold Shift to move
  them out of the folder you're in), onto a tag to tag them, onto Unfiled to take them
  out of every folder. Inside a folder, Delete removes items from that folder only;
  Cmd/Ctrl+Delete moves them to the Trash.
- **Search filters** combine with text and can be negated with `-`:
  `tag:review`, `tag:"open systems"`, `folder:Physics`, `folder:"Physics/Transport"`,
  `folder:none`, `type:book`, `year:2020`, `year:2018-2022`, `year:>2015`, `has:pdf`,
  `author:cirac`, `title:…`, `journal:…`, `key:…`. Save any search as a smart folder.
- **Cmd/Ctrl+C** copies the BibTeX of the selected items (Cmd+Shift+C: citation keys,
  Cmd+T: edit tags).
- **Browser button** (`browser_extension/`, see its README): saves the article you are
  reading — DOI/arXiv pages, publisher pages, PDFs — with folder, tags and the PDF.
- **Trash** with Undo, **Duplicate Items** view with "keep this one, trash the others".
- Sortable, resizable, re-orderable columns; light/dark theme; compact density;
  keyboard navigation (see Settings → Library & Shortcuts).
- Built-in PDF viewer with highlights, underlines, text boxes and drawings
  (pdf.js is bundled, so it works offline).

## Project layout

```
clone_app/
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
│   ├── pdf_finder.py      open-access PDF search + verification
│   ├── pdftext.py         PDF text extraction
│   ├── formats/           bibtex, ris, endnote (+XML), csljson
│   └── api/               Flask blueprints: items, attachments, settings, io, metadata,
│                          organize (folders/tags/smart), connector (browser extension)
├── frontend/
│   ├── index.html, viewer.html
│   ├── css/               tokens (themes), base, components, layout, items, item pane, settings
│   ├── js/                api, store, actions, ui/ (toast, dialog, menu), views/
│   └── vendor/pdfjs/
├── browser_extension/     Chrome extension (Manifest V3) – load unpacked
├── tests/                 pytest suite (python -m pytest tests)
└── data/                  your library (git-ignored)
```

The frontend never talks to external services directly; all lookups go through
the backend, and all SQL lives in `repository.py`.

## Tests

```bash
pip install pytest
python -m pytest tests
```
