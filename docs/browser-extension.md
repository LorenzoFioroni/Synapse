# Browser extension

The "Save to Library" button is a Chrome extension in `browser_extension/`. It reads the citation
data of the page you are on and posts it to the library's connector API
(`backend/api/connector.py`, which hands it to `backend/connector.py`). This page describes how both halves work and what must stay
compatible. To install the extension, see
[`browser_extension/README.md`](../browser_extension/README.md). Where it fits in the whole app is
in [`architecture.md`](architecture.md).

## Files

| File | Runs in | What it does |
|---|---|---|
| `manifest.json` | | Manifest V3: the popup, the icons, the options page and the shortcut. |
| `extract.js` | the web page | `extractCitation` (reads the metadata) and `fetchPdfInPage` (fetches the PDF). |
| `popup.html`, `popup.js`, `popup.css` | the popup | Talks to the library, shows the form, saves. |
| `options.html`, `options.js` | the options page | The library's address. |
| `tsconfig.json` | | Type-checking only (see [Type-checking](#type-checking)). |

The extension is plain JavaScript in ES modules, with no build: Chrome loads the folder as it is,
so **Load unpacked** works straight from the repository.

### Permissions

| Permission | Why |
|---|---|
| `activeTab` | Access to the current tab, only after the user clicks the button or presses the shortcut. |
| `scripting` | Runs `extract.js` functions in that tab. |
| `storage` | The library address (`chrome.storage.sync`) and the last folder (`chrome.storage.local`). |
| `host_permissions`: `http://127.0.0.1/*`, `http://localhost/*` | Lets the popup call the library on this computer. |

The shortcut is the `_execute_action` command, <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd> by
default. Apart from fetching the PDF inside the page (below), the extension talks only to the
library.

## extract.js: reading the page

`popup.js` imports the two functions and passes them to `chrome.scripting.executeScript` as
`func`. Chrome serialises the function's source and runs it in the page. That is why **each
function must be self-contained**: it cannot use imports, module-level constants or helpers
defined outside its own body. `extractCitation` declares its helpers (`metas`, `meta`, `clean`,
`decode`, `cleanDoi`) inside itself for this reason. `decode` is a `decodeURIComponent` that
keeps the text as it is when its `%`-escapes are malformed, so an odd URL cannot abort the
extraction.

### What it extracts, and in what order

`meta(a, b, …)` returns the first of the named `<meta name>` or `<meta property>` tags that has
content (names match case-insensitively). Each field tries its sources in this order:

| Field | Sources, first match wins |
|---|---|
| `title` | `citation_title`, `dc.title`, `prism.title`, `og:title`, `twitter:title`, then `document.title` |
| `authors` | every `citation_author`; else every `dc.creator`; else `citation_authors` split on `;` |
| `year` | `citation_publication_date`, `citation_date`, `citation_online_date`, `citation_year`, `dc.date`, `prism.publicationDate`, `article:published_time` |
| `journal` | `citation_journal_title`, `citation_conference_title`, `citation_book_title`, `prism.publicationName`, `citation_inbook_title`, `og:site_name` |
| `volume`, `issue` | `citation_volume` / `prism.volume`; `citation_issue` / `prism.number` |
| `pages` | `citation_firstpage`–`citation_lastpage` (or the PRISM pair); the first page alone if they are equal |
| `publisher` | `citation_publisher`, `dc.publisher`, `citation_technical_report_institution` |
| `abstract` | `citation_abstract`, `dc.description`, `og:description`, `description` |
| `issn`, `isbn`, `language` | `citation_issn` / `prism.issn`; `citation_isbn`; `citation_language` / `dc.language` |
| `pdf_url` | `citation_pdf_url`, made absolute against the page URL |
| `doi` | `citation_doi`, `dc.identifier`, `prism.doi`, `bepress_citation_doi`; then `meta[scheme="doi"]`; then the canonical link; then the page URL |
| `arxiv` | `citation_arxiv_id` (without an `arXiv:` prefix) |

After the meta tags, every JSON-LD block (`script[type="application/ld+json"]`, including an
`@graph`) whose `@type` looks like an article, book, thesis, report, chapter or dataset fills
fields that are **still empty**: title, authors, year, journal, DOI and abstract. A book there
sets `item_type` to `book`. Malformed JSON-LD is common and is ignored.
Because `og:title` and `document.title` are tried before JSON-LD, in practice JSON-LD seldom
sets the title; the same goes for the journal (`og:site_name`) and the abstract (`description`).

Then the site-specific rules run. On `arxiv.org/abs/…`, `/pdf/…` or `/html/…` the id comes from
the URL (without the version), `pdf_url` defaults to `https://arxiv.org/pdf/<id>`, and a leading
`[2205.03415] ` is removed from the title.

If nothing set `item_type`, it is chosen in this order: `preprint` (an arXiv id, or bioRxiv,
medRxiv, SSRN or "preprint" in the site or journal), `conferencePaper`, `thesis`, `report`,
`bookSection`, `book` (an ISBN and no journal), `journalArticle` (a journal or a DOI), else
`webpage`. `has_citation_meta` says whether the page had `citation_title`.

A **PDF opened in the browser** (`document.contentType` is `application/pdf`, or a `.pdf` path
with a PDF `<embed>`) is handled first and returns early. It sets `is_pdf`, `pdf_url` (the page
itself), a title from `document.title` or the file name, an arXiv id from an arXiv PDF URL, and a
DOI from the URL. The server then looks for a DOI inside the PDF (see below).

The `Citation` typedef at the top of `extract.js` lists every field it can return.

### Fetching the PDF with the user's cookies

`fetchPdfInPage(pdfUrl, maxBytes)` runs in the page too, and calls `fetch(pdfUrl, {credentials:
'include'})`. Because the request comes from the publisher's own page, the user's login and
institutional access apply. A server-side download would usually get a login page instead.

It refuses files over `maxBytes` (`MAX_PDF_BYTES`, 60 MB in `popup.js`) and anything that does
not start with `%PDF` (usually a login page). The bytes come back base64-encoded, because a
script's result must be serialisable to cross from the page to the popup. When it fails (an
error, or a cross-origin URL the page may not read), the popup leaves `pdf_url` in the request and
the server tries to download it itself.

## popup.js: the flow

1. Read the library address from `chrome.storage.sync` (`server`, default
   `http://127.0.0.1:8000`) and find the active tab. The automated test passes `?tabId=` instead.
   Only `http`, `https` and `file` pages can be saved.
2. In parallel: run `extractCitation` in the tab, and `GET /api/connector/ping`. If the ping fails
   or `app` is not one of `['synapse', 'zotero-clone']`, show "Library not running". If the
   extraction fails (Chrome forbids scripts on some pages), fall back to the tab's title and URL as
   a web page.
3. Show the form: the type, identifier and PDF badges, an editable title, the folder (the last
   one used, if it still exists) and tags (existing tags are suggested and matched case-
   insensitively). Checkboxes appear only when they apply:

   | Checkbox | Shown when | Sent as |
   |---|---|---|
   | Attach the PDF | the page has a `pdf_url` | `attach_pdf` |
   | Complete metadata online | the page has a DOI or arXiv id | `lookup` |
   | Look for an open-access PDF | `on_add.find_pdf` is `"ask"` and the page is not a PDF | `find_pdf` |
   | Fetch the reference list | `on_add.fetch_references` is `"ask"` and there is an identifier | `fetch_references` |

4. On **Save** (or <kbd>Cmd/Ctrl</kbd>+<kbd>Enter</kbd>): fetch the PDF in the page if wanted,
   then `POST /api/connector/save` as multipart with `meta` (JSON) and `file`. A PDF that was
   uploaded sets `attach_pdf: false` and `pdf_name: 'Full Text PDF'`, so the server does not
   download it again.
5. Show the result. On a 409 the duplicate screen offers **Add to "<folder>"** (`/file`) when a
   folder was chosen that the item is not in yet, otherwise **Save Anyway** (the same request
   with `allow_duplicate: true` and the earlier answers). **Open in Library** opens
   `<server>/#item=<id>`, reusing a library tab if one is open.

`options.js` stores the address (adding `http://` if it is missing) and checks it with a ping.

## The connector API

All endpoints are under `/api/connector`. The extension sends no `X-Synapse-Client` header, so
its changes reach every open window through the change feed (see
[`sync-and-devices.md`](sync-and-devices.md)).

### `GET /ping`

Tells the extension that the library is running and fills its pickers.

| Field | Content |
|---|---|
| `app` | `"synapse"` |
| `version` | `"1.0"` (`VERSION`) |
| `folders` | `folders.tree()`: a flat list with `id`, `name`, `parent_id`, `position` and counts |
| `tags` | tag names |
| `counts` | `{library, trash, duplicates}` |
| `on_add` | Settings → "When an item is added": `fetch_metadata`, `find_pdf`, `fetch_references`, each `"never"`, `"always"` or `"ask"` |

The popup uses `find_pdf` and `fetch_references` from `on_add`. It does not use `fetch_metadata`:
whether to look up online is its own "Complete metadata online" checkbox (`lookup`), and `save`
does not read `add_fetch_metadata`.

### `POST /save`

The body is JSON, or multipart with a `meta` field (JSON) and an optional `file`.

| Request field | Meaning |
|---|---|
| `title`, `year`, `journal`, `volume`, `issue`, `pages`, `publisher`, `abstract`, `issn`, `isbn`, `doi`, `url`, `language`, `item_type`, `place`, `short_title` | Item fields (`PAGE_FIELDS`). |
| `authors` | A list of names, or one string separated by `;` or new lines. |
| `arxiv` | An arXiv id; becomes `archive: arXiv` and `archive_id: arXiv:<id>`. |
| `page_url` | Used as `url` when nothing else gives one. |
| `folder_id`, `tags` | Where to file the new item. `folder_id` is a folder id, as a number or a string of digits; anything else is refused (400). |
| `lookup` | Look the identifier up online (default `true`). |
| `allow_duplicate` | Save even if the item is already in the library. |
| `pdf_url`, `attach_pdf` | Download this PDF on the server (default `true`) when no file was sent. |
| `pdf_name` | The attachment's name (default "Full Text PDF"). |
| `find_pdf`, `fetch_references` | The user's answer for settings that are `"ask"`. |

Other fields the extension sends (`site`, `is_pdf`, `has_citation_meta`) are ignored.

What saving does (`save` in `backend/connector.py`; the endpoint only reads the request and
shapes the answer):

1. Refuses a request with a malformed `folder_id`, or with no title, DOI, arXiv id or file (400).
2. If a PDF was uploaded and there is no DOI or arXiv id, extracts the text of its first two
   pages and looks for an identifier (`find_identifier` in `backend/pdf/text.py`).
3. Normalises the page data into item fields (`page_item`): authors through
   `parse_display_name`, the year through `year_from`, `item_type` `webpage` by default.
4. With `lookup` and an identifier, fetches the metadata online (`online.metadata.lookup`; see
   [`references.md`](references.md)). Online values win and the page fills the gaps. If the lookup
   fails, the page's data is used.
5. Checks for a duplicate (`find_duplicate`: same DOI, or same title and year, not in the Trash).
6. Creates the item. If the metadata came from online, queues its reference list when Settings
   (or `fetch_references` for "ask") says so.
7. Attaches the PDF: the uploaded file, else a download of `pdf_url` through
   `online/remote_pdf.py`, which fetches only from public addresses (also after redirects), only
   PDFs, and at most 100 MB, because the link comes from the browser. PDFs are saved as
   `<uuid>_connector.pdf` in `data/pdfs/`, and the full-text index catches up when automatic
   indexing is on (see [`fulltext-search.md`](fulltext-search.md)). With no PDF, and
   `add_find_pdf` set to "always" (or "ask" with `find_pdf`), an open-access search starts in the
   background.

| Answer | Body |
|---|---|
| 201 | `{item, source, pdf}`. `source` is `"online"` or `"page"`. `pdf` is `attached`, `downloaded`, `failed`, `not-a-pdf`, `searching` or `none`. |
| 409 | `{duplicate_of, title, folder_ids}`: the existing item. Nothing is saved. |
| 400 | `{error}`: nothing to save, or a malformed `folder_id`. Nothing is saved. |

### `POST /file`

For the "already in your library" screen. The body is `{id, folder_id, tags}`. It adds the
existing item to the folder and the tags, and answers `{item}`. `folder_id` is read as in `/save`
(the popup sends a string): a malformed one gives 400, and an unknown item id gives 404.

### CORS and the guard

The blueprint's `after_request` hook adds `Access-Control-Allow-Origin` (and allowed headers and
methods) for `chrome-extension://` and `moz-extension://` origins only. No other origin gets CORS
headers, so websites cannot read the ping's folders and tags.

The guard treats the connector like any other endpoint. On this computer it accepts extension
origins for requests that change something; from another device it does not. See
[The guard](sync-and-devices.md#the-guard).

## Compatibility

`CLAUDE.md` lists the HTTP API used by the browser extension as a contract. People reload an
unpacked extension when they think of it, not when they update the app. So:

- Keep the request and response fields above working. Add new fields as optional, and give them
  defaults that keep the old behaviour (as `lookup` and `attach_pdf` default to `true`).
- Keep the 409 shape: the popup reads `duplicate_of`, `title` and `folder_ids`.
- The popup must cope with an older library. It treats a missing `on_add` as "never ask".
- Both sides accept both names of the app. The server answers `app: "synapse"`, and `popup.js`
  and `options.js` accept `['synapse', 'zotero-clone'].includes(…)`, the older name.
  `test_extension_accepts_both_ping_names` in `tests/test_branding.py` checks that exact text, and
  `test_titles_and_ping` checks the server's answer.

## Type-checking

The code is typed with JSDoc comments (`@typedef`, `@param`, casts such as
`/** @type {HTMLInputElement} */ (…)`). `npm run check` in `frontend/` runs `tsc` twice, the
second time with `-p ../browser_extension`. `browser_extension/tsconfig.json` sets `allowJs`,
`checkJs`, `strict` and `noUnusedLocals`, and takes the `chrome` types from
`frontend/node_modules/@types` (`@types/chrome` in `frontend/package.json`). CI runs the check.
The `Library` typedef in `popup.js` and `Citation` in `extract.js` describe the API's shape on
this side: keep them in step with `connector.py`.

## Tests

- `tests/test_connector.py`: the ping, saving from page metadata without the network, online
  metadata winning, an uploaded PDF, a DOI read from a PDF, duplicates and `allow_duplicate`,
  `/file`, the `on_add` settings, the background PDF search, the page's PDF link fetched only from
  public addresses, and CORS for extension origins only.
- `tests/js/extract.test.mjs`: `extractCitation` with a faked page (malformed `%`-escapes in a PDF
  URL, an escaped DOI).
- `tests/test_guard.py`: `test_browser_extension_can_save`.
- `tests/test_branding.py`: the ping names (above).
- `tests/e2e/extension_checklist.py`, run by hand: loads the extension into Chromium, saves the
  address in the options page, opens `tests/e2e/fixtures/article.html`, checks that the popup reads
  its title and DOI, and saves it with a tag. It needs `pip install playwright` and full Chromium.

See [`testing.md`](testing.md) for how the suites run.

## Where to change things

| To change | Edit |
|---|---|
| What is read from pages | `extractCitation` in `extract.js` (self-contained), the `Citation` typedef, and `PAGE_FIELDS` or `page_item` in `backend/connector.py` if the server must read a new field |
| How the PDF is fetched | `fetchPdfInPage` in `extract.js`, `doSave` in `popup.js` |
| The form and its checkboxes | `renderForm` in `popup.js` |
| What saving does | `save` in `backend/connector.py`; the request and answer shapes in `backend/api/connector.py` |
| Permissions | `manifest.json`, and the Permissions section of `browser_extension/README.md` |
