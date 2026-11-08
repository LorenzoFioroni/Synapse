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
| `extract.js` | the web page | `extractCitation` (reads the metadata), `fetchPdfInPage` (fetches the PDF) and `pageState` (where a Find Missing PDFs tab has got to). |
| `popup.html`, `popup.js`, `popup.css` | the popup | Talks to the library, shows the form, saves. |
| `find_pdfs.html`, `find_pdfs.js` | a window of its own | Find Missing PDFs: the publisher's PDF of items that have none, fetched in this browser (see [below](#find_pdfsjs-missing-pdfs-through-the-browser)). |
| `options.html`, `options.js` | the options page | The library address, server permission and access token. |
| `connection.js` | popup and options | Validates addresses, loads server-bound credentials and sends authenticated requests. |
| `background.js`, `bridge.js`, `library.js` | worker and isolated library content script | Registers the bridge for the configured library and validates scoped item-ID requests from the app. No credentials cross the page bridge. |
| `tsconfig.json` | | Type-checking only (see [Type-checking](#type-checking)). |

The extension is plain JavaScript in ES modules, with no build: Chrome loads the folder as it is,
so **Load unpacked** works straight from the repository.

### Permissions

| Permission | Why |
|---|---|
| `activeTab` | Access to the current tab, only after the user clicks the button or presses the shortcut. |
| `scripting` | Runs `extract.js` functions in that tab. |
| `storage` | The library address (`chrome.storage.sync`), server-bound token and last folder (`chrome.storage.local`). |
| `host_permissions`: `http://127.0.0.1/*`, `http://localhost/*`, `http://[::1]/*` | Lets the popup call the library on this computer. |

Remote HTTPS servers use `optional_host_permissions: https://*/*`. Options requests only the
chosen server’s origin when the user clicks Save. Find Missing PDFs requests all of
`https://*/*` when the user clicks **Find PDFs**, the first time: it reads pages of whatever
publisher a DOI leads to. It can be withdrawn in `chrome://extensions` (the next run asks again). Tokens never enter Chrome Sync. Local extension
storage is restricted to `TRUSTED_CONTEXTS` before credentials are read or written, preventing content scripts from accessing it.

The shortcut is the `_execute_action` command, <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd> by
default. Apart from fetching the PDF inside the page (below) and the pages Find Missing PDFs
opens, the extension talks only to the library.

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
   | Complete metadata online | the page has a DOI or arXiv id; ticked unless `on_add.fetch_metadata` is `"never"` | `lookup` (left out when there is no checkbox) |
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

`options.js` validates and stores the address and checks it with a ping. A missing scheme means
HTTP for loopback names and HTTPS otherwise. Remote servers require HTTPS and a browser token
from Settings → AI Access → New Browser Token. Save requests optional host permission for that origin. The token
is stored with its server in persistent local extension storage; it is loaded only for a matching
address and an explicit `scope: "connector"` marker. It survives browser restarts and extension
updates. Stored credentials from earlier builds without this marker are removed; users must
create a dedicated browser token instead of reusing their old AI token. The marker is only a
client migration aid; the server validates the actual stored token scope.
Editing the address clears the token field. One server is active at a time.

`connection.js` adds `Authorization: Bearer …` to every connector call when configured. Fetches
omit cookies and reject redirects so credentials cannot follow a redirected server address.
The popup displays authentication errors with a link to its settings. Localhost still works
without a token, including older local servers.

## find_pdfs.js: missing PDFs through the browser

Publishers give their PDFs to readers on a subscribing network (a university's network or VPN),
but many put a browser check in front of their sites (Cloudflare "Just a moment…", Springer's
"Client Challenge", ScienceDirect's redirect by script) that a program on the server does not pass:
Find Available PDF in the app then gets a 403 or a page instead of the PDF. This page fetches
them in the user's browser instead, which passes those checks as it does when the user opens the
paper, and with the browser's access (campus network, VPN, the user's sign-ins). That is why the
browser has to be on the subscribing network, not the server. Zotero does the same with its
hidden browser and its Connector.

The popup's download icon opens `find_pdfs.html` in a window of its own (`chrome.windows.create`,
type `popup`): the popup closes when it loses focus, and a hidden tab's timers are throttled. The
page:

1. `GET /api/connector/missing-pdfs` (with the chosen folder and saved cursor): up to 50 items with
   a DOI and no PDF. Ordering is stable by descending `(date_added, id)`. Each completed attempt
   saves its cursor under a separate local-storage key for that exact server and folder. Failed
   papers remain eligible for retry but do not block **Next batch** or the next visit. Stop leaves
   untried items available to continue; **Start from newest** resets the cursor. Reaching the end
   clears it for a future full pass.
2. On **Find PDFs**: requests `https://*/*` (above), then opens one background tab (inactive, in
   the last focused window) and goes through the items one at a time, with `PAUSE_MS` (5 s)
   between them: publishers block institutions whose readers download in bulk.
3. For each item: load `https://doi.org/<doi>` in the tab and wait for it (`navigate`, at most
   `LOAD_MS`); then `settle` runs `pageState` in the tab every second until the page shows a PDF
   or PDF links, or has not changed for `STEADY_POLLS` (4) polls (pages built by script add
   their links late), for at most `SETTLE_MS` (25 s). The PDF links, in order: what the page
   names (`citation_pdf_url`, `<link rel="alternate" type="application/pdf">`); the PDF address
   of publishers that name none, from the page's URL as Zotero's translators do (APS:
   `/abstract/` → `/pdf/`; Atypon sites such as science.org, ACS, Wiley, Taylor & Francis:
   `/doi/…` → `/doi/pdfdirect/`, `/doi/pdf/`, `/doi/pdfplus/`, `/doi/epdf/`, each one the page
   links to); then up to four links that say PDF in their address or label, not supplementary
   material. A browser check runs its JavaScript and reloads the
   page by itself meanwhile; `pageState` recognises one by its title or text ("Just a moment",
   "Checking your browser", "Client Challenge", …) or its elements (`#challenge-form`,
   `.cf-turnstile`, a `challenges.cloudflare.com` frame, `#captcha-box`, …), and while it is
   there the page keeps waiting. One that needs a click stays in its tab for the user; the item
   then ends as `blocked`, for **Retry**.
4. Each PDF link is tried with `fetchPdfInPage` from the article page (its cookies, its
   origin), then by `fetch` from the extension page (host permission, so no CORS limit). Only
   after all links failed so, the first `OPEN_LINKS` (3) are opened in the tab: once it shows a
   PDF, that URL is fetched from there (ScienceDirect's link is a page that moves on to the PDF
   by script); when it shows a page, that page's own PDF links not tried yet are (an Atypon
   reader, `/doi/epdf/`, links the file).
5. A PDF goes to `POST /api/connector/attach-pdf`, which attaches it only if it is the paper. A
   PDF of another paper moves on to the next link.

Embedded PDF objects and PDF iframes are candidates too, including page-owned blobs fetched inside
their page. Chrome's native PDF viewer may refuse script injection; its original tab URL is then
fetched through the extension and still checked for PDF bytes and paper identity before attachment.

The outcome of each item is shown in its row: `attached`, `has-pdf`, `wrong-paper`, `unverified`,
`no-access` (only pages came back, usually a login or purchase page), `no-link`, `blocked` or
`error`. Under a failed item the row says what came back, for each link (`HTTP 403`, "not a
PDF: the page “Sign in”", "a PDF of another paper"), or which page had no PDF link, so a user
can tell a missing subscription from a page this code does not read. At the end, **Retry**
runs the items not found again, and the tab is closed. Article titles link to the publisher for
manual review. `?resolver=` (the DOI resolver), `?pause=` and `?batch=` exist for the automated
checklist.

### Requests from the app

`frontend/src/afterAdd.ts` first asks `publisherPdfs.ts` to hand eligible new DOI items to the
extension when PDF lookup was selected by the user or the existing Adding Items policy. The
isolated content script relays only item IDs and an acknowledgement ID. The worker requires the
browser-supplied sender to be the configured library's exact origin (scheme, host and port) and
top frame, bounds/validates the IDs, and verifies the connector connection and
`capabilities.publisher_pdfs` before accepting. It constructs the finder URL itself; pages cannot
supply URLs, credentials or another library. The content script is registered dynamically only
for the configured host's existing permission, and updated when the server or permissions change.
Because Chrome host match patterns do not distinguish ports, the worker's exact-origin check is
required. Credentials remain in trusted extension storage, and never enter the library page.

The finder lists just those IDs and starts automatically only if publisher permission was granted
previously; otherwise the user clicks Find PDFs. After a failed publisher attempt,
`POST /api/connector/find-pdf` queues other enabled sources. Failed queue requests pause visibly
and retain retry; closing the finder cancels untried work. Without a usable extension connection,
the app keeps its existing server-source follow-ups. Non-publisher identifiers and imports over
200 eligible DOI items also use the normal sources.

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
| `capabilities` | Optional feature flags; `publisher_pdfs` and `pdf_pagination` are true on servers supporting scoped app requests and resumable batches. |

The popup shows a checkbox for `find_pdf` and `fetch_references` when they are `"ask"`. Whether to
look up online is always the user's choice in the popup ("Complete metadata online"), so
`fetch_metadata` only sets that checkbox's starting state: ticked unless it is `"never"`. See
[Looking up and fetching references](#looking-up-and-fetching-references).

### `POST /save`

The body is JSON, or multipart with a `meta` field (JSON) and an optional `file`.

| Request field | Meaning |
|---|---|
| `title`, `year`, `journal`, `volume`, `issue`, `pages`, `publisher`, `abstract`, `issn`, `isbn`, `doi`, `url`, `language`, `item_type`, `place`, `short_title` | Item fields (`PAGE_FIELDS`). |
| `authors` | A list of names, or one string separated by `;` or new lines. |
| `arxiv` | An arXiv id; becomes `archive: arXiv` and `archive_id: arXiv:<id>`. |
| `page_url` | Used as `url` when nothing else gives one. |
| `folder_id`, `tags` | Where to file the new item. `folder_id` is a folder id, as a number or a string of digits; anything else is refused (400). |
| `lookup` | Look the identifier up online. Without it, `add_fetch_metadata` decides: anything but `"never"` looks up. |
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
4. With an identifier, and `lookup` (or, without it, `add_fetch_metadata` not `"never"`), fetches
   the metadata online (`online.metadata.lookup`; see [`references.md`](references.md)). Online
   values win and the page fills the gaps. If the lookup fails, the page's data is used.
5. Checks for a duplicate (`find_duplicate`: same DOI, or same title and year, not in the Trash).
6. Creates the item, and queues its reference list when it has a DOI or arXiv id and
   `add_fetch_references` (or `fetch_references` for "ask") says so, whether or not it was looked
   up.
7. Attaches the PDF: the uploaded file, else a download of `pdf_url` through
   `online/remote_pdf.py`, which fetches only from public addresses (also after redirects), only
   PDFs, and at most 100 MB, because the link comes from the browser. PDFs are saved as
   `<uuid>_connector.pdf` in `data/pdfs/`, and the full-text index catches up when automatic
   indexing is on (see [`fulltext-search.md`](fulltext-search.md)). With no PDF, and
   `add_find_pdf` set to "always" (or "ask" with `find_pdf`), an open-access search starts in the
   background.

#### Looking up and fetching references

Settings → "When an item is added" applies to the browser button like this:

- **Metadata** (`add_fetch_metadata`). The popup asks every time there is an identifier, with
  its "Complete metadata online" checkbox, because the page's own metadata is often good enough
  and the user can see it. The setting only chooses whether that box starts ticked (`"never"`:
  unticked). A request without `lookup` (a page with no identifier, such as a PDF tab whose DOI
  is then found inside the PDF, or another client) follows the setting, with `"ask"` counting as
  yes because nobody can be asked and saving always looked up before the setting existed.
- **References** (`add_fetch_references`). Fetching a reference list needs only the DOI or arXiv
  id, so it does not depend on the lookup: an item saved from the page's metadata gets its
  references too.
- **PDF** (`add_find_pdf`). Only when no PDF came from the page (step 7).

| Answer | Body |
|---|---|
| 201 | `{item, source, pdf}`. `source` is `"online"` or `"page"`. `pdf` is `attached`, `downloaded`, `failed`, `not-a-pdf`, `searching` or `none`. |
| 409 | `{duplicate_of, title, folder_ids}`: the existing item. Nothing is saved. |
| 400 | `{error}`: nothing to save, or a malformed `folder_id`. Nothing is saved. |

### `POST /file`

For the "already in your library" screen. The body is `{id, folder_id, tags}`. It adds the
existing item to the folder and the tags, and answers `{item}`. `folder_id` is read as in `/save`
(the popup sends a string): a malformed one gives 400, and an unknown item id gives 404.

### `GET /missing-pdfs`

For Find Missing PDFs. Items not in the trash with a DOI that is not an arXiv DOI (the app
downloads arXiv itself) and no PDF attachment, newest first. Query: `folder_id` (that folder and
its subfolders; malformed → 400), `limit` (default 50, at most 200), optional `ids` (1–200
comma-separated item IDs), and an optional opaque `cursor`. Answers
`{items: [{id, title, doi, year, cursor}], total, next_cursor}`. The total counts eligible items
before cursor filtering; `next_cursor` is null at the end. Per-item cursors resume after individual
completed attempts, even if the boundary item was deleted or acquired a PDF. Invalid or oversized
IDs/cursors give 400. Omitting the optional filters preserves the original listing contract.

### `POST /find-pdf`

JSON `{id}` queues the configured PDF sources for an existing item after an app-requested browser
attempt. Answers `{status: "searching"}` or `{status: "has-pdf"}` without adding a second PDF.
Malformed/missing IDs give 400; unknown items give 404. It accepts no caller-supplied URL.

### `POST /attach-pdf`

Multipart with `id` and `file`: a PDF the extension fetched for an existing item. It is attached as
"Full Text PDF" only if the item has no PDF yet and `verify_pdf` (the check Find Available PDF
makes: the DOI, the arXiv stamp or the title in the opening pages) says it is the paper. Typography,
ligatures and wrapped identifiers/titles are normalized. The first two pages are checked first;
indeterminate cover pages allow a bounded look through five pages, but contradictory opening-page
identity is never overridden by a later reference. Caller-supplied identity fields are ignored.
Answers `{status, item}`, `status` being `attached`, `has-pdf`, `wrong-paper`, `unverified` or
`not-a-pdf`. `wrong-paper` and `unverified` add a readable `reason`; unreadable/scanned PDFs remain
unattached for manual review.
No `id` gives 400, an unknown one 404. It never replaces or adds to a PDF the item has.

### CORS and the guard

The blueprint's `after_request` hook adds `Access-Control-Allow-Origin` (and allowed headers and
methods) for `chrome-extension://` and `moz-extension://` origins only. No other origin gets CORS
headers, so websites cannot read the ping's folders and tags.

The guard accepts extension origins without a token on this computer only, and only under
`/api/connector/` (`CONNECTOR_PREFIX` in `backend/access/guard.py`). Remote connector calls
from extension origins (or clients without an Origin) require a valid `connector`-scope bearer token,
validated by the guard before bypassing device-cookie checks. Host validation still applies,
and website origins cannot use connector bearer authentication. OPTIONS preflights from
extensions expose no data and need no token; CORS allows the Authorization header, including
on authentication failures. Browser tokens have no automatic expiry, are individually revocable,
and are rejected by `/mcp`; AI tokens are rejected by the connector. `/api/mcp/tokens` accepts optional `scope: "connector"` for issuance and otherwise
keeps its existing MCP default. Both scopes use its existing admin confirmation and revocation
checks. See [storage](storage.md#the-other-databases) for the additive schema migration. Anywhere else
an extension origin gets 403, so another installed extension cannot edit or delete the library.
A new endpoint the extension must call therefore belongs in this blueprint. See
[The guard](sync-and-devices.md#the-guard).

## Compatibility

`CLAUDE.md` lists the HTTP API used by the browser extension as a contract. People reload an
unpacked extension when they think of it, not when they update the app. So:

- Keep the request and response fields above working. Add new fields as optional, and give them
  defaults that keep the old behaviour (as `attach_pdf` defaults to `true`, and a missing `lookup`
  looks up unless the user set metadata lookup to "never").
- Keep the 409 shape: the popup reads `duplicate_of`, `title` and `folder_ids`.
- The popup must cope with an older library. It treats a missing `on_add` as "never ask".
- Find Missing PDFs must too: a 404 from `/missing-pdfs` (a library from before it) shows
  "Update Synapse first" instead of the server's error.
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
  public addresses, and CORS for extension origins only; `/missing-pdfs` (folders, arXiv DOIs and
  items with a PDF left out, the limit) and `/attach-pdf` (a login page, the wrong paper, never a
  second PDF).
- `tests/js/extract.test.mjs`: `extractCitation` with a faked page (malformed `%`-escapes in a PDF
  URL, an escaped DOI).
- `tests/test_guard.py`: `test_browser_extension_can_save`, and `test_extensions_only_reach_the_connector`
  (an extension origin is refused outside `/api/connector/`).
- `tests/test_branding.py`: the ping names (above).
- `tests/e2e/extension_checklist.py`, run by hand: checks remote setup with mocked network/permission
  responses (persistent server-bound token storage across browser restarts, content-script isolation, no token sync, switching back to localhost), and loads the extension into Chromium, saves the
  address in the options page, opens `tests/e2e/fixtures/article.html`, checks that the popup reads
  its title and DOI, and saves it with a tag. It needs `pip install playwright` and full Chromium.
- `tests/e2e/find_pdfs_checklist.py`, run by hand: Find Missing PDFs against a fake publisher on
  127.0.0.1: a browser check that sets a cookie and reloads, a PDF behind a redirect by script, a
  reader page whose download link gives the PDF, a link known only by its "Download PDF" label
  (not the supplementary PDF next to it), a login page, a PDF of another paper and a page without
  a PDF link; four PDFs attached, three to retry with what came back, the tab closed. It also
  checks the links `pageState` derives on APS and science.org pages (served by the test).

See [`testing.md`](testing.md) for how the suites run.

## Where to change things

| To change | Edit |
|---|---|
| What is read from pages | `extractCitation` in `extract.js` (self-contained), the `Citation` typedef, and `PAGE_FIELDS` or `page_item` in `backend/connector.py` if the server must read a new field |
| How the PDF is fetched | `fetchPdfInPage` in `extract.js`, `doSave` in `popup.js` |
| The form and its checkboxes | `renderForm` in `popup.js` |
| What saving does | `save` in `backend/connector.py`; the request and answer shapes in `backend/api/connector.py` |
| Permissions | `manifest.json`, and the Permissions section of `browser_extension/README.md` |
