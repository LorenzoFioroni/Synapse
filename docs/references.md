# References and online lookups

This page covers everything in Synapse that uses the web: item metadata, open-access PDFs, PDFs
behind an attachment's web link, and the citation graph. [Architecture](architecture.md) has the
map; this page goes into the core services in `backend/online/` and `backend/references/`, with provider HTTP code in
bundled plugins. The reference records, matching and graph remain available with providers off.

Nothing here runs on its own schedule. Every request starts from something the user did (a menu
command, adding items) or from a Settings → Adding Items choice. Without a network, these
features fail with a message and the rest of the app works as before.

| Module | What it does |
|---|---|
| `backend/online/http.py` | What every lookup shares: timeout, User-Agent headers, `LookupFailed`, arXiv request spacing. |
| `backend/online/metadata.py` | Query classification, author normalization and routing to plugin metadata sources. |
| `plugins/metadata-sources/server/providers.py` | Crossref, OpenAlex, arXiv and Open Library requests, title fallback and author enrichment. |
| `backend/online/pdf_finder.py` | Finds a PDF of a paper (the publisher's or an open-access copy), downloads it and checks it is the right paper. |
| `backend/online/article_page.py` | Reads an article's web page for the PDF it names (`citation_pdf_url`) or the page a meta refresh moves on to. |
| `backend/online/remote_pdf.py` | Downloads the PDF behind an attachment that is stored as a web link. |
| `backend/library/identifiers.py` | DOIs and arXiv ids: cleaning, normalising, finding them in an item. Offline. |
| `backend/references/sources.py` | Validates reference-fetch requests and delegates to injected providers. |
| `plugins/reference-sources/server/providers.py` | Reference fetching order and the existing fresh/recent-arXiv policy, using an injected public HTTP client. |
| `plugins/reference-sources/server/client.py` | Public `plugin.http` transport, one retry on 429/selected 5xx, interruptible retry waits and activation shutdown. |
| `plugins/reference-sources/server/records.py` | Offline reference normalization and OpenAlex record conversion through `scholarly`. |
| `plugins/reference-sources/server/arxiv_html.py` | Reading the bibliography of an arXiv paper's HTML version. |
| `backend/references/connections.py` | How a paper outside the library connects to it (for plugins' lists). |
| `backend/references/store.py` | `data/citations.db`, where the fetched lists are kept. |
| `backend/references/service.py` | `Citations`: fetching now or in a background queue, matching references to library items. |

## Shared HTTP helpers

`backend/online/http.py` holds what the lookups share:

- `TIMEOUT` (15 s) for API requests. The PDF code uses its own limits: the PDF sources wait 10 s
  for Unpaywall, Crossref and arXiv searches; `remote_pdf.py` has
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
- `arxiv_wait()` keeps requests to arXiv (API and PDF downloads alike) `ARXIV_SPACING` (3 s)
  apart. arXiv's API terms ask for one request every three seconds, and Synapse runs several
  lookups side by side. The turns are taken from `HOST_GAPS`, a process-wide line per service that
  plugins' requests (`plugin.http`) wait in too, so Synapse and its plugins together keep the
  spacing; `spacing(host, default)` puts all of arXiv's hosts in one line. Each turn starts
  a fresh interval from the actual release time. A delayed wake-up cannot cause catch-up bursts;
  waiting callers recheck the deadline, without blocking requests to other services.

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
   Names come from each work's `raw_author_name`, falling back to the author profile's
   `display_name` only when the work's name is blank. Profile names can be incorrect or differ
   from the name credited on this paper.
2. `resolve_authors` prefers a spelling already in the library: when a fetched name matches a
   known author letter for letter once accents are removed, and only the known one has accents,
   the known spelling is used (Banuls → Bañuls). The endpoints pass `known_authors(...)` of the
   library for this.

### Reviewing the result

`POST /api/items/<id>/lookup` (`backend/api/metadata.py`) looks up an existing item and saves
nothing. It answers `{current, fetched, changes}`; the added `changes` field leaves existing clients
compatible. `fetched` also carries a `citation_key` generated with the user's pattern from the merged
item. `backend/online/metadata.py` shares this lookup and `compute_changes` with Metadata Check;
shared fixtures check that the Python and TypeScript comparisons agree. Metadata Check reuses
`openReviewDialog` with stored results and callbacks for rejecting suggestions or ignoring papers.
Only successfully saved item IDs reach its Apply callback; unchecked changes remain reviewable.

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

## Finding a PDF

`Services.find_pdf(item_id)` (`backend/services.py`) takes the item's arXiv id from
`identifiers()`, calls `pdf_finder.find_and_download`, and attaches a found file as "Full Text PDF".
It then calls `attachments_changed()` so the full-text index picks the file up.

Where to look comes from the plugins' **PDF sources** (`@plugin.pdf_source`, see
[plugins](plugins.md#the-plugin-object)), gathered by `PluginHost.pdf_candidates(paper,
by_title=…)`: sources by identifier and sources searching by title, lower `order` first, each URL
once. With no source running (both plugins below turned off, and no other), Find Available PDF
says so. Two plugins come with Synapse, on by default, and use only the public plugin API.

**Publisher PDFs** (`plugins/publisher-pdfs/server/finder.py`, order 5) asks for the version of
record first. Publishers give the PDF without a login to readers whose network has a subscription
(a university's network or its VPN), so where Synapse runs on such a network the published version
wins over any preprint or accepted manuscript. Elsewhere a paywalled attempt ends at a login page,
which is not a PDF, and the open-access sources follow. For a DOI that is not an arXiv DOI:

1. The full-text links the publisher deposited with Crossref (`api.crossref.org/works/<doi>`,
   the manifest's only host), those with `content-type` `application/pdf` that are not an
   accepted manuscript (`content-version` `am`). Text-mining APIs that need a key
   (`api.elsevier.com`, `api.wiley.com`) are left out; Wiley's `/doi/pdf/` viewer becomes
   `/doi/pdfdirect/`, the file.
2. `https://doi.org/<doi>`: the article page, which Synapse follows to its PDF (below). This
   covers publishers that deposit no PDF link (APS, Elsevier, Optica, …).

**Open-Access PDFs** (`plugins/open-access-pdfs/server/finder.py`, order 10) asks Unpaywall and
arXiv through `plugin.http` (its manifest names `api.unpaywall.org` and `export.arxiv.org`):

1. For a preprint (an arXiv id, and no DOI or only an arXiv DOI): `arxiv.org/pdf/<id>`.
2. For a DOI that is not an arXiv DOI: Unpaywall's `best_oa_location.url_for_pdf`. Unpaywall
   requests carry the contact e-mail (Settings → Online Services, else a placeholder address),
   URL-encoded.
3. For a published paper that also has an arXiv id: the arXiv PDF, after Unpaywall.
4. Searches by title: Unpaywall's search (first two results) and an arXiv title search (the
   arXiv API over https, in arXiv's line of `plugin.http`). Google Scholar is not asked: it
   has no API, and its robots.txt forbids programs to read its result pages.

`find_and_download` tries the sources by identifier first (the publisher's links and page, then
steps 1–3) and asks the title searches only if none of those was the paper: a title search finds
other papers too, and costs requests.

`_download_first` tries the candidates in order. `_fetch_candidate` downloads one with
`remote_pdf.fetch`; when the answer is a web page rather than a PDF, `article_page.links` reads
it for the PDF it names (the `citation_pdf_url` meta tag that publishers write for Google Scholar,
or a `<link rel="alternate" type="application/pdf">`) or, failing that, the page a meta refresh
moves on to (Elsevier's `linkinghub.elsevier.com`). Up to three such pages are followed, never one
twice. The cookies the sites set travel from one request to the next, as in a browser, because
publishers check them between the article page and the PDF. A response counts only if it is a
PDF (the `%PDF` header). `verify_pdf` then reads the first two pages and accepts the
file when they contain the DOI, the arXiv stamp (`arXiv:<id>`) or, failing both, the title (fuzzy
match of at least 85, with `rapidfuzz` when it is installed and a `difflib` fallback otherwise).
The check exists because candidates are often the wrong paper or a publisher's login page. A
verified file is saved as `<uuid>_downloaded.pdf` in the PDF folder. The result is
`{"status": "success", "filename", "source"}` (`source`: the candidate it came from) or
`{"status": "not_found", "message"}`.

Whose network counts is the server's: Find Available PDF reaches the publisher from the computer
Synapse runs on, so a subscription works when that computer is on the university's network or VPN
(a Synapse on a home server does not share a laptop's VPN). Requests bypass environment proxies
(see below), so an HTTP proxy such as EZproxy is not used.

Find Available PDF in the item menu calls `A.findPdf`; several items go through `A.findPdfs`
(`frontend/src/actions.ts`), one after another with 400 ms between them, skipping items that
already have an attachment. Items saved with the browser button use `Services.find_pdf_later`, a
background thread; `_pdf_lock` lets only one such search run at a time.

Publishers' sites that put a browser check in front of their PDFs (Cloudflare and the like) refuse
these downloads from the server. The browser extension's Find Missing PDFs fetches them in the
user's browser instead and sends them to `POST /api/connector/attach-pdf`, which runs the same
`verify_pdf`; see [browser extension](browser-extension.md#find_pdfsjs-missing-pdfs-through-the-browser).

## PDFs behind a web link

An attachment can be a web address instead of a file. The frontend opens it through
`GET /api/attachments/<id>/remote` (`fileUrl` in `frontend/src/api.ts`), and
`backend/online/remote_pdf.py` downloads it. The browser button's PDF link (when the extension
could not fetch the PDF itself) is downloaded the same way (`backend/connector.py`). Because this
makes the server fetch a URL, it is careful not to become a way into the local network:

- Only `http` and `https`. Every address the host resolves to must be public
  (`is_public_address`: `ipaddress.is_global`, not multicast). An IPv4 address carried inside an
  IPv6 one (IPv4-mapped, NAT64 `64:ff9b::/96`, 6to4, Teredo, IPv4-compatible) must be public
  itself, so `64:ff9b::7f00:1` or `2002:7f00:1::` cannot reach this computer. NAT64 addresses of
  public hosts stay allowed (DNS64 answers with them on IPv6-only networks); the local-use NAT64
  prefix `64:ff9b:1::/48` and site-local `fec0::/10` are refused.
- Find Available PDF uses the same guarded downloader for its candidate URLs and the article pages
  it follows, with the same redirect checks and 100 MB size cap, and the PDF header check before
  verifying the paper identity.
- The validator and transport live in `backend/online/http.py`, shared with `plugin.http`.
  The URL, as requests normalizes it, must be plain http(s): a host name or IP literal, an optional
  port, and printable ASCII after it. A user name or password in the address is refused before any
  DNS lookup, since parsers disagree about where such an authority ends.
  DNS answers are retained and sockets connect directly to those exact addresses, preventing a
  second DNS lookup from redirecting the connection into a private network. All returned addresses
  must pass validation before any connection, including when DNS mixes public and private answers.
  Connection failures may try another checked address. HTTP Host, HTTPS SNI and certificate
  verification keep the original hostname. Environment proxies and netrc are bypassed; requests'
  default CA bundle is used rather than environment CA overrides.
- Redirects are followed by hand, at most `MAX_REDIRECTS` (5), and each target is checked again,
  even when it names the same host (DNS may have changed).
- The body is capped at `MAX_BYTES` (100 MB), by `Content-Length` and while streaming, and must
  contain `%PDF-` in its first 1024 bytes.

Failures raise `RemoteError`, which carries the HTTP status the endpoint returns (403 for a local
address, 413 too large, 415 not a PDF, 502 for network trouble).

## The citation graph

### Fetching reference lists

`fetch(item)` in `backend/references/sources.py` needs a DOI or an arXiv id; without one it raises
`LookupFailed`. It delegates to the running reference providers registered with the plugin host.
**Reference Sources** is a contributed plugin, on by default and stopped in safe mode; disabling it stops its HTTP requests but
keeps `citations.db` and saved graph links. Without any provider, fetching reports how to turn one
on. Its bundled scholarly provider preserves the existing order and first-nonempty policy:

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
   It goes through `arxiv_wait()` like every arXiv request.

   On announcement day the other services often know only part of a paper's references, or none
   (measured on a day of quant-ph: Semantic Scholar had 13 of 119, 13 of 57, none of 478). So for
   an arXiv id from this month or the last (`recent_arxiv`), the HTML list is asked even when
   another service answered, and kept when it is longer. `fetch(item, fresh=True)` (a paper
   announced in the last days, as arXiv Today asks) puts the HTML version first and asks the others
   only when it has no list. `python tools/arxiv_refs.py quant-ph`
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
for them each time an item is selected. They are built under a lock, so panes asked for at once
(arrowing through the list) share one read of the library instead of each reading it, and that
read leaves out abstracts (`papers.list(leave_out=("abstract",))`): a save waits for every read
of the library to finish (see [storage](storage.md#the-library-database)).

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
| `cites` | its reference list (given by the caller) matched with `match_refs`, excluding authored matches | 3 per library paper |
| `cites_own` | a matched library reference has an author matching caller-supplied `author_names` (surname and first initial) | 6 per authored library paper |
| `cited_by` | library papers whose stored reference lists contain its DOI or arXiv id | 3 per library paper |
| `authors` | family name and first initial, normalised, shared with library papers | 0.5 per library paper, at most 3 (1.5 total) |
| `similar` | cosine similarity of title and abstract (TF-IDF over the library; words in more than a quarter of the library and stop words left out), from 0.15 | 10 × the best similarity |

The library side (author index, word weights and postings) is built once and again only when
`library_signature()` changes. `folders` limits all of it to papers in those folders.
Citation reasons distinguish authored papers from other library papers; a configured author name
alone cannot make an unmatched outside reference count as authored. Each matched reference counts
once. Name matching can still confuse namesakes. Similarity is labelled “Similar title/abstract
(TF-IDF)” and uses a search icon. This is local lexical similarity rather than an embedding model.
The arXiv plugin rescans retained scores on activation and when configured author names change.
The same score controls ordering and the notification threshold. No broad-reference discount is applied.

`build_graph()` in the Graph contributed plugin (`plugins/graph/server/build.py`, served at
`GET /api/plugins/graph/data`, using public citation-graph and permission-gated notes reads) adds the notes for the Graph view (`plugins/graph/frontend/`): `paper`, `work` and `note` nodes, and `cites`, `cite` (a note's
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
| `POST /api/connector/save` | `api/connector.py`, `backend/connector.py` | The browser button; looks up metadata when the page has a DOI or arXiv id. |
| `POST /api/outside/add` | `api/plugins.py`, `Services.add_paper` | Adds `{paper, folder_id, tags}` from a plugin's list: looked up by DOI or arXiv id (the paper's fields when that fails), duplicates checked. 201 `{item, duplicate: false}`, or 200 with the existing item and `duplicate: true`. |

## How tests fake the network

No test talks to the web. Provider tests replace `backend.online.http.public_request` with a
function that answers from canned data, using pytest's `monkeypatch`. Permission checks,
credentials, parsing and rate spacing still run. Transport tests use local HTTP/TLS servers
and synthetic DNS answers instead of an external service.

- `tests/test_metadata.py`: `FakeResp` and `fake_net()` route by host (Crossref, arXiv Atom feeds
  built by `atom()`). One test uses a `threading.Barrier` to prove Crossref and OpenAlex are asked
  side by side; another checks the arXiv spacing. `tests/fake_metadata.py` fakes only HTTP/DNS,
  keeping the public client's permissions, credentials, response parsing and spacing in the tests.
  `tests/test_public_http.py` exercises the real shared transport against local HTTP/TLS servers
  with synthetic public DNS answers: rebinding, redirect validation, Host/SNI, certificate failures,
  mixed/private addresses, failover, proxy bypass and socket cleanup.
- `tests/fixtures/metadata_author_names.json`: synthetic Crossref and OpenAlex responses where
  profile names differ from the names credited on a work. `tests/test_metadata.py` checks DOI
  and title lookups, spurious suffixes, initials, surname particles, accents, missing profiles
  and blank work-name fallbacks. `tests/plugins/test_metadata_sources.py` checks Retrieve
  Metadata and Metadata Check against both correct records and records using profile names.
- `tests/test_pdf_finder.py`: the `web` fixture answers by URL prefix and records every URL asked,
  so tests can assert the order of candidates. The PDFs are made with `make_pdf` from
  `tests/conftest.py`.
- `tests/test_references.py` and `tests/test_graph.py`: `router()` answers by a substring of the
  URL through `tests/fake_metadata.py`, so the public HTTP policy still runs. Retry waits are
  bypassed in these fixtures; dedicated lifecycle tests use actual stop events.
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

- **A new metadata source.** Register `@plugin.metadata_source` in a plugin (see
  [plugins](plugins.md)). Return normalized item fields or `None`; a lower `order` takes
  priority over the bundled Metadata Sources plugin (100). Use `plugin.http` with network permission in a
  third-party plugin. The bundled contributed providers live in `plugins/metadata-sources/server/`.
  Add a faked test, and add any new reviewable field to `LOOKUP_FIELDS` in `metadataReview.ts`.
- **A new PDF source.** Register `@plugin.pdf_source`; the core downloads and checks the candidates.
- **A new reference source.** Register `@plugin.reference_source` (API 1.1). Return
  `{"references": [...]}` with reference objects using `doi`, `arxiv_id`, `title`, `authors`,
  `year`, `venue` and `raw` where known, or `None`/an empty list inside that object for no match.
  Lower `order` runs first. Declared sub-sources are qualified by plugin and registration;
  fixed host aliases retain the bundled providers' historical source names. Use `plugin.http` and fake its
  responses in tests. See [Plugins](plugins.md).
- **A new identifier rule** shared by several packages goes in `backend/library/identifiers.py`,
  with a test in `tests/test_identifiers.py`.
- **A new setting** (an e-mail, a key) goes in `DEFAULTS` in `backend/settings.py` and on the
  Online Services page (`frontend/src/views/settings/page.ts`); read it in the caller and pass it
  in.

Metadata providers are the default-on **Metadata Sources** contributed plugin: Crossref, OpenAlex, arXiv and Open Library. Its `server/providers.py` owns HTTP and lookup orchestration; `server/parsing.py` converts provider records into library fields. `backend/online/metadata.py` keeps query classification, title matching and author normalization; the running plugin sources supply metadata. Existing HTTP endpoints and responses are unchanged. Turning all matching sources off gives a readable error without a network request; library records are kept.

The plugin imports only `backend.plugins`. Its manifest declares `api.crossref.org`,
`api.openalex.org`, `export.arxiv.org` and `openlibrary.org`; calls use `plugin.http`, its current
Online Services credentials/User-Agent and conditional cache. The existing parallel Crossref and
OpenAlex DOI requests remain parallel. arXiv uses the shared queue once per request, without a
second plugin-specific wait. Other hosts now follow the host's default 0.5 s spacing, so batches
of Crossref/OpenAlex/Open Library lookups can take longer than before.

Each activation owns its provider and executor. Disable/restart closes that instance and cancels
queued author enrichment; an in-flight HTTP call may finish within its timeout, but its retired
provider does not return the result as a successful lookup. Restart creates a new executor.
Malformed required-provider data produces a readable error; optional OpenAlex failures retain the
Crossref result. Stored fields, query normalization and the HTTP API have no migration.

Metadata Sources remains on by default only for the bundled copy's first discovery. Existing
plugin choices are preserved; an installed copy waits for user enablement. Safe mode now stops
it, as it does other contributed plugins.


## Named reference providers and compatibility

Plugin API 1.5 adds declared sub-sources to `plugin.reference_source` and exposes the existing
reference-record field contract as `scholarly.REF_FIELDS`. Registrations without declarations
retain their API 1.1 behavior. A selected declared source receives a qualified ID, except for the
host's four compatibility aliases for the bundled `reference-sources.scholarly` registration.
Those aliases preserve `semanticscholar`, `openalex`, `crossref` and `arxiv-html` exactly on disk
and in `fetch.source`; no existing citation rows are rewritten.

The app's reference response may add `fetch.source_title`. Services inject the plugin host's
label resolver into `Citations`; storage stays independent of plugins and still stores only the
source identity. Historical labels work with fetching disabled or the plugin missing. Other
providers get their declared titles while running, and unknown stored identities fall back to
their source ID. The browser escapes titles and retains its own legacy fallback labels for an
older server. See [the plugin contract](plugins.md#named-reference-sub-sources).


## Reference Sources as a contributed plugin

The bundled plugin requires API 1.5 and declares only `api.semanticscholar.org`,
`api.openalex.org`, `api.crossref.org` and `arxiv.org`. Credentials and User-Agent come from
`plugin.http` at every request. No provider imports internal backend modules or reads
`plugin.synapse`. Parsing, source selection and the stored record format are preserved.
The host's fixed aliases keep all four recorded source names and their titles unchanged; there
is no citation database migration or rewrite. Existing enabled/disabled choices survive;
first discovery enables only the bundled copy. Safe mode stops fetching while saved graphs and
references remain readable.

Every activation owns a small client with a stop event. As before, scholarly APIs retry once on
429, 500, 502, 503 and 504; numeric Retry-After is clamped to 0–10 seconds and a nonnumeric value
uses 2 seconds. arXiv HTML retains its no-retry policy. Stop interrupts a retry wait, prevents the
next fallback request, and discards an in-flight response when it returns. In-flight DNS/network
work already accepted by the shared host transport finishes under that transport's policy.
Restart creates a fresh client without waiting for the old HTTP response. Failed refreshes keep
previously saved lists; HTTP error messages include the service host and status, not credential
query strings. Repeated Semantic Scholar pagination cursors are treated as a provider failure
and allow fallback instead of looping indefinitely.

All requests now use the shared host spacing and conditional cache. arXiv enters its shared
3-second queue once per request; the separate arXiv wait was removed. Other hosts use the host's
0.5-second default spacing, so multi-page/batched reference fetching can take longer than the
previous unthrottled direct requests.
