# References and online lookups

This page covers everything in Synapse that uses the web: item metadata, open-access PDFs, PDFs
behind an attachment's web link, and the citation graph. [Architecture](architecture.md) has the
map; this page goes into the two packages that own this work, `backend/online/` and
`backend/references/`. They are the only parts of the backend that use the network.

Nothing here runs on its own schedule. Every request starts from something the user did (a menu
command, adding items) or from a Settings → Adding Items choice. Without a network, these
features fail with a message and the rest of the app works as before.

| Module | What it does |
|---|---|
| `backend/online/http.py` | What every lookup shares: timeout, User-Agent headers, `LookupFailed`, arXiv request spacing. |
| `backend/online/metadata.py` | Item metadata from Crossref, OpenAlex, arXiv and Open Library. |
| `backend/online/pdf_finder.py` | Finds an open-access PDF, downloads it and checks it is the right paper. |
| `backend/online/remote_pdf.py` | Downloads the PDF behind an attachment that is stored as a web link. |
| `backend/library/identifiers.py` | DOIs and arXiv ids: cleaning, normalising, finding them in an item. Offline. |
| `backend/references/sources.py` | Reference lists from Semantic Scholar, OpenAlex, Crossref and arXiv's HTML version. |
| `backend/references/arxiv_html.py` | Reading the bibliography of an arXiv paper's HTML version. |
| `backend/references/connections.py` | How a paper outside the library connects to it (for plugins' lists). |
| `backend/references/store.py` | `data/citations.db`, where the fetched lists are kept. |
| `backend/references/service.py` | `Citations`: fetching now or in a background queue, matching references to library items. |
| `backend/references/graph.py` | The network shown in the Graph view (papers, cited works, notes). |

## Shared HTTP helpers

`backend/online/http.py` holds what the lookups share:

- `TIMEOUT` (15 s) for API requests. The PDF code uses its own limits: `pdf_finder.py` waits 10 s
  for Unpaywall, Google Scholar and arXiv searches and 20 s per download; `remote_pdf.py` has
  `TIMEOUT = 30`.
- `api_headers(email)` returns a User-Agent of the form
  `Synapse/2.0 (https://github.com/…; mailto:<email>)`. Crossref and OpenAlex ask clients to name
  themselves and answer such "polite" requests faster. Without an e-mail it uses
  `synapse@example.com`. Crossref, OpenAlex and Semantic Scholar requests send it; arXiv, Open
  Library and Unpaywall requests do not.
- `BROWSER_UA`, a browser's User-Agent, for PDF downloads: some publishers only serve PDFs to
  something that looks like a browser.
- `LookupFailed`: a lookup failed, and its message is shown to the user as is. The API turns it
  into a JSON error (mostly 404). `references/sources.py` subclasses it as `FetchFailed`.
- `arxiv_wait()` keeps requests to arXiv (API and PDF downloads alike) `ARXIV_SPACING` (1 s)
  apart, behind a process-wide lock. arXiv asks API users to space their requests, and Synapse
  runs several lookups side by side.

**Contact e-mail and API keys.** They live in the settings (Settings → Online Services):
`contact_email`, `openalex_api_key` and `semantic_scholar_api_key`. `http.py` does not read
settings. The callers read them and pass them in: the endpoints in `backend/api/metadata.py`,
`backend/api/attachments.py`, `backend/connector.py`, `Services.find_pdf` in
`backend/services.py`, and `Citations.fetch_now` in `backend/references/service.py`. The e-mail
goes into the User-Agent above, into Unpaywall's `email` parameter, and into OpenAlex's `mailto`
parameter when there is no OpenAlex key. The OpenAlex key is sent as the `api_key` parameter, the
Semantic Scholar key as the `x-api-key` header.

## Item metadata

### Recognising identifiers

`classify_identifier(s)` in `backend/online/metadata.py` decides what a typed or pasted string is.
It is tried in this order:

| Kind | Recognised as | Looked up on |
|---|---|---|
| `doi` | `10.<4–9 digits>/…` anywhere in the string, so DOI links work too | Crossref (arXiv DOIs: arXiv) |
| `arxiv` | New-style id (`2101.00001`, optional version), bare or after `arxiv.org/abs/`, `arxiv.org/pdf/` or `arXiv:` | arXiv API |
| `arxiv` | Old-style id (`hep-th/9901001`), only after one of those prefixes | arXiv API |
| `isbn` | 10 or 13 digits (X allowed), with only spaces, hyphens, `ISBN` and `:` around them | Open Library |
| `pmid` | 5–9 digits, optionally after `PMID` | not supported yet: `LookupFailed` |

Anything else raises `LookupFailed("Not a recognised identifier…")`.

Matching identifiers of *existing* items is a separate job, done by
`backend/library/identifiers.py`. It is used by the lookups, the citation graph and the MCP tools,
so that all of them agree on what counts as the same paper:

- `clean_doi` strips `https://doi.org/` and `doi:`; `norm_doi` also lower-cases and drops a
  trailing dot.
- `norm_arxiv` gives the bare arXiv id without its version, lower case.
- `arxiv_id_of(item)` finds an arXiv id in `archive_id`, `doi`, `url`, `extra`, `journal` or
  `number`.
- `identifiers(item)` returns the normalised `(doi, arxiv_id)`; an arXiv DOI
  (`10.48550/arXiv.…`) counts as its arXiv id.
- `title_key(title)` reduces a title for matching (no accents, case, punctuation, markup or LaTeX
  commands). Titles shorter than 15 characters give `None`: "Introduction" is too ambiguous.

### The lookup

`lookup()` is the one entry point. It takes an `identifier`, a `doi`, an `arxiv_id` or a `title`
(in that order of preference: an explicit DOI wins), plus the known authors and the e-mail.

- **DOI.** Crossref `/works/<doi>`. OpenAlex is asked for the authors at the same time, on a
  small thread pool (`_POOL`, four workers), so the second request costs no extra wait.
- **arXiv DOI or arXiv id.** arXiv's own DOIs are registered with DataCite, not Crossref, so they
  go to the arXiv API (`export.arxiv.org/api/query`).
- **ISBN.** Open Library's books API.
- **Title.** Crossref's `query.bibliographic` search (three results). The best one by
  `title_similarity` must reach `TITLE_MATCH` (0.8); otherwise the title is searched on arXiv (its
  first 12 words), because preprints are often only there. If neither matches, the error asks the
  user to add a DOI or arXiv ID.

A network error becomes `LookupFailed("Could not reach <host>…")`. The result is a dict of item
fields with empty values left out. Crossref types map to item types (`journal-article` →
`journalArticle`, `posted-content` → `preprint` …). An arXiv result is a `preprint` with
`archive_id` `arXiv:<id>`, the publisher's DOI when arXiv knows one (else `10.48550/arXiv.<id>`)
and the journal reference in `extra` as `Published as: …`.

Two merges happen inside `lookup()`:

1. When the result has a DOI, the OpenAlex author list replaces Crossref's if it is longer.
2. `resolve_authors` prefers a spelling already in the library: when a fetched name matches a
   known author letter for letter once accents are removed, and only the known one has accents,
   the known spelling is used (Banuls → Bañuls). The endpoints pass `known_authors(...)` of the
   library for this.

### Reviewing the result

`POST /api/items/<id>/lookup` (`backend/api/metadata.py`) looks up an existing item and saves
nothing. It answers `{current, fetched}`. `fetched` also carries a `citation_key` generated with
the user's pattern from the merged item.

The review is `frontend/src/views/metadataReview.ts`:

- `LookupJob` runs the lookups `CONCURRENCY` (4) at a time, with `PAUSE_MS` (120 ms) between one
  worker's requests. It can be stopped, and it stops by itself when the server is unreachable
  rather than failing every item.
- `computeChanges` compares the fields in `LOOKUP_FIELDS`. A field is proposed only when the
  fetched value is not empty and differs. A new citation key for an item that already has one is
  unticked, because replacing it would break documents that cite it.
- The dialog lists articles with changes first, then those not found, then the ones already up to
  date, folded away. Each change has a checkbox, a word diff and an Edit button. Nothing is saved
  until Apply, which sends one `PATCH /api/items/<id>` per article and keeps proposed citation
  keys unique within the batch.
- `retrieveInBackground` runs a job without the dialog (after an import), with a progress toast
  and a Review button at the end. A job opened in the dialog can also continue in the background.
- After Apply, the reference lists of the looked-up items are queued when the job's
  `references` is set (`LookupJob.referencesToQueue`). Retrieve Metadata on items already in the
  library sets it from the Settings → Online Services switch `references_auto`. After an import,
  `runAfterAdd` sets it from `add_fetch_references` (or its checkbox) instead, so "never" for new
  items wins over `references_auto`; and the items it already queued because they had a DOI or
  arXiv id (`referencesQueued`) are not queued again, so only those that got their identifier
  from the lookup are.

## Open-access PDFs

`Services.find_pdf(item_id)` (`backend/services.py`) takes the item's arXiv id from
`identifiers()`, calls `pdf_finder.find_and_download`, and attaches a found file as "Full Text PDF".
It then calls `attachments_changed()` so the full-text index picks the file up.

`candidate_urls()` lists where to look, best first:

1. For a preprint (an arXiv id, and no DOI or only an arXiv DOI): `arxiv.org/pdf/<id>`.
2. For a DOI that is not an arXiv DOI: Unpaywall's `best_oa_location.url_for_pdf`. Unpaywall
   requests carry the contact e-mail (Settings → Online Services, else `FALLBACK_EMAIL`),
   URL-encoded.
3. For a published paper that also has an arXiv id: the arXiv PDF, after Unpaywall.
4. Searches by title: Unpaywall's search (first two results), the PDF link of Google Scholar's
   first result (scraped, with the browser User-Agent), and an arXiv title search (the arXiv
   API over https, after `arxiv_wait()`, with `TIMEOUT`).

When the item has an arXiv id, `find_and_download` tries steps 1–3 first and runs the title
searches only if none of those was the paper.

`_download_first` downloads the candidates in order. A response counts only if it is a PDF (by
`Content-Type` or the `%PDF` header). `verify_pdf` then reads the first two pages and accepts the
file when they contain the DOI, the arXiv stamp (`arXiv:<id>`) or, failing both, the title (fuzzy
match of at least 85, with `rapidfuzz` when it is installed and a `difflib` fallback otherwise).
The check exists because candidates are often the wrong paper or a publisher's login page. A
verified file is saved as `<uuid>_downloaded.pdf` in the PDF folder. The result is
`{"status": "success", "filename", "source"}` or `{"status": "not_found", "message"}`.

Find Available PDF in the item menu calls `A.findPdf`; several items go through `A.findPdfs`
(`frontend/src/actions.ts`), one after another with 400 ms between them, skipping items that
already have an attachment. Items saved with the browser button use `Services.find_pdf_later`, a
background thread; `_pdf_lock` lets only one such search run at a time.

## PDFs behind a web link

An attachment can be a web address instead of a file. The frontend opens it through
`GET /api/attachments/<id>/remote` (`fileUrl` in `frontend/src/api.ts`), and
`backend/online/remote_pdf.py` downloads it. The browser button's PDF link (when the extension
could not fetch the PDF itself) is downloaded the same way (`backend/connector.py`). Because this
makes the server fetch a URL, it is careful not to become a way into the local network:

- Only `http` and `https`. Every address the host resolves to must be public
  (`ipaddress.is_global`, not multicast; IPv4-mapped IPv6 is unwrapped first).
- Redirects are followed by hand, at most `MAX_REDIRECTS` (5), and each target is checked again.
- The body is capped at `MAX_BYTES` (100 MB), by `Content-Length` and while streaming, and must
  contain `%PDF-` in its first 1024 bytes.

Failures raise `RemoteError`, which carries the HTTP status the endpoint returns (403 for a local
address, 413 too large, 415 not a PDF, 502 for network trouble).

## The citation graph

### Fetching reference lists

`fetch(item)` in `backend/references/sources.py` needs a DOI or an arXiv id; without one it raises
`LookupFailed`. It tries the sources in order and keeps the first non-empty list:

1. **Semantic Scholar** (`semanticscholar`). The DOI first (a journal version), then the arXiv
   id. One request gives titles too, and it is good for arXiv papers. Pages of 1000, following
   `next`.
2. **OpenAlex** (`openalex`). By DOI, or by the arXiv DOI built from the arXiv id. It returns the
   ids of the referenced works; their details come in batches of 50 (OpenAlex's limit for an OR
   filter). The original order is kept, and works without details stay as bare OpenAlex ids.
3. **Crossref** (`crossref`). The publisher's reference list, often with DOIs only. Skipped for
   arXiv DOIs.
4. **arXiv's HTML version** (`arxiv-html`, `arxiv_html.py`), for papers with an arXiv id: the
   other services usually know a paper's references only days after it is announced, the HTML
   version (made with LaTeXML for most LaTeX submissions, at `arxiv.org/html/<id>`) from the first
   day. Each `li.ltx_bibitem` of the `ltx_bibliography` section becomes a reference: its text
   (`raw`, without the `[12]` label), a DOI or arXiv id from its text or links (an arXiv DOI,
   `10.48550/arXiv.…`, counts as the arXiv id), the title from quotes or else the longest
   italic stretch, and the year. A paper without an HTML version (404) has no references here.
   It goes through `arxiv_wait()` like every arXiv request. `python tools/arxiv_refs.py quant-ph`
   measures, for a day's new papers, how many have references there and how many of those carry
   identifiers, next to what Semantic Scholar knows that day.

Each request goes through `_get`: a 404 means "nothing here", and a 429 or 5xx is retried once
after `Retry-After` (at most 10 s; 2 s when the header is a date). A source that fails is logged
and the next one is tried, so one broken service does not hide the others. If no source returned
references and at least one failed, `fetch` raises `FetchFailed`: trying later may work. Only when
every service answered is an empty result stored, with the source `none`. `_clean_ref` normalises
each reference (identifiers through `identifiers.py`, integer year, collapsed whitespace).

### Storage

`backend/references/store.py` keeps the lists in their own database, `data/citations.db`, so
`library.db` is never touched. Deleting the file only loses lists that can be fetched again.

| Table | Columns |
|---|---|
| `fetches` | `item_id` (key), `source` (`semanticscholar`, `openalex`, `crossref`, `arxiv-html` or `none`), `fetched_at`, `doi` and `arxiv_id` used, `count`, `error` |
| `refs` | `item_id`, `position` (key together), `doi`, `arxiv_id`, `openalex_id`, `s2_id`, `title`, `authors`, `year`, `venue`, `raw` (the citation as printed) |

`save` replaces an item's list. `save_error` records a failure without losing a list fetched
earlier. The rows of items deleted for good (or when the Trash is emptied) are removed by
`backend/api/items.py`. The schema is created with `CREATE TABLE IF NOT EXISTS` and has no
migration step; see [storage](storage.md) before changing it.

### Background queue and matching

`Citations` (`backend/references/service.py`) is created in `Services`:

- `fetch_now(item_id)` fetches, stores and returns the item's view. A failure is stored with
  `save_error` and raised again.
- `enqueue(ids)` puts items on a queue served by one daemon thread (`references`). Items already
  waiting are not added twice. The thread pauses 1 s between items to be gentle with the free
  APIs, and ends after 2 s with nothing to do. `status()` gives `{queued, running, last_error}`.
- `maybe_enqueue(item, asked)` is the hook for newly added items; see the next section.

A cited work is matched to a library item by DOI, then arXiv id, then `title_key` with years at
most one apart (a preprint and its journal version are often a year apart). The lookup tables of
the library are cached until a signature of the `papers` table changes, because the item pane asks
for them each time an item is selected.

`item_view(item_id)` is what the item pane shows: the references (each with `library_id` when it
is in the library), the library items citing this one, the fetch info, `can_fetch` and `queued`.
The pane (`frontend/src/views/referencesSection.ts`) polls every 2 s while the item is queued.
`graph()` returns every library item and every work they cite, with ids `item:<id>`, `doi:…`,
`arxiv:…`, `openalex:…` or `ref:<item>:<position>`, and `cited_by_library` counts.

`match_refs(refs)`, `citing_ids(doi, arxiv_id)` (the library items whose stored lists cite a
work, `CitationStore.citing`) and `library_signature()` are what `connections.py` uses.

### How a paper connects to the library

`Connections` (`backend/references/connections.py`, created in `Services`) scores papers outside
the library for plugins (`plugin.services.connections`, see [plugins](plugins.md#papers-outside-the-library)).
For each paper it gives `{"score", "reasons"}`, each reason `{kind, text, items}` with the library
items involved:

| Kind | Found by | Score |
|---|---|---|
| `cites` | its reference list (given by the caller) matched with `match_refs` | 3 per library paper |
| `cited_by` | library papers whose stored reference lists contain its DOI or arXiv id | 3 per library paper |
| `authors` | family name and first initial, normalised, shared with library papers | 2 per library paper, at most 5 |
| `similar` | cosine similarity of title and abstract (TF-IDF over the library; words in more than a quarter of the library and stop words left out), from 0.15 | 10 × the best similarity |

The library side (author index, word weights and postings) is built once and again only when
`library_signature()` changes. `folders` limits all of it to papers in those folders.

`build_graph()` in `backend/references/graph.py` adds the notes for the Graph view
(`frontend/src/views/graph/`): `paper`, `work` and `note` nodes, and `cites`, `cite` (a note's
`[@key]`), `link` (a note's `[[link]]`) and `about` (a paper note) edges. Everything is derived;
nothing is stored. See [notes](notes.md) for the links it reads.

## What the Adding Items settings trigger

Each setting is `never`, `always` or `ask`. With `ask`, the place where items are added shows a
ticked checkbox (`afterAddHtml` in `frontend/src/afterAdd.ts`). On the server,
`SettingsStore.wants(key, asked)` is true for `always`, or for `ask` when the user ticked the box.

| Setting (default) | Where it acts | What runs |
|---|---|---|
| `add_fetch_metadata` (`ask`) | Import dialog (`importDialog.ts` → `runAfterAdd`); the browser button (`backend/connector.py`, and the popup's checkbox) | `retrieveInBackground`, then the review. Items added by identifier or from PDFs are always looked up. The browser button asks with its own checkbox, which starts unticked when this is `never`; a save without that answer follows this setting (`ask` counts as yes). |
| `add_find_pdf` (`ask`) | Import dialog and Add by Identifier (`runAfterAdd` → `A.findPdfs`); the browser button on the server (`backend/connector.py`, when the page gave no PDF) | `POST /api/items/<id>/find_pdf` per item; `Services.find_pdf_later` for the browser button. |
| `add_fetch_references` (`always`) | Server: `from_identifier`, `from_pdf` (when the lookup found metadata) and the connector's `save` (whether or not it looked the item up), via `Citations.maybe_enqueue`. Import dialog: `runAfterAdd` → `POST /api/references/queue` for items with an identifier, and the metadata review's Apply for the ones that got it from the lookup. | The background queue. Only items with a DOI or arXiv id. For dropped PDFs, `ask` is a Fetch References button in the toast (`addPdfsAsItems` in `actions.ts`). |

`GET /api/connector/ping` sends the three values to the browser extension, which shows its own
checkboxes; see [browser extension](browser-extension.md).

## HTTP endpoints

| Endpoint | Module | What it does |
|---|---|---|
| `POST /api/items/<id>/lookup` | `api/metadata.py` | Fresh metadata for the review; saves nothing. 404 with the message on `LookupFailed`. |
| `POST /api/items/from_identifier` | `api/metadata.py` | Creates an item from a DOI, arXiv ID or ISBN. 409 with `duplicate_of` unless `allow_duplicate`. |
| `POST /api/items/from_pdf` | `api/attachments.py` | One item per uploaded PDF, filled from a DOI or arXiv id found in its text. |
| `POST /api/items/<id>/find_pdf` | `api/attachments.py` | Looks for an open-access PDF and attaches it (`Services.find_pdf`). The item needs a title, DOI or arXiv id, else 400. |
| `GET /api/attachments/<id>/remote` | `api/attachments.py` | The PDF behind a web-link attachment. |
| `GET /api/items/<id>/references` | `api/references.py` | The item's references and the library items citing it. |
| `POST /api/items/<id>/references` | `api/references.py` | Fetches the list now. 502 on `FetchFailed` or network trouble, 404 on `LookupFailed`. |
| `POST /api/references/queue` | `api/references.py` | Queues `{ids}` for background fetching. |
| `GET /api/references/status` | `api/references.py` | `{queued, running, last_error}`. |
| `GET /api/graph/citations` | `api/references.py` | The whole citation graph (`Citations.graph()`). |
| `GET /api/graph` | `api/references.py` | The Graph view's network (`build_graph()`). |
| `POST /api/connector/save` | `api/connector.py`, `backend/connector.py` | The browser button; looks up metadata when the page has a DOI or arXiv id. |
| `POST /api/outside/add` | `api/plugins.py`, `Services.add_paper` | Adds `{paper, folder_id, tags}` from a plugin's list: looked up by DOI or arXiv id (the paper's fields when that fails), duplicates checked. 201 `{item, duplicate: false}`, or 200 with the existing item and `duplicate: true`. |

## How tests fake the network

No test talks to the web. The tests replace `requests.get` with a function that answers from
canned data, using pytest's `monkeypatch`. They patch it through the module under test
(`monkeypatch.setattr(metadata.requests, "get", fake)`), which replaces the attribute on the shared
`requests` module for the length of the test.

- `tests/test_metadata.py`: `FakeResp` and `fake_net()` route by host (Crossref, arXiv Atom feeds
  built by `atom()`). One test uses a `threading.Barrier` to prove Crossref and OpenAlex are asked
  side by side; another checks the arXiv spacing.
- `tests/test_pdf_finder.py`: the `web` fixture answers by URL prefix and records every URL asked,
  so tests can assert the order of candidates. The PDFs are made with `make_pdf` from
  `tests/conftest.py`.
- `tests/test_references.py` and `tests/test_graph.py`: `router()` answers by a substring of the
  URL; `R.time.sleep` is patched so retries do not wait.
- `tests/test_remote_pdf.py`: also fakes `socket.getaddrinfo`, so host names resolve to chosen
  public or private addresses.
- `tests/test_connector.py`: patches `connector.lookup`, `pdf_finder.find_and_download` or
  `Services.find_pdf_later` directly.
- `tests/plugins/test_outside.py`: patches `metadata.lookup` and `sources.fetch` (the `no_web`
  fixture); parses a LaTeXML bibliography written in the test.

`tests/conftest.py` sets `ARXIV_SPACING` to 0 for every test, and the `app` fixture turns off
background reference fetching and PDF finding, which would outlive the test. The frontend job is
tested in `tests/js/lookupJob.test.mjs` with an injected `lookup` function. See
[testing](testing.md).

## Where to change things

- **A new metadata source.** Add a function in `backend/online/metadata.py` that returns item
  fields (drop empty values, set `library_catalog`) and raises `LookupFailed` with a readable
  message. Recognise its identifier in `classify_identifier` if it has one, and route to it in
  `lookup()`. Use `TIMEOUT` and `api_headers(email)`, and `arxiv_wait()` for anything on arXiv.
  Add a faked test in `tests/test_metadata.py`. If it returns a field the review should offer,
  add it to `LOOKUP_FIELDS` in `metadataReview.ts`.
- **A new PDF source.** Add its candidates in `candidate_urls()` at the right priority; downloads
  and checks are shared.
- **A new reference source.** Add a function in `backend/references/sources.py` that returns
  dicts with keys from `REF_FIELDS` and add it to `attempts` in `fetch()`. Add its name to the
  `source` comment in `store.py` and to `SOURCES` in `referencesSection.ts`.
- **A new identifier rule** shared by several packages goes in `backend/library/identifiers.py`,
  with a test in `tests/test_identifiers.py`.
- **A new setting** (an e-mail, a key) goes in `DEFAULTS` in `backend/settings.py` and on the
  Online Services page (`frontend/src/views/settings/page.ts`); read it in the caller and pass it
  in.
