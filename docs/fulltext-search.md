# Full-text search

This page covers how text gets out of the PDFs, how the full-text index stores it, and how the
library search uses it together with the item metadata and the notes. For where the packages sit,
see [architecture](architecture.md); for the files on disk, see [storage](storage.md).

## Extracting text from PDFs

`backend/pdf/text.py` turns a PDF into text. It uses PyMuPDF (`fitz`) when it is installed,
because it is faster and more accurate, and falls back to `pypdf`, which is always installed
(`requirements.txt` lists PyMuPDF as optional). If PyMuPDF fails on a file, `pypdf` gets a try.

| Function | Returns | Used by |
|---|---|---|
| `extract_text(path, max_pages=None)` | `(text, page_count)` | the full-text indexer (whole file); identifier detection and PDF verification (`max_pages=2`) |
| `extract_pages(path)` | one cleaned string per page | the MCP tools (`backend/mcp/tools/common.py`) |
| `clean_text(text)` | normalised text | the indexer, `extract_pages`, identifier detection |
| `find_identifier(text)` | `{"doi": …}`, `{"arxiv": …}` or `{}` | items made from dropped PDFs and from the browser extension |

None of these raise: a broken file gives `("", 0)` or `[]`, and the reason is logged.

`clean_text` applies NFKC normalisation (which also splits ligatures such as "ﬀ" into "ff"),
removes NUL characters, rejoins words hyphenated across a line break when the next line starts in
lower case, and collapses runs of spaces and tabs. It keeps line breaks.

`find_identifier` looks only at the first two pages. It returns the first DOI it finds (trailing
punctuation stripped), or else an arXiv id written as `arXiv:NNNN.NNNNN`. It is a best guess used
to look the item up online.

## The full-text index

`FulltextIndex` in `backend/pdf/fulltext.py` keeps the text in `data/fulltext.db`. It is a
separate file so extracted text never bloats `library.db`, and so it can be deleted at any time
and rebuilt from the PDFs.

### Tables

| Table | Holds |
|---|---|
| `documents` | One row per indexed attachment: `attachment_id` (primary key), `paper_id`, `file`, `mtime`, `size`, `pages`, `chars`, `indexed_at`. |
| `content` | An FTS5 table with `text`, and `attachment_id` and `paper_id` as unindexed columns. Tokenizer `unicode61 remove_diacritics 2`, so matching ignores case and accents. |
| `content_plain` | The fallback when SQLite has no FTS5: `attachment_id`, `paper_id`, `text`. |

`_init` tries to create `content`. If SQLite raises because FTS5 is missing, it logs a warning,
sets `fts = False` and uses `content_plain` instead. `_table` names whichever is in use, so the
write code is the same for both. `stats()` reports `fts5`, and Settings shows
"FTS5 unavailable (slower fallback)" when it is false.

### Writing

- `needs_indexing(att_id, path)` compares the file's `mtime` and `size` with the `documents` row.
  A file is extracted again only when one of them changed.
- `index_file(att_id, paper_id, path)` extracts and cleans the whole file, then replaces the
  attachment's rows in one transaction. A file with no extractable text (a scan, or a broken
  file) still gets a `documents` row with empty text, so it is not retried on every run.
- `remove(att_ids)` deletes rows. The API calls it when an attachment is deleted and when items
  are deleted for good or the Trash is emptied (`backend/api/attachments.py`,
  `backend/api/items.py`). Items in the Trash stay indexed, so a search in the Trash view still
  finds them by their text.
- `clear()` empties both tables and runs `VACUUM`, so the file shrinks.

Writes take `_write_lock`, because the indexer thread and request threads can both write.

### The background indexer

`Indexer` keeps the index in step with the attachments in a background thread named
`fulltext-indexer`. Each run (`_run`):

1. Removes rows for attachment ids that no longer exist.
2. Collects the attachments whose file is on disk, ends in `.pdf`, and `needs_indexing`.
   Web-link attachments have no file and are skipped.
3. Indexes them one by one. An exception on one file is logged and counted in
   `status["errors"]`; the run goes on.

`sync()` starts a run. If a run is already going, it sets `_again` instead, and the thread does
one more run when it finishes. Many calls in a row therefore cost at most one extra run.
`rebuild()` is `clear()` followed by `sync()`.

`Indexer.status` (`running`, `total`, `done`, `current`, `last_run`, `errors`) is returned with
the index's `stats()` by `GET /api/stats`, which the sidebar and Settings use to show progress.

What starts a run:

| When | Where | Needs `fulltext_auto_index` |
|---|---|---|
| The web app starts | `create_app` | yes |
| A file is attached (upload, dropped PDF, browser extension, PDF found online) | `Services.attachments_changed` | yes |
| A backup is restored | `Backups._after_restore` | yes |
| `POST /api/fulltext/sync` | `backend/api/items.py` | no |
| `POST /api/fulltext/rebuild` (Settings → Rebuild Index) | `backend/api/items.py` | no |

The MCP server never starts the indexer. It reads `fulltext.db` for search and for page counts.

## The library search

`search()` in `backend/library/search.py` filters a list of items in Python. It does not query
SQLite: the API loads the items with `PaperRepository.list()` and passes them in, along with the
full-text index and, optionally, a notes matcher. Searching in memory keeps the query language
in one place and lets one query mix metadata, PDF text and notes.

### Modes

The mode decides where plain terms are looked for. The labels come from `SEARCH_MODES` in
`backend/settings.py`; the default is the `default_search_mode` setting.

| Mode | Label | Plain terms match |
|---|---|---|
| `basic` | Title, Abstract & Authors | fields with `basic_search=True` in `backend/library/fields.py` |
| `all` | All Fields | fields with `searchable=True`, attachment names, tags, and the notes |
| `fulltext` | All Fields & Full Text | everything in `all`, plus the text of the PDFs |

Field filters (below) work the same in every mode. The field lists come from `FIELDS`, so a new
field is searchable without touching the search code.

### Query syntax

`parse_query()` splits the query into terms. Every term must match (AND).

| Syntax | Meaning |
|---|---|
| `word` | Must appear somewhere in the mode's scope. |
| `"two words"` | A phrase: kept together as one term. |
| `-word`, `-"two words"`, `-tag:x` | Must not match. Any term or filter can be negated. |
| `tag:quantum`, `tag:"open systems"`, `tag:none` | Has this tag (whole name), or no tags. `tags:` is the same. |
| `folder:Transport` | In a folder with this name, at any depth. |
| `folder:"Physics/Transport"` | In a folder with this path, starting at the top level. |
| `folder:none`, `folder:unfiled` | In no folder. |
| `type:book`, `type:journal` | A whole type key or label (`book`, `"book section"`) means that type only. Anything else matches types whose key or label starts with the value, or whose label contains it. |
| `year:2020`, `year:2018-2022`, `year:>2015`, `year:<=2000` | By year (the first four digits of `year`). |
| `has:pdf` (or `attachment`, `file`), `has:tag`, `has:folder`, `has:<field>` | Has any attachment, tag or folder, or a non-empty field. |
| `author:`, `title:`, `journal:`, `doi:`, `key:` | The value appears in that field (`key` is `citation_key`). |

Prefixes not in `FILTER_KEYS` are not filters: `arXiv:2205.03415` is searched as plain text.

Matching ignores case and accents: `normalize()` decomposes the text (NFKD), drops combining
marks and case-folds it, so `banuls` finds "Bañuls". Metadata, filter values and notes are
matched as substrings of the normalised text.

### How a query runs

1. Filters are applied first, on the item fields (`_filter_matches`). Folder paths and type
   labels come from `ctx` (`search_context` in `backend/api/items.py`).
2. In `fulltext` mode, each plain term is looked up in the full-text index with
   `FulltextIndex.match(term)`, which returns `{paper_id: snippet}`.
3. In `all` and `fulltext` modes, each term is also looked up in the notes matcher. PDF snippets
   win over note snippets for the same item.
4. Each item's metadata is joined into a normalised haystack (`_build_haystack`), and each term
   must be in the haystack or in that term's text matches. Fields are joined with a `\u0001`
   separator, so a phrase cannot match across two fields. Haystacks are cached in `_hay_cache`,
   keyed by id, `date_modified`, mode, number of attachments and tags.

Each term is matched on its own, so in one query one word can match the title and another the
PDF.

### Matching in the PDF text

With FTS5, `match()` quotes the term (`_fts_quote`). A single word becomes a prefix query
(`"lind"*` finds "Lindblad"); a phrase must appear as written. Results are ordered by FTS5's
`rank`, at most 2000 per term, and the snippet is FTS5's `snippet()` with about 14 tokens. Note
that FTS5 matches whole tokens or their beginnings, while metadata is matched anywhere in a word.

Without FTS5, `match()` uses `LIKE '%term%'` on `content_plain` and cuts a snippet of 60
characters on each side of the first match. This is slower, and SQLite's `LIKE` ignores case
only for ASCII letters and does not ignore accents.

Both paths mark the match with private control characters, escape the text with `html.escape`,
and only then turn the marks into `<mark>` and `</mark>`. PDF text can therefore never inject
HTML.

### What GET /api/items returns

`GET /api/items` takes `view`, `q`, `mode` and `tags` (a comma-separated list; an item must have
all of them). It answers:

```json
{"items": [...], "hits": {"<item id>": {"fulltext": "…<mark>lindblad</mark>…"}},
 "counts": {...}, "mode": "fulltext", "query": "lindblad", "sidebar": {...}}
```

`hits` has an entry only for items with a text snippet. `search()` picks it from the first term
that matched only in the PDF or notes text; if every term also matched the metadata, it still
shows the first text match in `all` and `fulltext` modes, for context. A snippet from a note
starts with `<span class="hit-src">Note · <title></span>`. Despite the key name `fulltext`,
`hits` holds note snippets too. The browser shows the snippet in the items table
(`frontend/src/views/itemlist/rows.ts`) and the item pane.

### Matching through notes

`NoteTextMatcher` in `backend/notes/service.py` lets the search find an item by what the notes
say about it: the body of its paper note, and each line in another note that cites it
(`NotesIndex.texts_by_key`). `NotesService.text_matcher()` caches it until the notes index or
the citation keys change. `NotesIndex.signature()` tells when the index changed: it combines a
counter bumped by this process with the size, modification time and SQLite change counter of
`notes_index.db`, so a note the MCP server edits in its own process is searched as it is now. `GET /api/items` uses it only while the `notes_enabled` setting is on.

## Smart folders

A smart folder is a saved query: a row in `smart_folders` with `query` and `mode`, managed by
`SmartFolderRepository` in `backend/library/organize.py` (the query must not be empty and the
mode must be one of `MODES`). It uses the same `search()` and query language.

For `view=smart:<id>`, `items_for_view` returns the whole library (not the Trash) with the
folder's query and mode. `GET /api/items` runs that query first, then the `tags` filter, then
the query typed in the search box with the current mode, and merges the snippets.
`GET /api/search/preview` counts the matches while the user edits a smart folder. It searches
with the notes matcher too (`notes_matcher` in `backend/api/items.py`, shared with
`GET /api/items`), so the count is what the folder will show.

## Search elsewhere

- **MCP:** `search_library` in `backend/mcp/tools/library.py` calls the same `search()` (mode
  `fulltext`, or `all` when `fulltext=False`) and sorts the results by title matches, then year.
  See [MCP](mcp.md).
- **Notes:** `GET /api/notes/search` searches only the notes, through the FTS5 table
  `notes_fts` in `notes_index.db` (`NotesIndex.search`). Each word is a prefix query, and the
  query language above does not apply. See [notes](notes.md).

## Where to change things

| To change | Edit |
|---|---|
| Which fields a mode searches | `basic_search` and `searchable` on the field in `backend/library/fields.py` |
| The query syntax or a filter | `parse_query`, `FILTER_KEYS`, `FIELD_ALIASES` and `_filter_matches` in `backend/library/search.py`; update the module docstring, this page and `README.md` |
| How terms combine, or which snippet is shown | `search()` in `backend/library/search.py` |
| The modes or their labels | `MODES` in `backend/library/search.py` and `SEARCH_MODES` in `backend/settings.py` |
| PDF text extraction or cleaning | `backend/pdf/text.py`; then rebuild the index to see the effect on old files |
| The full-text query, ranking, snippet length or fallback | `FulltextIndex.match` in `backend/pdf/fulltext.py` |
| The layout of `fulltext.db` | `FulltextIndex._init`; old files need clearing (see [storage](storage.md#changing-the-schema)) |
| When indexing runs | `Indexer` in `backend/pdf/fulltext.py` and `Services.attachments_changed` |
| Matching through notes | `NoteTextMatcher` in `backend/notes/service.py`, `NotesIndex.texts_by_key` |
| The search endpoints | `list_items`, `search_preview`, `rebuild_fulltext`, `sync_fulltext` in `backend/api/items.py` |

Tests: `tests/test_search.py` covers parsing, modes, filters, full text (with a fake index) and
matching through notes. See [testing](testing.md).
