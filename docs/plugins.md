# Plugins

A plugin adds a feature to Synapse without changing Synapse's own code: a folder with a
`manifest.json`, some Python for the server and some JavaScript for the app. Each part gets an
object to register what it offers. This page is both the guide for writing one and the description
of how Synapse runs them.

This page documents API **1.14**, which also accepts plugins targeting 1.0–1.13. Reference
providers and export writers were added in 1.1, Synapse's reference writers
(`backend.plugins.formats`) in 1.2; plugins using them should declare `"api": "^1.1"` or
`"^1.2"`. Loopback HTTP and editor suggestions require `"^1.3"`; offline scholarly helpers require `"^1.4"`. Named reference sub-sources and the reference record
contract require `"^1.5"`. Metadata review, progress jobs, library editing and field scheduling helpers require `"^1.6"`. Notes reads and visualizations require `"^1.7"`. The phone layout's additions (a page's
`phone` layout, `p.capture`, `p.editor.toolbar`, `role: 'dismiss'` and a paper's `group`) require
`"^1.11"`. Local services (`plugin.http.local_service`) require `"^1.12"`. PDF sources that return an article's page rather than its PDF require `"^1.13"`.
What exists today:

- **Backend** (Python): routes, tools for Claude, events from the library and notes, reading the
  library, settings, stored data, **per-item fields** (values attached to papers that become list
  columns, search filters, item-pane rows and data for Claude), schedules and background jobs, the
  web (within the hosts its manifest names), Python packages of its own, a log, **lists of papers
  outside the library** (shown like folders, with a "+" that adds a paper), and Synapse's own
  machinery: looking papers up, their reference lists, **how a paper connects to the library**,
  adding papers.
- **Frontend** (JavaScript): pages with a sidebar entry, commands in the command palette (with keys),
  sections in the item pane, actions in the item menu, notifications, calls to its own routes.

## Using plugins

- Put a plugin's folder in **`data/plugins/`** (next to `library.db`), then open
  Settings → Plugins and press **Look Again**. A new plugin is listed but always off.
- Turning one on asks first and lists what it may do (read the library, connect to sites, offer
  tools to AI apps). A plugin runs with full access to the library and the computer, like any
  program you install: turn on only plugins you trust.
- Each plugin has a **page of its own** in Settings, shown beside the Plugins list when you pick
  its row (or with "Settings: <name>" in the command palette): its switch and what it may do,
  and while it is on its settings (drawn from its manifest, saved as you change them, and
  whatever its frontend draws there), whether its tools are also offered to AI apps on other
  computers (`/mcp`, off by default), what it serves and listens to, **Restart** (to load changed
  code) and its **Log**. A plugin that is off shows what it may do and its log.
- A plugin that fails while starting, or fails three times within ten minutes, is turned off and
  Settings says why. Turning it on again tries again.
- Enabling, disabling, restarting or reconfiguring a plugin runs or changes code on the computer
  running Synapse and lasts, so from another device it needs a passkey confirmation, like pairing a
  device or making an access token (the `admin.refusal` gate in `backend/api/plugins.py`; see
  [remote access](remote-access.md)). Settings → Plugins and a plugin frontend's `saveSettings`
  ask for the passkey when the server wants one (`asAdmin`); cancelling leaves the plugin as it
  was. Reading the list of plugins does not need it.
- `python server.py --safe-mode` stops bundled and installed plugins, keeping the basic library
  and notes available when a plugin keeps the app from working. Plugin settings and enabled
  choices are preserved; plugin controls are disabled until Synapse starts normally.
  `mcp_server.py --safe-mode` also stops these plugins for the MCP server an AI app starts.
- Restart the AI app after turning on a plugin with tools if they do not show up: some AI apps read
  the list of tools only once.
- A plugin's pages and lists of papers are listed in the sidebar with Library and Notes; its commands
  are in the **command palette** (⌘⇧P, Ctrl+Shift+P or F1), together with Synapse's own.
- A plugin's **list of papers** (today's arXiv papers, say) shows like a folder, in the order the
  plugin gives (click a column to sort, a third click goes back). Papers already in the library are
  the library items; the others have a **+** that adds them (looked up online by DOI or arXiv id,
  with Undo in the notification center), and the item pane says why the plugin lists them, linking the library
  papers involved. Right-click (or select several) to add them to a folder. Adding runs what
  Settings → Adding Items says ("Ask" counts as yes: there is no dialog to ask in).

Plugins that come with Synapse live in the repository's `plugins/` folder. A plugin in
`data/plugins/` with the same id replaces the one that comes with Synapse.

### Plugin Settings and legacy core status

Settings shows bundled and installed plugins in one **Plugins** list. Bundled rows say
"comes with Synapse"; development packages and replacements retain their source labels.
Its navigation expands the individual plugin sections only while the Plugins area is in view,
and otherwise keeps a single top-level **Plugins** entry.

All currently shipped optional feature plugins use the public API and are contributed plugins.
None has core status, so safe mode stops every currently shipped plugin as well as installed
plugins. The host still retains the following internal compatibility paths for `"core": true`
in bundled manifests; installed plugins cannot claim that status. These paths are not used by
current plugins and are separate from the Settings list. A legacy core plugin has these privileges:

- It may import Synapse's own modules (`backend.…`, and in the frontend the app's modules), since
  it ships and changes with Synapse; `tests/test_layers.py` leaves it out of the import rule.
- Its frontend is part of the app's own build: `"frontend": "core:<name>"` names
  `frontend/src/plugins/core/<name>/index.ts` (listed in `frontend/src/plugins/core/index.ts`),
  which exports `activate(p)` like any plugin frontend and gets the same `PluginContext`.
- It starts on the first time Synapse sees it, unless its manifest says `"enabled_by_default":
  false`; `"enabled_from_setting": "<a former Synapse setting>"` starts it as that switch was
  instead. A setting with `"from_setting"` takes over the value of the Synapse setting it replaces
  (`settings.json` keeps such values, `SettingsStore.legacy`). After that, turning it on and off is
  the user's choice, as for any plugin.
- Its tools for Claude keep the names they had in Synapse (no `<plugin>__` prefix), its commands
  may use Synapse's own keys (which other plugins cannot take, even while it is off), and its
  commands and actions are not labelled with a plugin name.
- Its server part gets the app's `Services` object as `plugin.synapse` (None for other plugins).
- It keeps running in safe mode (`--safe-mode` stops the other plugins only).

Tasks and the host bridge for contributed Notes visualizations register sidebar entries in the internal `navigation`
registry in `frontend/src/extend.ts`, and remove them on unload. The sidebar only renders those
registrations; it does not own plugin-specific buttons or counts. Each entry determines its active
state from the visible workspace as well as its retained view state, so leaving Graph for Tasks or
the library clears its highlight even though Notes keeps the graph in memory. Third-party plugins
can use `p.pages`, backend item sources, or the public Notes visualization API. Extended Markdown and Autocompletion are editor integrations, Citation
Counts adds item fields, and metadata/PDF plugins supply lookups; those do not create empty pages.

Workspace transitions are coordinated by `main.ts`: opening a plugin page first saves and closes
Notes and leaves Tasks. `views/pages.ts` owns mounting, cleanup and the short restart grace period;
choosing another view cancels that wait. Graph and Notes never highlight simultaneously.
`nav.ts` records library views, Graph, Tasks and plugin pages without intermediate workspace entries.
Back/Forward to a stopped page, Tasks or paper list falls back to the library; a stopped Graph
falls back to Notes. These unavailable history entries are replaced rather than adding a new entry.
Tasks records its destination before loading, and a delayed load cannot focus or replace a later view.

A plugin's custom Settings sections are kept in step with the registry while Settings is open.
Stopping one disposes and removes its controls; starting it again mounts one fresh section. If the
selected section disappears (a plugin's page goes when the plugin is removed), Settings shows
General, or the Plugins list beside another plugin. During a Settings search, the query is
kept and the remaining matching sections or the empty state are shown instead. Unrelated
sections keep their mounted state.
Autocompletion also clears visible and pending suggestions when stopped; an older status request
cannot overwrite a newer check.

### Bundled contributed plugins

Some features Synapse always had are **contributed plugins**: they use only the public plugin API
(`backend.plugins`, `plugin.http`), as an installed plugin would, so they show what a plugin can
do and keep that API honest. Open-Access PDFs, Export Formats, Metadata Sources and Reference Sources start on the
first time Synapse sees them, because their manifest says `"enabled_by_default": true` (honoured only for plugins in
`plugins/`; an installed plugin always waits to be turned on). After that they are the user's
choice, and safe mode stops them like any contributed plugin. Settings lists them alongside
installed plugins under **Plugins**.

- **Tasks** (`plugins/tasks`, on by default): the Tasks workspace under **Plugins** in the left
  sidebar, task controls in notes,
  and the existing `list_tasks`, `get_task`, `create_task`, `update_task`, `delete_task` tools.
  Records and note links stay in the core for validation, backups and attachment retention.
  Disabling it removes its controls and tools; reenabling restores them with all tasks intact.
  Its routes are `/api/plugins/tasks/…`; `/api/tasks` remains a compatibility alias. See [tasks](tasks.md).

- **Extended Markdown** (`plugins/extended-markdown`, frontend only, on by default): Markdown
  syntax beyond the basics, one extension per folder in
  `plugins/extended-markdown/frontend/`, each with a boolean setting of its own:
  - **Galleries and numbered figures** (`figures`): ` ```gallery ` blocks drawn as carousels (one
    figure with panels in exports), figures numbered Fig. 1, 2, … and `\ref` / `\cref` to them;
    see [notes](notes.md#galleries). Off, a gallery reads as the images and captions it holds, an
    image's caption as the line of text it is, and a reference to a figure as ??. The notes format
    does not change: the parser and the attachment sweep still read a gallery's lines.
  - **Spoilers** (`spoilers`): inline `||spoilers||` and collapsible `:::spoiler` blocks in
    Live/Read modes. Disabling it renders ordinary Markdown
  without changing note files. HTML/PDF export reveals spoiler contents; Markdown keeps the source.

- **Graph** (`plugins/graph`, on by default): local note neighbourhoods and the full graph in
  Notes, with the existing sidebar entry, note/item menus, history and Cmd/Ctrl+Shift+G. It uses
  public citation and note reads, the Notes visualization API and the host-vendored offline
  force-graph library. Its data route remains `/api/plugins/graph/data`; graph data is derived
  from saved notes and references, with no migration. The host retains the bundled Graph toggle's
  reserved shortcut only; installed replacements receive ordinary shortcut rules. Saved Graph
  preferences and enabled choices survive. Safe mode stops the plugin, preserving all inputs.

- **Reference Sources** (`plugins/reference-sources`, on by default): Semantic Scholar, OpenAlex,
  Crossref and arXiv HTML citation lists through the public API 1.5, declared HTTP permissions and
  named sub-sources. Host aliases preserve historical source keys and titles. Saved references,
  matching and graphs remain in core when off or in safe mode. Retry waits can be interrupted
  when the plugin stops, and retired in-flight results are discarded. See [references](references.md).
- **Metadata Sources** (`plugins/metadata-sources`, on by default): Crossref, OpenAlex, arXiv and
  Open Library lookups through `plugin.http`, with exactly those four API hosts declared. API 1.4's
  `scholarly` module supplies shared identifier, author-name and title-matching rules. Parallel
  DOI/author enrichment and title fallback are preserved. Turning it off or entering safe mode
  preserves library records; lookups still work when another matching provider is enabled.
  Existing enabled/disabled choices survive the conversion. See [references](references.md).
- **Autocompletion** (`plugins/autocompletion`, off by default or as previously configured): local
  Ollama suggestions through `plugin.http` with `network: ["loopback"]` (and the server's local
  service `ollama`, API 1.12) and the public editor suggestion API. It stops in safe mode; settings survive. Legacy `notes_ai_*` settings are
  carried over once by the host for the bundled plugin only. See [autocompletion](autocompletion.md).
- **Publisher PDFs** (`plugins/publisher-pdfs`): Find Available PDF tries the publisher's own PDF
  first, by the PDF links publishers deposit with Crossref and the DOI's article page (its PDF
  source, order 5, see [references](references.md#finding-a-pdf)); behind a subscription this works
  where the server's network has access, such as a university's network or VPN. Network permission
  for `api.crossref.org`; Synapse itself fetches the publisher's pages and PDFs, as it does every
  candidate. Turn it off if you have no subscription access and want fewer requests per search.
- **Open-Access PDFs** (`plugins/open-access-pdfs`): where Find Available PDF looks next, Unpaywall and
  arXiv by identifier and by title (its two PDF sources, see
  [references](references.md#finding-a-pdf)), through `plugin.http` (network permission for
  `api.unpaywall.org` and `export.arxiv.org`). Turned off, or in safe mode, Find Available PDF has
  nowhere to look unless another plugin offers a source.
- **Export Formats** (`plugins/export-formats`): the existing BibTeX, RIS, EndNote tagged/XML and
  CSL JSON library exports, registered with Synapse's own writers through the public
  `backend.plugins.formats`, so the output is what it always was. Imports, clipboard citation
  copying and note HTML/PDF/Markdown exports remain in core. Its formats are named like any
  plugin's, `export-formats.bibtex` and so on; the names they had before (`bibtex`, `ris`,
  `endnote`, `endnote_xml`, `csljson`) still reach them through `/api/export`, and a saved
  *Default export format* is renamed once when the settings are read (`RENAMED_EXPORT_FORMATS` in
  `backend/settings.py`). With it off, imports still work, the stored export preference survives
  and the dialog explains how to enable a writer.

### Optional plugins that come with Synapse

These are contributed plugins, off until turned on; safe mode stops them:

- **Citation Counts** (`plugins/citation-counts`): how often each paper is cited, from OpenAlex
  and Semantic Scholar, as a *Citations* column, a search filter (`citations:>100`, also in smart
  folders and for Claude) and a row in the item pane. New papers get their count soon after they
  are added; counts are refreshed weekly. Papers with a DOI are asked of OpenAlex first, arXiv
  preprints of Semantic Scholar first (it counts a preprint and its journal version together); its
  setting turns Semantic Scholar off. It uses the keys in Settings → Online Services, if any.
  Semantic Scholar's `400` response with `error: "No valid paper ids given"` means that none of
  the batch's papers is indexed there. The plugin continues to OpenAlex's arXiv fallback;
  papers neither service knows keep an unknown count and are checked again at the normal
  weekly refresh. Other HTTP errors retain the existing retry behavior.
- **Metadata Check** (`plugins/metadata-check`): a *Metadata Check* text field refreshed through
  the active metadata providers, every three days by default (setting *Days between checks*, applied
  without a restart). It compares every field the Retrieve Metadata dialog compares without folding case,
  accents or punctuation. Existing citation keys remain opt-in and do not trigger a flag by themselves.
  Runs show progress in the notification center and notify with a desktop alert and a
  **Review** button when papers have differences, or end silently when all are up to date. Clicking
  Review (from the notification, palette, or Settings) opens the Retrieve Metadata review popup fed from
  stored findings without re-fetching, allowing per-suggestion Accept, Reject (remembered per-field),
  editing, or ignoring the paper altogether. Its Settings section offers **Run Now**, **Review Changes**
  (with a pending badge), and lists of both **Ignored papers** and **Rejected suggestions** with
  **Stop Ignoring**. What each check found is kept in `findings.json`, the ignores in `ignored.json`, and
  ignored papers in `ignored_papers.json` (all in backups). Rejections remember complete field values,
  including long abstracts; older normalized rejections are migrated from their saved display values.
  Old findings without a full metadata payload are refreshed once. Applying only selected fields keeps
  the remaining suggestions available, and Stop Ignoring a paper restores its cached findings without
  another lookup. Progress counts only items due in that run, counts failed attempts separately, and
  clears interrupted runs on restart. An unreachable service keeps its previous field value for retry;
  failed attempts produce a warning rather than an all-up-to-date message.
- **arXiv Today** (`plugins/arxiv-today`): the papers announced on arXiv in the categories you
  choose in its settings, under one **arXiv Feed** entry. Opening it shows a separate sidebar beside
  the papers for the retained announcement days (latest first, 30 days by default), without
  crowding the main sidebar. They are ranked by their **match** (below), with the reasons in the
  item pane; each paper's reference list is read from its HTML version on arXiv in the background
  (one request every 3 seconds), so the match sharpens as the morning goes on. A notification
  says "12 new papers: 3 to read, 4 to skim" once a day is scored. Right-click a paper to **Dismiss** it (⌘⌥D / Ctrl+Alt+D) or open its PDF on
  arXiv; ⌘⌥T / Ctrl+Alt+T shows the list, and the palette has **arXiv: Fetch Today's Papers
  Now**. Its fields *arXiv Match*, *Announced* and *arXiv Categories* are columns and filters
  (`categories:quant-ph`, `score:>70`) and stay with a paper you add. Claude can ask for a day's
  papers with its tool `arxiv-today__today`, and compare the match with the earlier score on the
  kept days with `arxiv-today__match_report`. The feed (rss.arxiv.org) is read after each
  announcement (Monday to Friday, 00:30 US Eastern), at the next start when the computer was off,
  and when the categories change. If arXiv's cache serves a feed dated for the new day but
  listing only earlier papers, the plugin asks arXiv directly past the cache. A half-hourly check keeps retrying during announcement weekdays
  until a nonempty listing dated that day is stored, including after empty/stale RSS responses or
  exhausted network retries. Holidays or categories without new papers may therefore keep checking
  that day. A fetched day stops these checks; Fetch Now still forces a request.
  Day labels use the browser's calendar date and refresh every minute and on visibility changes;
  the newest stored day is only labelled Today if its date is today.
  Cross-lists are in by default, replacements not.
  **Followed authors** accepts full publication names, one per editable row. **Categories** and
  **Your author names** use the same Add/Remove list editor; Enter adds another row.
  Matching papers show a gold **Following** badge beside their title, with the matched names in
  its tooltip and in *Why It Is Here*, including papers already added to the library. Matching
  ignores case, accents and extra whitespace, but requires the full name (add published variants
  separately). Changes apply immediately to all retained days, offline, without altering scores
  or ordering. An empty list turns the Following badges off. A Synapse red **Cites you** badge marks
  papers with a matched citation to a library paper identified by **Your author names**. Papers
  can carry both badges. Retained days and the main arXiv Feed entry show matching circular
  indicators: yellow with a white star for Following, Synapse red with a solid white megaphone for Cites you.
  Only today's badges also appear in the main sidebar; older days keep their own indicators.
  Day tooltips report paper counts. Dismissed papers do not contribute; Undo restores them.
  **Your author names** accepts publication names in Given Family order; library papers by
  these names are yours (full first names must agree when both sides have them; with initials
  only, namesakes can match).

  *The match* (`backend/scoring.py`) is 0–100, how likely you are to want to read the paper:
  `100 · σ(−2.5 + Σ weight × feature)`, so no evidence gives 8. It rests on how *specific* and how
  *personal* a paper's links to the library are, not on how many there are. Each library paper is
  an anchor weighted by **interest** × **specificity**². Interest starts at 1 and is multiplied by
  3 for a paper you wrote, 2 when a note cites it or is its paper note (2.5 when that note changed in
  the last 60 days), and 1.5 when it was added in the last 90 days or from the feed, capped at 4.
  Specificity is how rarely the papers announced in your categories cite it (from 0 to 1), counted
  on the reference lists the plugin read over the last 180 days (`background.py`, kept longer than
  the listings; filled from the kept listings on the first start) and shrunk toward 0.5 while
  that background is small, so a textbook everyone cites counts little and a niche paper a lot.
  Six features, each with diminishing returns: the anchors a paper cites (log(1 + Σ interest ×
  specificity²), weight 3.5); papers you authored that it cites (weight 5, enough for Read on its
  own, and the **Cites you** badge); rare references it shares with your library's own reference
  lists, also works outside the library (weight 1.5); its authors who wrote library papers,
  matched by full name and discounted by how common the name is among announced papers (weight
  1.5); title and abstract against the library (TF-IDF, top five, weighted by interest), counted
  only where it stands out from that day (a z-score above 1.5, weight 0.8); and −0.4 for
  cross-lists. A library paper is never compared with itself. Papers from 70 are in the **Read**
  tier and from 35 in **Skim** (settings *Read from match*, *Skim from match*); the first reason
  in the item pane names the match and tier, the others the evidence, strongest first. Followed
  authors do not change the match. While the new match is checked, the earlier score
  (`plugin.services.connections`) is still computed and shown beside it, and the report tool
  compares where added and dismissed papers ranked under each. Changing author names or the
  tier edges rescores all retained days, as does starting the plugin. The item pane's **arXiv
  Match** section shows the match, its tier and one bar per kind of evidence (what it added, in
  log-odds).

  *Learning* (`backend/learning.py`, setting **Learn from what I do**, on by default): each night at
  03:20 (the server's time) the plugin turns what you did into labels and refits how much each
  kind of evidence counts. A paper added to the library on or after its announcement is wanted
  (strength 1); one opened, shown in the item pane for 4 seconds or its PDF opened from the feed, is
  probably wanted (0.3); a dismissed one is not wanted (1); one listed above a paper you opened, and
  never opened itself, was passed over (not wanted, 0.3). Papers below the last one you opened get
  no label, and a day you never opened teaches nothing. Labels are kept a year, longer than the
  listings. The fit is a logistic regression with the baseline held fixed, so the tiers keep their
  meaning; wanted and unwanted labels weigh the same in total, and an L2 prior (worth about ten
  labels) pulls the weights toward the hand-set ones. Weights stay between 0 and three times their
  hand-set value (the cross-list penalty between that and 0). It needs three wanted and three
  unwanted labels before it learns at all; then the retained days are rescored. The plugin's
  Settings section lists the hand-set and learned weights, with **Learn Now** and **Reset to
  Hand-set**.
  OS notices require an open running page; background phone push is not supported.
  Its TypeScript frontend (`frontend/main.ts`) imports public Plugin API types and is included
  in `npm run check`. The host bundles it when enabled; no generated bundle is committed.

## Writing a plugin

Start with the [plugin authoring quickstart](plugin-authoring.md) and its runnable Hello Library
example; return here for the complete API reference.

Start from a working one:

```bash
python server.py --new-plugin my-plugin --dev-plugins ~/synapse-plugins   # makes ~/synapse-plugins/my-plugin
python server.py --dev-plugins ~/synapse-plugins                          # then turn it on in Settings
```

`--dev-plugins DIR` loads the plugins in `DIR` besides the installed ones and **restarts one
whenever one of its files changes** (Python, TypeScript, the manifest): the open windows load its
new frontend by themselves. `--new-plugin` (without `--dev-plugins`, into `data/plugins/`) makes a
plugin with a Python backend (a route, a tool for Claude, an event listener, a schedule), a
TypeScript frontend (a page and a command), the frontend's type declarations (`frontend/api.ts`)
and a README. The MCP server takes `--dev-plugins` too (it does not watch for changes: restart
Claude).

The example in [`docs/examples/recently-added/`](examples/recently-added/) uses every part of the
API once, with a JavaScript frontend; copy it into `data/plugins/` to try it.
[`docs/examples/suggested-reading/`](examples/suggested-reading/) offers a list of papers from
outside the library (see [Papers outside the library](#papers-outside-the-library)). A plugin folder looks
like this:

```
data/plugins/recently-added/      the folder's name is the plugin's id
  manifest.json
  backend/                        a package (or a single .py file)
    __init__.py                   def activate(plugin): ...
    store.py
  frontend/
    main.js                       export function activate(p) { ... }
    style.css
```

### The manifest

```json
{
  "id": "recently-added",
  "name": "Recently Added",
  "version": "1.0.0",
  "api": "^1.0",
  "author": "…",
  "description": "Remembers when each item was added …",
  "backend": "backend",
  "permissions": { "library": ["read"], "mcp": true },
  "settings": [
    { "key": "keep_days", "type": "int", "label": "Remember for", "default": 90, "min": 1, "max": 3650 },
    { "key": "skip_tags", "type": "multi", "label": "Ignore items tagged", "default": [] }
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | 2–64 lowercase letters, digits and `-`; must be the folder's name. |
| `name`, `version` | Shown in Settings. Required. |
| `api` | The plugin API it was written for: `"^1.0"` runs on any 1.x from 1.0 on. A plugin needing a newer minor version, or another major version, is listed but cannot be turned on. |
| `backend` | Its Python code: a package folder (with `__init__.py`) or a `.py` file, inside the plugin's folder. |
| `frontend` | Its part in the app: an ES module (`.js`), or TypeScript (`.ts`, `.tsx`) that Synapse bundles with esbuild when the plugin starts (see [The frontend](#the-frontend)). |
| `styles` | A stylesheet the app loads while the plugin runs. |
| `permissions` | `network`: the hosts `plugin.http` may reach (`"export.arxiv.org"`, or `"*.example.org"` for a domain and its subdomains). `notes`: `read` for note listings/links, `edit` for supported history-preserving note operations; `tasks`: `read` and/or `edit` for task operations; `library`: `read` (`plugin.library`), `add` (`plugin.actions.add`), `edit` (`plugin.actions.update`, `p.library.update` and review Apply). `mcp`: true to offer tools to Claude. Unknown permissions make the manifest invalid. |
| `python` | Python packages it needs, as requirements (`["scirate>=0.0.7"]`): installed with pip when it first starts (see [Python packages](#python-packages)). |
| `settings` | What the user can set, see below. |
| `enabled_by_default` | `true`: start on the first time Synapse sees it. Honoured only for the plugins that come with Synapse (`plugins/`); an installed plugin waits for the user. |

Each setting has a `key` (lowercase, `_`), a `type`, a `label`, an optional `description` and
`default`:

| Type | Value | Extra |
|---|---|---|
| `bool` | true / false | |
| `int`, `float` | a number | `min`, `max` |
| `text` | a string (≤ 10,000 characters) | |
| `choice` | one of `options` | `options`: `["a", "b"]` or `[["a", "Label A"], …]` |
| `multi` | a list of strings | `options` (then the user ticks them) or none (editable rows with Add/Remove and Enter to add) |

### The `plugin` object

`activate(plugin)` registers everything; `deactivate(plugin)`, if the module has one, runs when the
plugin is turned off. Plugin code imports from Synapse only `backend.plugins` (`Plugin`,
`PluginError`, `EVENTS`, `API_VERSION`); the rest of `backend` may change between versions, and
`tests/test_layers.py` checks the plugins in the repository for this.

```python
from backend.plugins import Plugin, PluginError


def activate(plugin: Plugin) -> None:
    db = plugin.storage.sqlite("added", migrations=[SCHEMA_1])

    @plugin.on("item.added")
    def remember(event: dict) -> None:
        for item_id in event["ids"]:
            item = plugin.library.get(item_id)
            ...

    @plugin.route("/recent")  # GET /api/plugins/recently-added/recent
    def recent():
        return {"items": db.query("SELECT * FROM added")}

    @plugin.mcp_tool("Items added to the library in the last days.")
    def recently_added(days: int = 7) -> dict:  # Claude sees recently-added__recently_added
        if days < 1:
            raise PluginError("days: at least 1")  # Claude reads this and can try again
        ...
```

| Member | What it does |
|---|---|
| `plugin.id`, `plugin.manifest` | The plugin's id and its parsed manifest. |
| `plugin.role` | `"app"` in the web app, `"mcp"` in the MCP server Claude starts. Both run the plugins that are on; only the app sends `app.started` and serves routes. |
| `plugin.log` | A `logging.Logger`; its lines go to the plugin's log (Settings → Plugins → Log) and to the server's output. |
| `plugin.settings` | `plugin.settings["keep_days"]`, `.get(key)`, `.all()`: the user's values, else the defaults. `@plugin.settings.on_change` gets `{key: new value}` of the keys that changed. |
| `plugin.storage` | `.json(name)` a small JSON object (`get`, `set`, `update`, `delete`, `all`); `.sqlite(name, migrations)` a database (`session()` for a transaction, `query(sql, params)`); `.dir` a folder for anything else. All in `data/plugin-data/<id>/`, which is in backups. |
| `plugin.library` | Read access (needs `"library": ["read"]`): `get(id)`, `items(ids=None)`, `search(query, limit=50, fulltext=False)` in the library's search syntax, `find(doi=…, arxiv=…)`, `folders()`, `tags()`. Plain dicts, as the JSON API answers. |
| `@plugin.route(rule, methods=("GET",))` | Serves the function at `/api/plugins/<id><rule>` (Werkzeug rule syntax: `/items/<item_id>`). It returns a dict or list (sent as JSON), text, `None` (`{"ok": true}`) or a Flask response; `flask.request` has the request. The route sits behind the same guard as the app's own (this computer, paired devices, the app's own pages for changes). |
| `@plugin.mcp_tool(description, name=None, title=None, schema=None, read_only=True)` | Offers the function to Claude as `<id>__<name>` (needs `"mcp": true`). The argument schema comes from the type hints (`str`, `int`, `float`, `bool`, `list[str]`, `Literal[…]`, optional with a default) unless `schema` is given. |
| `plugin.on(event, fn)` / `@plugin.on(event)` | Calls `fn(payload)` after the event, on the plugin's own thread. Called directly it returns a function that removes the listener. |
| `plugin.on_unload(fn)` | Calls `fn()` when the plugin is turned off (to stop a thread it started, say). |
| `@plugin.schedule(name, cron=…, every=…, tz=None, catch_up=True)` | Runs the function on a cron rule or every so often, in the web app (see [Background work](#background-work)). Also `plugin.schedule(name, fn, …)`. |
| `@plugin.job(name, retries=3)` / `plugin.jobs.enqueue(name, *args, key=None, delay=0)` | A job handler, and queuing work for it; `plugin.jobs.pending()` lists what waits. |
| `plugin.fields` | `declare(name, type=…, label=…, refresher=…, refresh=…)`, `set(paper, name, value)`, `set_many(name, {paper: value})`, `get(paper, name)`, `key(paper)`, `clear(name, papers=None)`: values attached to papers (see [Per-item fields](#per-item-fields)). |
| `plugin.http` | `get(url, params=…)`, `post(url, json=…)`, `request(method, url, …)`, `limit(host, seconds)`: the web, within the manifest's `network` (see [Reaching the web](#reaching-the-web)). `local_service(name)` (API 1.12): the address of a service the server was started with, or `None`. |
| `@plugin.item_source(id, title=…, icon="list", description="", badge=None)`, `plugin.refresh_sources()` | A list of papers in the sidebar, and saying it changed (see [Papers outside the library](#papers-outside-the-library)). |
| `@plugin.metadata_source(id, title=…, kinds=("doi", "arxiv", "isbn", "title", "pmid"), order=100)` | `fn(query)` receives `kind`, normalized `value`, and the contact `email`. Return an item dict in Synapse's field format, or `None` for no match. Lower order runs first; the first nonempty result wins. Raise `PluginError` for an unavailable source or a readable lookup problem; later sources are still tried. The bundled Metadata Sources plugin uses order 100. Sources are removed when their plugin stops and listed as `metadata_sources` in its info. Use `plugin.http` and declare network permission for a third-party provider. |
| `@plugin.pdf_source(id, title=…, by_title=False, order=100)` | Where Find Available PDF may look: `fn(paper)` gets `{"title", "doi", "arxiv"}` and returns PDF URLs, best first (API 1.13: or an article's web page, whose `citation_pdf_url` Synapse follows); Synapse downloads them in turn and keeps the first that is the paper (it checks the DOI, the arXiv stamp or the title in its text). Sources `by_title` are asked only when those going by identifier found nothing. Lower `order` first (Publisher PDFs uses 5, Open-Access PDFs 10). For an institutional repository or a proxy, say. |
| `@plugin.reference_source(id, title=…, order=100, subsources=None)` | API 1.1: `fn(query)` receives `item` (a copied library-style record), contact `email` and `fresh` (prefer newly announced-paper sources). Return `{"references": [...]}` or `None`. Lower order first; empty results and failures fall through. No active source raises a readable lookup error. API 1.5 adds declared named sub-sources; fixed host aliases preserve historical bundled names (below). |
| `@plugin.export_format(id, title=…, extension=…, mimetype=…)` | API 1.1: `fn(items, options) -> str` registers a text export. Options contain `unicode`, `abstract` and citation-key `pattern`. Keys are `<plugin>.<id>` (a core plugin's would keep its bare id). Writers disappear on unload. Extensions and MIME types are validated. Imports remain independent. To write a format Synapse knows, use `backend.plugins.formats` (below). |
| `plugin.services` | `contact_email()`, `lookup(identifier)`, `references(paper, new=False)`, `match(refs)`, `connections(papers, refs=None, folders=None, author_names=None)`: Synapse's own lookups, reference lists and scoring. |
| `plugin.actions` | `add(paper, folder_id=None, tags=None)`: adds a paper as the "+" does (needs `"library": ["add"]`). API 1.6: `update(id, changes)` edits metadata with `"library": ["edit"]`, using the library validation, citation-key rewrite and change events. Unknown/read-only fields and structured values are rejected before writing; tags and folder membership use separate app operations and are not accepted here. |

Raise **`PluginError("…")`** for a problem the user or Claude should read: a route answers 400 with
it as the error, a tool returns it as an error Claude can act on, and it does not count as the
plugin failing. Any other exception from a route, a tool or a listener is logged with its traceback
and counts towards turning the plugin off.

**Synapse's reference writers** (API 1.2): `from backend.plugins import formats`.
`formats.writers()` lists them (`key`, `title`, `extension`, `mimetype`, and `dump(items,
**options)`), and `formats.dump(items, key, **options)` writes one: BibTeX (`bibtex`), RIS (`ris`),
EndNote tagged (`endnote`) and XML (`endnote_xml`), CSL JSON (`csljson`). They are the writers File →
Import reads back, so a plugin exporting through them does not drift from Synapse's importers.
Items are dicts in the library's field format; the options are an export writer's. Export Formats
registers exactly these:

```python
from backend.plugins import Plugin, formats


def activate(plugin: Plugin) -> None:
    for w in formats.writers():
        plugin.export_format(w.key, title=w.title, extension=w.extension, mimetype=w.mimetype)(
            lambda items, options, w=w: w.dump(items, **options)
        )
```

### Scholarly helpers

API 1.4 provides `from backend.plugins import scholarly`. These helpers reuse the library and
importers' implementations and work offline without reading settings or user data.

| Helper | Contract |
|---|---|
| `clean_doi(value)` | Strip a DOI URL or `doi:` prefix and surrounding whitespace; keep case and trailing punctuation. Empty/None becomes `""`. |
| `norm_doi(value)` | A DOI for matching: stripped prefix, lowercase, no trailing dots; empty/None becomes `None`. |
| `norm_arxiv(value)` | Bare lowercase arXiv ID without its version, from an identifier or URL; `None` if absent. |
| `arxiv_id_of(item)` | Find an arXiv ID in a library item's identifier/link fields; keep its case. Return `None` when absent. |
| `ARXIV_ID` | Compiled pattern for modern and legacy IDs; group 1 is the unversioned ID, preserving case. |
| `parse_display_name(name)` | Convert a display name to `(family, given)`, preserving accents and family-name particles. |
| `split_authors(text)`, `join_authors(pairs)` | Read/write Synapse's `Family, Given; Family2, Given2` author format. Joining accepts an iterable of pairs. |
| `strip_tags(text)` | Plain text from JATS/HTML markup, omitting a JATS abstract heading and collapsing whitespace; None becomes `""`. It does not decode HTML entities. |
| `title_similarity(a, b)`, `TITLE_MATCH` | Similarity in 0–1 using the existing case/accent/punctuation-insensitive rules (ASCII letters after normalization); empty normalized titles score 0. The current lookup threshold is 0.8. |

### Named reference sub-sources

API 1.5 lets one reference provider identify which catalogue answered:

```python
@plugin.reference_source("catalogues", title="Catalogues", subsources={"local": "Local catalogue"})
def references(query):
    return {"source": "local", "references": [{"title": "A cited work"}]}
```

A nonempty result must select one declared sub-source. Sub-source IDs contain lowercase letters,
digits, underscores or hyphens, start with a letter and have at most 64 characters. Titles must
be nonempty strings of at most 80 characters. A contributed result is stored as
`<plugin>.<registration>.<sub-source>`; another plugin cannot claim that identity. If `subsources`
is omitted, API 1.1 behavior is preserved: contributed results use `<plugin>.<registration>` and
ignore a returned `source` value. Empty reference lists still mean no match and allow fallback.

The host supplies four fixed compatibility aliases for `reference-sources.scholarly.*`:
`semanticscholar`, `openalex`, `crossref` and `arxiv-html`. Those declarations continue returning
exactly the historical bare values in `citations.db` and HTTP responses, preserving existing
records and consumers without a database rewrite. These aliases are scoped to those complete
plugin/registration/sub-source IDs and cannot be claimed by other plugin IDs.

The reference view adds an optional `fetch.source_title` from running declarations. Historical
bundled titles remain available with plugins off, including after restart; unknown saved plugin
sources retain their full ID as their fallback label. The browser also keeps its old labels for
older servers. Source IDs remain the stable identity; titles are presentation only.

`scholarly.REF_FIELDS` is the reference-record field tuple: `doi`, `arxiv_id`, `openalex_id`,
`s2_id`, `title`, `authors`, `year`, `venue`, `raw`. References may provide any subset; absent values
are unknown. The first two fields identify a work, author text uses the library's author format,
`year` is an integer or unknown, and `raw` preserves an unstructured citation. The store owns
`item_id`, `position` and later library matching; providers must not supply database identity.
API 1.5 also exposes `scholarly.identifiers(item)` (normalized DOI and arXiv ID, both may be present)
and `ARXIV_DOI` (group 1 is the suffix of an arXiv DOI).

### Events

| Event | Payload (besides `event`) | When |
|---|---|---|
| `item.added` | `ids` | Items were created (by hand, by identifier, import, the browser button, a PDF). |
| `item.changed` | `ids`, `fields` | Metadata fields were set (`fields`: their names). Tags and folders are not reported yet. |
| `item.trashed`, `item.restored` | `ids` | Items went to the Trash or came back. |
| `item.deleted` | `ids` | Items were deleted for good. |
| `citekey.changed` | `id`, `old`, `new` | A citation key changed (after the notes were rewritten). |
| `note.saved` | `path`, `hash`, `by` | A note was created or saved; `by` is `app` or `ai` (Claude's edit). |
| `app.started` | | The web app is up (not sent in the MCP server). |

Events are sent after the change is saved and never slow the request: each plugin has one worker
thread that runs its listeners in order. A listener running more than ten seconds is noted in the
log.

### Storing data

The host closes SQLite connections used by per-item field storage and background job queues
after each operation, including failures; their transaction scopes commit on success and roll
back on error. They do not retain connections until garbage collection.

Prefer `plugin.storage.sqlite` for records. Its `migrations` list brings the schema from version
`i` to `i + 1` (kept in `PRAGMA user_version`); only ever append to it, so every user's database
can be updated. Before migrating a database that already holds data, Synapse makes a backup (as
before other risky operations). A database written by a newer version of the plugin is refused
rather than damaged. JSON updates and SQLite migrations are serialized across the web app and
standalone MCP process. A JSON update merges its keys with the latest document and publishes it
atomically; a migration rechecks the schema after acquiring the lock. Read/modify/write across
several calls (`get` followed by `set`, for example) is not a transaction: use a SQLite `session`
for that. Hidden `.lock` files coordinate processes and are excluded from backups.

### Per-item fields

A field is a typed value a plugin attaches to papers: a citation count, a score, a status. Declare
it once and Synapse does the rest:

```python
def counts(papers: list[dict]) -> dict:
    # papers: [{"key", "item_id", "doi", "arxiv", "title", "year"}, …], at most 50 at a time
    found = lookup([p["doi"] for p in papers if p["doi"]])  # your code, with plugin.http
    return {p["key"]: found.get(p["doi"]) for p in papers}  # missing ones: "not found"


plugin.fields.declare("citations", type="int", label="Citations", source="OpenAlex", refresher=counts, refresh="7d")
plugin.fields.set(item, "citations", 152)  # or set values yourself
plugin.fields.set({"arxiv": "2610.01234"}, "citations", 0)  # also for a paper not in the library
```

- **Where it shows.** In the list's column chooser under *From plugins* ("Citations (Citation
  Counts)", sortable, empty values last); as a search filter, also in smart folders and Claude's
  `search_library` (`citations:>100`, `citations:10-50`, `citations:none` for no known value,
  `citations:any`, a word for text, `true`/`false`, dates like `>2026-01-01`; when two plugins
  use one name, or the search has a filter of that name, write `<plugin id>.<name>:`); in the
  item pane under *From Plugins*, with its source and when it was fetched; and in Claude's
  `get_paper` as `plugin_fields`.
- **Types**: `int`, `float`, `text`, `date`, `bool`, `url`, `enum` (with `options`). A value that
  does not fit is refused with a `PluginError`. `None` means the plugin looked and found nothing
  ("Not found"), which is kept apart from never having looked ("Not fetched yet") and from 0.
- **Whose value.** Values are stored by the paper's DOI, else its arXiv id, else the library
  item's id, so a value set for a paper not in the library (`{"doi": …}`, `{"arxiv": …}`) is
  there when the paper is added, and an item keeps its values when it gains a DOI. A paper is
  named by the item (its dict or id), `{"doi": …}`, `{"arxiv": …}`, or a key (`"doi:10.…"`).
- **Refreshing.** With a `refresher`, Synapse hands it the library items that have no value yet,
  then (with `refresh`, e.g. `"7d"`) those whose value is older, 50 at a time, on the plugin's
  background thread in the web app: soon after items are added or changed, and every ten
  minutes. It returns `{key: value}` (or `{key: (value, source_url)}`); keys it leaves out are
  stored as not found. An `HttpError` leaves the whole batch unasked, to try again later; a
  refresher that reached some services but not others answers **`plugin.fields.LATER`** for the
  papers it could not ask about: nothing is stored for them and they are handed over again in an
  hour (not at once, so they do not hold up the rest).
- **Reviewing a library item.** `plugin.services.lookup_item(item_or_id)` returns `(current, fetched)`
  through the same metadata lookup as Retrieve Metadata, including author resolution and a proposed
  citation key. It saves nothing. `lookup(identifier)` remains the outside-record lookup.
- **Asking again now.** `plugin.fields.refresh(name, papers=None, wake=True)` hands the field's papers (or
  only `papers`) to its refresher again soon, whatever their age: for a "Check now" button. The
  values stay shown until the new ones arrive. Explicit refresh also retries papers deferred after
  an unreachable service. A refresh interval taken from a setting can be
  changed while running by assigning seconds to the declared field's `refresh`
  (`field = plugin.fields.declare(…)`; `field.refresh = days * 86400` in
  `@plugin.settings.on_change`).
- Values live in `data/fields.db` (in backups), apart from the library. A plugin turned off takes
  its columns and filters with it; its values stay for when it is turned on again.

### Papers outside the library

A plugin can offer a **list of papers** (an arXiv listing, search results, suggestions). The app
shows it in the sidebar's list like a folder: the columns, the search, sorting and the
fields all work on it, papers already in the library show as the library items, and the others
get a **+**.

```python
@plugin.item_source("feed", title="arXiv Feed", icon="calendar", badge=lambda: len(unseen()),
                    variants=lambda: [{"id": "2026-10-26", "title": "26 Oct 2026"}])
def feed(day=None) -> list[dict]:
    # called each time the list is shown: return what you have, fetch in jobs
    return [
        {"arxiv": "2610.01234", "title": "…", "authors": ["Jane Doe", "Roe, Rick"], "year": 2026,
         "abstract": "…", "categories": ["quant-ph"], "reasons": scores[key]["reasons"]},
        …
    ]
```

- **A paper** is a dict with a `doi` or an `arxiv` id (a `PluginError` otherwise) and any of
  `title`, `authors` (a list of names or "Family, Given; …"), `year`, `date`, `abstract`, `journal`,
  `volume`, `pages`, `publisher`, `url`, `item_type`, `categories`, `reasons`, `extra` (a dict).
  Optional `highlights: [{"label": "Following", "text": "Following: Jane Doe", "tone": "gold",
  "icon": "star"}]` adds multiple title badges (up to 8; labels capped at 40 characters, tooltips
  at 300). Tones are `gold`, `red` or `teal`; icons `star`, `megaphone` or `link`. The legacy single `highlight`
  remains supported. Badges and reasons survive matching a library item, within the source view.
  API 1.11 adds `group`: a short heading (at most 40 characters) the paper is shown under, such as
  a tier; papers of one group should come one after another. The list then has a heading per group
  (on the desktop while it is in the plugin's own order, a row that folds the group with a click,
  remembered per list; on a phone, cards for the first group and rows for the others). It reaches
  the item as `_group`, also for a paper already in the library.
  Variants may return the same `highlights` array for compact circular day indicators; the main
  source entry shows only the browser's current calendar-day variant's badges (on a phone too:
  beside the list's tab or its row in the Plugins tab, and on each day's chip). Older retained
  days keep their own badges, including when no feed exists today. Navigation refreshes at
  least once a minute and when the window becomes visible.
  Synapse gives it the id `x:<key>` (`x:arxiv:2610.01234`) and a `url` (arXiv's or doi.org's).
  `plugin.services.lookup` returns papers in this shape. The list keeps the plugin's order (at most
  2000 papers), shows each paper once, and the badge is the short text or count `badge()` returns.
- **Variants** keep one entry in the main sidebar and add a second choice pane beside the item
  list. `variants()` returns `{id, title, badge?}` rows; the source function receives the selected
  id (or `None` for its default view). Variant ids are validated against the current list before
  the source runs.
- **Reasons** say why the paper is listed, shown in the item pane under *Why It Is Here*: lines of
  text, or `{"kind", "text", "items"}` where `items` are library item ids (shown as links), as
  `plugin.services.connections` gives them.
- **Changes.** The list function runs when a window shows the list; call `plugin.refresh_sources()`
  when the papers change (a job fetched new ones), and the open windows ask again (change kind
  `sources`). Keep the function fast: fetch in [jobs](#background-work) and return what is stored.
- **Fields** (`plugin.fields.set({"arxiv": …}, …)`) show on outside papers as on library items,
  and stay with a paper once it is added.
- **Item-pane sections** of plugins' frontends are shown for outside papers too
  (`item._outside` is true, `item.id` starts with `x:`); item actions are not.

`plugin.services` reaches the web the way Synapse does (the scholarly services it already uses,
with the contact address and keys from Settings), not through `plugin.http`, so it needs no
`network` permission:

| Service | What it returns |
|---|---|
| `lookup(identifier)` | A paper's metadata by DOI, arXiv id (`arXiv:2610.01234`) or ISBN, as a paper in the shape above. `PluginError` when nothing is found, `Offline` without network. |
| `references(paper, new=False)` | The works a paper (`{"doi"}` or `{"arxiv"}`) cites: `[{doi, arxiv_id, title, year, authors, venue, raw}]`, from Semantic Scholar, OpenAlex or Crossref, else from **arXiv's HTML version**, which has them on the day a paper is announced; for arXiv papers of this month or the last, the HTML list wins when it is longer (see [references](references.md)). `new=True`, for a paper announced in the last days, asks the HTML version first and the others only when it has no list. `[]` when none is known, `Offline` when the services cannot be reached. |
| `match(refs)` | For each reference, the id of the library item it is (by DOI, arXiv id, or title and year), or `None`. |
| `connections(papers, refs=None, folders=None, author_names=None)` | For each paper, `{"score", "reasons"}`: the library papers it cites (`refs`: `{paper key: its references}`), those whose reference lists cite it, shared authors (family name and first initial) and how close its title and abstract are to the library's (TF-IDF). `folders`: only papers in these folders count. `author_names`: identify authored library papers by surname and first initial; their citations receive a separate `cites_own` reason and score 6 rather than 3. Shared authors contribute at most 1.5. Sort by `score`; show `reasons`. |

`plugin.actions.add(paper, folder_id=None, tags=None)` adds a paper as the "+" does: looked up
by its DOI or arXiv id (the paper's own fields when that fails), the duplicate check, a citation
key, `item.added` for the plugins, and then a reference list and a PDF when Settings → Adding
Items says *Always*. It returns the library item, with `"duplicate": True` when it was there already.

### Background work

```python
@plugin.schedule("daily", cron="30 2 * * 1-5", tz="Europe/Rome")  # minute hour day month weekday
def fetch_listing() -> None:
    for entry in today():
        plugin.jobs.enqueue("references", entry["id"], key=f"refs:{entry['id']}")


@plugin.job("references")
def references(arxiv_id: str) -> None:
    page = plugin.http.get(f"https://arxiv.org/html/{arxiv_id}")
    ...
```

- **Schedules** run in the web app only (not in the MCP server), on one thread per plugin that
  also runs its jobs, one thing at a time. `cron` takes the usual five fields (`*`, ranges, lists,
  `*/n`, `mon`–`sun`, `jan`–`dec`) in `tz` (this computer's zone by default); `every` takes
  seconds or `"30m"`, `"6h"`, `"1d"` (at least a minute). With `catch_up` (the default) a run
  missed while the computer slept or the app was closed happens once at the next start, and the
  very first time the work runs at once; without it, missed runs are skipped. When each schedule
  last ran is kept in the plugin's data folder (`.schedules.json`).
- **Jobs** are kept in a small database in the plugin's data folder (`.jobs.sqlite`), so they
  survive a restart, and the MCP server can queue work that the web app then does. Arguments are
  JSON values. With a `key`, a job is not queued again while one with that key waits; that one is
  brought forward when the new one would run sooner (`enqueue("score", key="score", delay=30)`
  while lists come in, then `delay=0` after the last). A job that
  fails is tried again after 30 s, 2 min and 10 min (`retries` times); failing to reach the web
  (`HttpError`) does not count against the plugin, any other error does.
- A running plugin's section in Settings lists its schedules and how many jobs wait.

Schedules without `tz` follow the computer's local wall clock, including daylight-saving changes.
An explicit IANA zone (`tz="Europe/Zurich"`, for example) follows that zone's rules. Interval
schedules count elapsed seconds instead.

### Reaching the web

`plugin.http` is the only way a plugin should reach the web (`requests` would work, but skips all
of this):

- Only the hosts its manifest's `network` names, and only public addresses, also after redirects.
  Public requests use the shared transport in `backend/online/http.py`: every DNS answer must be
  public, and sockets connect directly to those checked addresses without another hostname lookup.
  The original hostname remains the HTTP Host, TLS SNI and certificate-verification identity.
  Each redirect gets fresh permission and DNS checks; mixed public/private DNS answers are refused.
  Environment proxies and netrc credentials are not used for these requests, so they cannot bypass
  destination validation. TLS verification remains enabled with requests' default CA bundle.
  API 1.3 adds the special `"loopback"` network permission, shown as **this computer (loopback)**:
  localhost and literal loopback IPs (`127.0.0.0/8`, `::1`), on any port. It grants no LAN or web
  access. Localhost is pinned to `127.0.0.1`, environment proxies are bypassed, embedded credentials
  are refused, and each redirect is checked again. Loopback calls have no default rate gap; a
  plugin can still request one with `limit`. Status and model-generation timeouts may be a
  `(connect, read)` pair, as in requests. Ordinary host permissions still refuse private addresses.
- API 1.12 adds **local services**: services the deployment runs next to Synapse, named by whoever
  starts the server (`--local-service ollama=http://ollama:11434` or `SYNAPSE_LOCAL_SERVICES`, never
  Settings), such as Ollama in a container of its own ([Docker](docker.md)). With the `"loopback"`
  permission their exact origins (scheme, host and port) are reached like loopback; other ports of
  that host are not. `plugin.http.local_service("ollama")` returns the address, or `None` when the
  server names no such service.
- API 1.13: a **PDF source** may return an article's web page instead of its PDF (a DOI's
  `https://doi.org/…`, say). Synapse follows the page's `citation_pdf_url` (or a meta refresh) to
  the PDF itself, with the cookies the site sets, so the source needs no network permission for
  the publisher.
- Requests to one host are spaced (0.5 s; more with `plugin.http.limit(host, seconds)`), across
  all plugins, so two plugins cannot overload one service. All of arXiv's hosts share one line, 3 s
  apart as its API terms ask, with Synapse's own requests to arXiv (`backend/online/http.py`
  `HOST_GAPS`).
- A User-Agent naming Synapse, the plugin and the contact address from Settings → Online
  Services, as scholarly APIs ask.
- For the scholarly services Synapse itself uses, the user's keys from Settings → Online Services,
  as Synapse's own lookups send them: OpenAlex (`api.openalex.org`) gets `api_key` and `mailto`,
  Semantic Scholar (`api.semanticscholar.org`) the `x-api-key` header
  (`http.service_credentials`). The plugin's own parameters and headers win, and a redirect to
  another host drops them.
- GET answers with an ETag or Last-Modified are cached and asked for again conditionally.
- Errors are `HttpError` (an error status, `e.status`; a refused redirect; an answer over 50 MB)
  and `Offline` (no network, a timeout): both readable, so a route or tool passes them on as they
  are. `raise_for_status=False` returns error answers instead.
- The answer is a `Response`: `status`, `url`, `headers`, `content`, `text`, `json()`,
  `from_cache`.

### Python packages

`"python": ["scirate>=0.0.7", "feedparser"]` in the manifest makes Synapse install them with pip
(the Python that runs Synapse) into the plugin's data folder (`.python/`, not in backups) the first
time the plugin starts, and again when the list changes; they are on the import path while it
runs. That first start needs the network; when pip fails, the plugin is turned off with pip's
message. Requirements are names with versions only (no URLs, paths or pip options), and packages
Synapse itself uses (Flask, requests, pypdf …) are refused: the plugin gets the versions Synapse
has.

### Trying a change

With `--dev-plugins`, saving a file restarts the plugin. Otherwise **Restart** in Settings →
Plugins (or turn it off and on) loads the code again. Watch the plugin's **Log** for errors.

### The frontend

API 1.3 also accepts `p.backend.post(path, body, {signal})` to cancel requests. Registrations and
editor-open listeners are disposed automatically on stop or failed activation.

The `frontend` module is loaded by the app (every window) while the plugin runs. It exports
`activate(p)`, and optionally `deactivate(p)`; `p` is a `PluginContext`, declared in
[`frontend/src/plugins/api.ts`](../frontend/src/plugins/api.ts). That file has only types, so it can
be copied next to a plugin's code as its declaration file (in JavaScript:
`/** @param {import('./api').PluginContext} p */`).

```js
export function activate(p) {
  p.pages.register({
    id: 'recent', title: 'Recently Added', icon: 'calendar',
    mount(el) {
      el.innerHTML = '…';
      return p.onChange((kinds) => { if (kinds.includes('library')) redraw(el); });   // cleaned up when the page goes
    },
  });
  p.commands.register({ id: 'show', title: 'Show Recently Added', key: 'Mod-Alt-r', run: () => p.pages.open('recent') });
  p.itemPane.register({ id: 'added', title: 'Added', mount(el, item) { … } });
  p.itemActions.register({ id: 'forget', title: 'Forget When It Was Added', run: async (items) => { await p.backend.post('/forget', { ids: items.map((i) => i.id) }); } });
}
```

| Member | What it does |
|---|---|
| `p.editor.suggestions.register({ready, complete, delay, includeContext})` | API 1.3: register a plain-text ghost suggestion provider. `ready()` gates requests; `complete(prefix, suffix, signal)` resolves to text, with up to 6,000 characters before and 2,000 after the cursor. API 1.10 optionally adds `includeContext: true`: `complete` receives a fourth argument `{title, headings, excerpts}`, containing bounded source text before the cursor. The combined field lengths are at most 1,100 characters; excerpts are relevant earlier definitions/notation, not other notes. Older three-argument providers retain the same nearby-text inputs and need no changes. `delay` defaults to 350 ms. The newest registration is active; disposing it restores the preceding one. Registration/disposal cancels requests and clears visible text in retained editors. CodeMirror stays private. |
| `p.editor.onOpened(fn)` | API 1.3: disposable event when a note editor opens. Also initialize at activation, since an editor may already exist. |
| `p.settingsPage.open()` | API 1.3: show this plugin's Settings section. |
| `p.pages.register({id, title, icon, mount, phone})` | API 1.11 adds `phone(el, nav)`: the page on a phone (see [The phone layout](#the-phone-layout-111)); `mount` may then be left out for a page that exists only on phones. A page: an entry under **Plugins** in the sidebar; when chosen, the page takes the place of the library (as Tasks does) and `mount(el)` draws it. `p.pages.setBadge(id, value)` puts a count on the entry, `p.pages.open(id)` shows it. |
| `p.commands.register({id, title, key, when, run})` | A command in the palette, named after the plugin. `key` like `Mod-Alt-r` (Mod: ⌘ on a Mac, Ctrl elsewhere) needs Mod or Alt and cannot be one of Synapse's keys; a key that cannot be used is left out (the console says why). |
| `p.itemPane.register({id, title, when, mount})` | A section at the end of the item pane, for the items `when(item)` accepts; `mount(el, item)` runs once the selection rests. Papers from a list (`item._outside`) get sections too. |
| `p.itemActions.register({id, title, icon, outside, role, when, run})` | API 1.11 adds `role: 'dismiss'` for an action that takes papers off the plugin's list: a phone runs it for "Not for me" and a swipe to the left. An entry in the item menu for the selected items, also in the palette. With `outside: true` it is also offered for papers from a list that are not in the library (`item._outside`); without it, it is left out while one is selected. |
| `p.sources.open(id)`, `p.sources.refresh()` | Show one of the plugin's lists (leaving Notes, Tasks or a page); ask for the lists again at once after changing them from this window (other windows hear it through the change feed). |
| `p.notify(message, {type, desktop, sourceId, action, transient, timeout})` | A notification in the center, named after the plugin; severity and optional OS delivery are independent. |
| `p.backend.get/post/put/delete(path, …)` | The plugin's own routes (`/api/plugins/<id><path>`), with the page's sign-in; resolves to the JSON answer. |
| `p.library.selected()`, `.item(id)`, `.show(id)` | The selected items, one whole item, showing an item in the library. |
| `p.onChange(fn)` | `fn(kinds)` when something changed: what the change feed reports (`library`, `notes`, `tasks`, `annotations`, `plugins`, `fields`, `sources`), and `library` when this window's item list reloads. |
| `p.settings()`, `p.saveSettings(changes)` | The settings' values (a promise); changing some of them (checked against the manifest, heard by `@plugin.settings.on_change`). |
| `p.settingsPage.register({id, title, icon, shows, mount})` | A part of the plugin's own section in Settings, above the settings drawn from the manifest, for what those cannot show: a status, a test button, a list to pick from. `shows` names manifest settings the part draws itself, which are then not listed again. A title other than the plugin's name is shown as the part's heading. `mount(el)` runs each time Settings opens. (`after`, which placed a section of its own, is still accepted and ignored.) |
| `p.ui` | `icon(name)`, `escapeHtml(text)`, `confirm(…)`, `prompt(…)`, `formatDate(iso, withTime)`: the app's own. |
| `p.onUnload(fn)` | Runs when the plugin stops. |
| `p.capture.register({id, title, icon, run})` | API 1.11: an entry in the phone's **+** sheet, under From plugins. |
| `p.editor.toolbar.register({id, title, icon, label, run(view)})` | API 1.11: a button above the keyboard while a note is edited on a phone; `label` (a few characters) replaces the icon; `run` gets the note's CodeMirror `EditorView`. |

Rules for a plugin's frontend:

- Draw only inside the element a mount function gets: the rest of the app's markup is not part of the
  API. Return a function from `mount` to clean up (stop timers, remove listeners).
- The element sits inside one with `data-plugin="<id>"`: start the stylesheet's rules with
  `[data-plugin="<id>"]` and use the colour tokens (`var(--text)`, `var(--accent)` …) so light and
  dark themes work.
- Everything registered through `p` is taken away when the plugin stops, including registrations
  made by an activation that fails. That failed frontend is retried only after the plugin restarts.
  A backend call whose plugin stopped rejects with `AbortError`; callers may catch it to finish
  local cleanup. It never stays pending solely to suppress an error. Late registrations from a
  retired context are disposed immediately. Errors in its functions
  are reported (a notification and the browser console) instead of breaking the app.
- The app loads the module again when the plugin restarts (its URL carries the start time). Modules
  it imports keep their URL, so the browser may keep the old ones until the page reloads: a single
  file (or a bundle) is the simplest.
- TypeScript: point `frontend` at the `.ts` file. Synapse bundles it (with what it imports) into
  one module with esbuild when the plugin starts: the copy Synapse builds its own frontend with
  (`frontend/node_modules`, after `npm ci`), else one on the PATH. A build error turns the plugin
  off with esbuild's message. Bundling also avoids the cache problem above. The bundle is in the
  plugin's data folder (`.build/`).

### The phone layout (1.11)

Phones get a layout of their own ([phone](phone.md)): Library and Notes are Synapse's, and every
plugin destination sits in a **Plugins** tab, one of them pinned to the tab bar by the reader. A
plugin's list of papers needs nothing more: the phone draws it as cards and rows, grouped by the
papers' `group`, with Add, PDF and "Not for me" (the action with `role: 'dismiss'`). Item sections,
fields and item actions appear on a paper's page. A page needs a phone layout of its own:

```js
p.pages.register({
  id: 'board', title: 'Board', icon: 'list',
  mount: (el) => drawWide(el),                 // the desktop (may be left out)
  phone: (el, nav) => {                        // a phone
    el.innerHTML = '<button class="more">More</button>';
    el.querySelector('.more').onclick = () => nav.push({ title: 'Detail', mount: (box) => { box.textContent = '…'; } });
  },
});
p.capture.register({ id: 'idea', title: 'Idea', icon: 'sparkle', run: () => p.ui.prompt('Idea').then(save) });
p.editor.toolbar.register({ id: 'stamp', title: 'Insert a date', label: '§', run: (view) => insertDate(view) });
```

`nav` offers `push({title, mount})` (a screen of the page's own, with a back button), `back()`,
`openNote(path)` and `openItem(id)`. The phone keeps a page's screens while its tab is shown; the
cleanup a `mount` or `phone` function returns runs when its screen goes. The Tasks plugin is an
example (`plugins/tasks/frontend/phoneView.ts`).

## How the host works

`backend/plugins/` sits above `library`, `online` and `references` in the layers (it imports
`config`, `settings`, `changes`, `library`, and for `plugin.services` `online` and `references`; see
[architecture](architecture.md#layers)). `Services` creates the `PluginHost` and connects it to the library: item changes
from `PaperRepository.on_items`, key changes from `on_citekey_change`, saved notes from
`NotesService.saved_listeners`. `create_app` starts it with `role="app"`, `mcp_server.py` with
`role="mcp"`.

| Module | What it does |
|---|---|
| `host.py` | `PluginHost`: finds plugins (`scan`), starts and stops them, runs their routes and tools with failure counting (`route`, `run`, `mcp_tools`), sends events (`emit`), and what Settings shows (`info`, `overview`). |
| `catalog.py` | Discovers manifests without importing code, applies folder precedence and, once per plugin, turns on the bundled plugins that start by themselves (core plugins, and contributed ones with `enabled_by_default`), carrying legacy preferences over. |
| `files.py` | Shared file transactions: process and thread locks, atomic JSON replacement. |
| `manifest.py` | Reads and checks `manifest.json`; `API_VERSION`; settings and their values. |
| `context.py` | `Plugin`, the object a plugin gets; `PluginError`; `EVENTS`; tool schemas from type hints. |
| `state.py` | `data/plugins.json`: which plugins are on, their settings, `remote_mcp`, why one was turned off. |
| `storage.py` | `plugin.storage`: JSON and SQLite stores. |
| `library.py` | `plugin.library`: what plugins may read. |
| `http.py` | `plugin.http`: allowed hosts, public addresses, spacing, caching, errors. |
| `jobs.py`, `schedule.py` | The job queue and the runner thread that runs jobs and schedules (web app only); cron rules and intervals. |
| `deps.py` | Python packages from the manifest, installed with pip into `.python/`. |
| `build.py` | TypeScript frontends bundled with esbuild into `.build/`. |
| `loading.py` | Importing a plugin's backend, the files of its frontend the app may fetch, when its files last changed. |
| `report.py` | What Settings shows about a plugin (`describe`). |
| `fields.py`, `itemfields.py` | Per-item fields: keys, types, the store (`data/fields.db`) and filters; `plugin.fields` and `FieldIndex` (values for the list and the item pane, search filters, refreshing). |
| `outside.py`, `sources.py`, `services.py` | Papers outside the library: the shape of one (`record`), the lists running plugins offer (`SourceList`: the sidebar's entries, a list's papers with library items in their place), `plugin.services` and `plugin.actions`. |
| `scaffold.py` | `--new-plugin`. |
| `errors.py` | `PluginError`. |
| `runtime.py` | A plugin's log file, its worker thread and its failure count. |

- **Loading.** A plugin's backend is imported under a fresh module name each time it starts
  (`_synapse_plugin_<id>_<n>`), so a restart picks up changed code; turning it off drops its modules.
  Everything it registered (routes, tools, listeners) is held by its `Plugin` object and dropped
  with it, then its `deactivate` and `on_unload` functions run.
- **Two processes.** The web app and the MCP server both run the plugins that are on. Only the app
  changes `plugins.json` (the MCP server writes it only to turn off a plugin that failed); each
  process notices the other's changes by the file's time and size (`refresh`, called when Settings
  lists plugins and when Claude lists tools), including core-plugin changes in safe mode.
- **Failures.** `fault()` logs the traceback; three within ten minutes turn the plugin off (from a
  separate thread, since the failure may be on the plugin's own worker), record the reason in
  `plugins.json` and tell open pages through the change feed (`plugins`).
- **Routes** are matched per plugin with a Werkzeug `Map` behind one catch-all endpoint
  (`/api/plugins/<id>/…` in `backend/api/plugins.py`), so plugins can be turned on and off while
  the app runs. Settings → Plugins talks to `/api/plugin-host/…`.
- **Tools** are asked for at each `tools/list` and `tools/call` (`Server.extra` in
  `mcp/protocol.py`); `build_server(..., remote=True)` (the `/mcp` endpoint) offers only those of
  plugins with `remote_mcp` on. A `PluginError` reaches Claude as a tool error.
- **Dev plugins.** With `--dev-plugins`, `catalog.discover` adds that folder (source `dev`, replacing a
  plugin of the same id), and a thread compares the newest modification time of each running dev
  plugin's files (`loading.newest`) every second; a newer one restarts it (`check_changes`), which
  the change feed tells the open windows. Stopping the host joins its watcher before unloading
  plugins; starting again creates one new watcher.
- **Data.** `data/plugins/` holds plugins (code, not in backups), `data/plugin-data/<id>/` what each
  stores plus dot files that are left out of backups: its log `.plugin.log`, `.python/`, `.build/`,
  `.jobs.sqlite`, `.schedules.json`. `data/plugins.json` is their state, `data/fields.db` the values
  of their fields. Backups hold `plugins.json`, `fields.db` and `plugin-data/` (SQLite files copied
  with the backup API);
  restoring stops the plugins, puts both back (keeping today's logs) and starts them again.
- **The frontend** (`frontend/src/plugins/host.ts`) asks `/api/plugin-host` which plugins run, then
  `import()`s each one's module from `/api/plugin-files/<id>/<path>?v=<started>` and loads its
  styles. It syncs at start, when the change feed names `plugins`, and after Settings turns one on
  or off (that window's own change is not in the feed). `PluginHost.asset` serves only the styles
  file and files in the folder of the frontend module, never Python files or the manifest.
- **Extension points** (`frontend/src/extend.ts`) are registries the app's views read: `commands`
  (the palette, `views/palette.ts`; `main.ts` registers Synapse's own and runs plugins' keys),
  `pages` (`views/pages.ts`, entries in the sidebar's list), `itemSections` (the item pane) and
  `itemActions` (the item menu). The plugin host prefixes ids with the plugin's id, wraps every
  function so its errors are reported, and passes copies of items.
- **Fields.** `PluginHost.fields` (a `FieldIndex`) reads the declarations of the running plugins.
  `api/items.py` attaches known values to the items it lists as `_f` (`{"<plugin>.<name>": value}`)
  and passes `search_filters()` to the search as `ctx["field_filters"]` (`library/search.py` only
  knows them as functions); the MCP tools do the same. The item pane asks
  `/api/items/<id>/fields`, the column chooser `/api/plugin-fields`. A change of values records
  `fields` in the change feed; the open windows reload the list. A plugin with a refresher gets a
  background runner that calls `FieldIndex.refresh` (`Runner.refresher`), woken on `item.added`
  and `item.changed`.
- **Outside papers.** `PluginHost.sources` (a `SourceList`) answers `/api/plugin-sources` (the
  sidebar) and the item list's view `source:<plugin>.<list>` (`api/items.py`): it runs the plugin's
  function, turns each paper into an outside record and puts the library item in place of those
  already in the library. Records are sent whole (the item pane has nothing else to ask).
  `POST /api/outside/add` adds one (`Services.add_paper`). `plugin.refresh_sources()` records
  `sources` in the change feed. `plugin.services.connections` is `references/connections.py`.
- **Trust.** Plugins are not sandboxed: Python plugins run inside the server with its access, and a
  plugin's frontend runs in the app's page, with its access.
  Permissions are shown before turning a plugin on and checked where the API can check them
  (`plugin.library`, `plugin.mcp_tool`), but they do not contain a plugin that ignores the API.

## Tests

`tests/plugins/` (manifests, the host, storage and backups, the files of a frontend; `test_fields.py`
for per-item fields (keys, types, filters, the list, the item pane, the refresher, outside papers,
Claude, backups); `test_http.py`
against a local server, `test_jobs.py` for cron rules, schedules and jobs, `test_tooling.py` for
background work in the app and the MCP server, packages with a stand-in for pip, TypeScript builds,
the scaffold and dev plugins; the example plugin is installed by the `example` fixture and
`make_plugin` writes small ones), the import rule
in `tests/test_layers.py`, `tests/js/extend.test.mjs` (registries, keys, the palette's ranking),
`test_outside.py` (outside records, lists, adding, the services, arXiv's HTML references, the
Suggested Reading example), `test_citation_counts.py` (the Citation Counts plugin against faked
OpenAlex and Semantic Scholar answers, the services' keys, `LATER`; `conftest.py` leaves the
plugins in `plugins/` out of the other tests, the `bundled` fixture brings them back),
`test_arxiv_today.py` (arXiv Today with a faked feed and reference lists: the listing, scoring,
both lists, dismissing, the news, Claude's tool, forgetting old days, the RSS parser), and the browser checklists `plugins` (Settings → Plugins and the
example plugin's page, command, fields, section and action) and `outside` (a plugin's list in the
sidebar and the list, sorting, the pane, adding, Undo, a folder, changes, turning it off).

## Provider and writer lifecycle

`GET /api/formats` keeps its existing `formats` import descriptors and adds `exports` containing
only running writers. Library export dialog previews, its copy button and downloads use those writers; the dialog
reloads the list whenever opened. Default-export choices follow plugin changes while Settings
is open, preserving an unavailable saved preference for reactivation. Stale preview responses
cannot replace the newest selected format.

The app injects reference resolution into `Citations`; `plugin.services.references` uses the same
providers in both app and standalone MCP hosts. Resolution and export dispatch live in focused
`backend/plugins/references.py` and `exports.py` modules. Provider registrations are described
in the plugin's section of Settings, with their normal stop/restart lifecycle.

Item-list Copy as BibTeX (menu and Cmd/Ctrl+C, including selection prefetch) uses
`POST /api/clipboard/bibtex` with selected `ids`, so it remains available with Export Formats off.
`backend/formats/exporting.py` prepares record copies, selection order and merged keywords for
both clipboard output and optional writers; endpoints only parse requests and shape responses.

### Notification delivery

`p.notify` now goes to the [notification center](notifications.md) by default. Severity and OS
delivery are independent: `{type: "success", desktop: true, sourceId: "today"}` offers an OS
notification when enabled and retains a Show action across reloads. Use `transient: true` for
brief confirmations/Undo; `timeout` only applies to transient toasts.

### Metadata review and audit APIs (1.6)

`scholarly.compute_changes(current, fetched)` returns differences in the app's metadata display
order, as `{key, current, fetched, isNew, checked}` dictionaries. Empty fetched values are skipped;
comparison preserves case and punctuation. Changing an existing citation key is unchecked by
default. This is an offline helper and does not edit either record.

`plugin.fields.due(name)` returns library items in scheduler order (missing values first, then
oldest stale values); it requires library `read`, a declared field and a refresher. It respects
retry backoff after `LATER`. `refresh(name, papers=None, wake=False)` expires cached values and
clears retry backoff without waking the runner. An audit can take its due snapshot, initialize
progress, then call `plugin.fields.refresh_soon()`. `plugin.fields.changed()` tells open pages
that associated status changed without rewriting a field value. Results from stopped or restarted
refreshers are discarded by the host.

`p.jobs.start(message)` returns a progress job with `set(message, done?, total?)`,
`finish(message?, options?)` and `close()`. Finish accepts notification options (severity,
Review action, optional desktop delivery); the host assigns the plugin as owner. Unfinished jobs
close on unload, and actions cannot run after the plugin stops.

`p.ui.reviewMetadata(articles, options)` opens the shared review dialog. Each article is
`{id, current: PluginItem, changes: MetadataChange[], published?: string}`; inputs are copied.
Options are `title`, `onRejectSuggestion({id}, change)`, `onRejectArticle({id})`,
`onIgnorePaper({id})` and `onApplied(savedIds)`. Only selected fields are saved, after explicit
Apply. Callbacks let the plugin persist exact rejections and exclusions. Apply, like
`p.library.update(id, changes)`, uses the running plugin's `library: ["edit"]` permission through
`PATCH /api/plugin-host/<pid>/items/<id>` and the ordinary metadata update path. A stopped plugin
cannot submit new edits; unloading closes its review and prevents subsequent batch saves.
An edit already submitted may complete. No library schema or stored plugin data changes.

### Notes and visualization APIs (1.7)

`plugin.library.citation_graph()` requires library `read`. It returns the stored citation graph
(`nodes`, `edges`, `stats`) plus `fetched`: library item IDs whose reference lists have been fetched,
including empty lists. Trash is excluded. It makes no network requests.

`plugin.notes.list()` and `plugin.notes.links()` require `permissions: {"notes": ["read"]}`.
The listing contains paths, front-matter metadata and timestamps. Links are resolved as in Notes:
`{source: path, kind: "note" | "cite", target: path | citation_key}`. Embedded-note links are
included; unresolved note targets are omitted. These operations do not grant note writes.
Settings shows notes read access alongside library/network permissions.

`p.notes.enabled()`, `current()` and `open(path)` expose Notes availability, the current path and
navigation. `p.notes.visualizations.register(spec)` adds a sidebar entry, a section above note
details and a view in place of the editor. One visualization may register at a time; a second
registration fails explicitly. This preserves Notes navigation and Back/Forward rather than
turning a visualization into a separate page. The current view is also available from the Notes
header and note menu.

A spec has `id`, `title`, optional `icon`, `attach(context)`, `section(): HTMLElement`,
`showNote(pathOrNull)`, `changed()`, `open(focusOrNull): Promise<void>`,
`close({notify?})`, `isOpen(): boolean` and `destroy()`. The context provides `main` (the main-view
host), `currentPath()`, `openNote(path)`, `open(focus)`, `setOpen(boolean)` (host layout/sidebar
state), and `closed()` (history after a user close). Plugins own their section/main DOM, canvases,
observers and timers, and dispose them in `destroy`. The host unregisters navigation, restores
the editor and calls destroy on unload. `visualizations.open(id, focus?)` and `toggle(id)` leave
other workspaces through the host; plugins never import private navigation/store modules.

`p.assets.vendor("force-graph")` returns the host-vendored offline library entry point. TypeScript
plugins may use force-graph's public package types; Synapse's checker resolves its installed type
dependency for bundled Graph. There is no runtime npm/CDN import. Graph keeps its own model and
rendering code in `plugins/graph/frontend/` and discards retired async canvas/data work.

`p.library.openPdf(id)` opens an item's attachment or URL through the ordinary app operation;
`p.library.refresh()` refreshes the visible library data after a plugin route adds an item.
Graph's Add button uses `plugin.actions.add`, with library `add` permission and existing duplicate
checks, metadata fallback, citation-key generation and Adding Items settings.

### Markdown contributions and shared SDK (1.8)

`p.markdown.register(provider)` installs a Markdown provider: `markdown(md)`,
`spoilers(text, scanned)`, `exportStatic(root)`, `fence(info)` and `portable(text)`.
Providers compose in registration order; disposing a registration rebuilds retained renderers and
live preview. No editor-library instance is handed over: spoiler/figure source ranges remain plain
UTF-16 offsets, and the host creates preview widgets. `p.markdown.figures.register(provider)`
registers the document's figure scanner, label formatter, renderer, portable/export preparation
and gallery insertion function. One figure provider may register at a time because numbering is
document-wide. The host removes both registrations on unload.

The public `frontend/src/plugins/markdown.ts` SDK exports existing scan/mask rules, reference
formatting and text-reference helpers, image/anchor/line HTML helpers and `renderTaskBlock`.
Imports of this SDK are kept external by the plugin bundler and rewritten to the single host
module `/js/plugins/markdown.js`. This preserves host renderer and Tasks-provider identity without
bundling another engine or fetching a CDN. Type-only imports of markdown-it use the installed
matching types; actual rules receive the host's vendored renderer instance.

Extended Markdown now lives in `plugins/extended-markdown/frontend/` and uses only these public
interfaces. All three settings, plugin identity, galleries/figures/spoilers/task-block source,
rendering, exports and multi-image insertion behavior remain. It stays enabled on first discovery;
saved choices survive and safe mode stops it. Parser support for gallery images/captions and task
IDs, attachment retention, note validation and MCP reading/writing conventions remain in core as
saved-data contracts. This conversion does not introduce arbitrary new backend syntax registration
or move note data. Disabling the plugin still leaves those saved notes safely readable.

### Tasks, editor integration and UI SDK (1.9)

Declare `permissions: {"tasks": ["read", "edit"], "notes": ["read", "edit"]}` as needed.
`plugin.tasks.records` offers `list(trashed=False)`, `get(id)`, `history(id)`,
`create(title, body=…, parent_id=…, due=…, status=…)`, `update(id, changes)`,
`move(id, parent_id=…, before=…, after=…)`, `set_status(id, status, with_subtasks=False)`,
`delete(ids)`, `restore(batch=… or task_id=…)` and `undo(history_id)`.
Reads require tasks `read`, mutations tasks `edit`. Record methods return ordinary dictionaries
and lists; they use the existing validation, task history and change events.

`plugin.tasks.overview()`, `refs()` and `task(id)` additionally require notes `read` because
results include associated note paths/titles. `create(data, by="human")` accepts the existing
creation fields, optional `note` and `after_line`; using `note` also requires notes `edit`.
`add_to_note`, `remove_from_note` and `add_image(filename, bytes)` require tasks `edit` and notes
`edit`. `convert(text)` requires tasks `edit`; the editor applies its returned replacement text.
`notes_changed(client_id=None)` reports the resulting note update to open pages. Public
`backend.plugins.tasks` exports the task errors, note error, existing query matcher, token writer
and client-header name. `plugin.notes.resolve(ref)` resolves a path/wiki link/title under notes
`read` without changing files. All task records and note integrity stay in core.

Frontend `p.tasks.register(integration)` installs one Tasks UI provider at a time. Its `page`
supplies `open`, `close`, `isOpen`, `panelButton`, `shortcut` and `changed`; `notes` supplies the
published `TaskNoteHooks` callbacks for task preview/actions, side panel, note saves, completion,
new tasks, title HTML and portable tasks. `render` supplies task and task-block HTML. `destroy`
retires plugin resources. Registration and removal update the host layout, navigation and renderer.

The public `frontend/src/plugins/task-runtime.ts` module exposes shared `createEditor`, `loadMD`,
`createRenderer`, rendering helpers/settings, UI utilities/dialogs/menus, window opening, typed
app events and navigation, task/note record types, note/citation indexes, current editor, a plain
current-note snapshot and library selection/view snapshots. This explicit SDK is shared as
`/js/plugins/task-runtime.js`; it does not expose the mutable app store or private workspace.
Plugins must clean up subscriptions and editor/form instances they create. SDK modules and the
host's CodeMirror/Markdown libraries stay singletons; no runtime packages or CDN are fetched.
`p.backend.patch(path, body)` adds scoped PATCH alongside the existing plugin route methods.

Only the bundled Tasks plugin retains the five historical MCP tool names and Mod-Shift-K.
User/dev replacements keep normal tool prefixes and shortcut restrictions. The bundled plugin's
initial MCP availability is preserved; saved user choices take precedence. Safe mode stops the
UI/routes/tools without removing records, history, attachments or note tokens. See [tasks](tasks.md).

The standalone MCP host does not build frontend modules: it can start contributed Python tools
without Node/esbuild. Only the web app bundles a plugin frontend.

Import public Python helpers with `from backend.plugins import tasks` (or `scholarly`),
then call their documented members. The frontend `api.ts` declarations reference the public
Markdown/task SDK types; use those declarations together for TypeScript integrations.

`noteIndexSnapshot()` returns a plain copy of the host note listing. Tasks follows the
`notes-count` UI event for this window's own writes, and notes/library change notifications
for other clients, so its private picker/completion indexes stay current without sharing the
mutable workspace. Pending index responses are generation-checked and ignored after unload.


### Note export contributions (1.14)

`p.notes.exports.register({id, title, icon?, run(note)})` adds an entry to Notes → Export.
`run` receives a plain `{path, title, text}` snapshot using the editor's current text when open,
including unsaved changes. The host prefixes the ID with the plugin ID, catches invocation
errors and removes the entry when the plugin unloads. The returned disposer removes it early.
An export menu left open across plugin removal cannot invoke a retired provider.

The public task/editor SDK also exports `exportNoteFile(path)` (the saved note),
`exportBibliography(ids)` (the existing BibTeX clipboard writer), `exportMathSettings()`
(`{physics, macros}`), `splitFrontmatter`, `noteSection` and `downloadBlob`. Together with
`noteIndex`, `citationIndex`, `loadMD` and `createRenderer`, these let a plugin build an offline
export without importing private workspace/store modules. The Markdown SDK exports
`parseDefinitions` for the same macro parsing used by the host.

The bundled Export Formats plugin registers a LaTeX project download here. Its ordinary
bibliographic writers and their `/api/export` responses remain text-only; project ZIPs are
built in the browser with no runtime dependency or extra server route. See
[Exporting a note](notes.md#exporting-a-note) for files, options and conversion limits.
