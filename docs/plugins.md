# Plugins

A plugin adds a feature to Synapse without changing Synapse's own code: a folder with a
`manifest.json`, some Python for the server and some JavaScript for the app. Each part gets an
object to register what it offers. This page is both the guide for writing one and the description
of how Synapse runs them.

The design and the plan for the rest of the plugin API (per-item fields, paper feeds) are in the
design doc "Synapse plugin API v1". What exists today:

- **Backend** (Python): routes, tools for Claude, events from the library and notes, reading the
  library, settings, stored data, schedules and background jobs, the web (within the hosts its
  manifest names), Python packages of its own, a log.
- **Frontend** (JavaScript): pages with a sidebar entry, commands in the command palette (with keys),
  sections in the item pane, actions in the item menu, notifications, calls to its own routes.

## Using plugins

- Put a plugin's folder in **`data/plugins/`** (next to `library.db`), then open
  Settings → Plugins and press **Look Again**. A new plugin is listed but always off.
- Turning one on asks first and lists what it may do (read the library, connect to sites, offer
  tools to Claude). A plugin runs with full access to the library and the computer, like any
  program you install: turn on only plugins you trust.
- **More** shows its settings (drawn from its manifest, saved as you change them), whether its tools
  for Claude are also offered to AI apps on other computers (`/mcp`, off by default), what it
  serves and listens to, **Restart** (to load changed code) and its **Log**.
- A plugin that fails while starting, or fails three times within ten minutes, is turned off and
  Settings says why. Turning it on again tries again.
- `python server.py --safe-mode` starts no plugin at all, for when one keeps the app from working.
  `mcp_server.py --safe-mode` does the same for the MCP server Claude starts.
- Restart Claude after turning on a plugin with tools if they do not show up: some AI apps read the
  list of tools only once.
- A plugin's pages are listed under **Plugins** in the sidebar; its commands are in the **command
  palette** (⌘⇧P, Ctrl+Shift+P or F1), together with Synapse's own.

Plugins that come with Synapse live in the repository's `plugins/` folder and are off until turned
on. A plugin in `data/plugins/` with the same id replaces the one that comes with Synapse.

## Writing a plugin

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
API once, with a JavaScript frontend; copy it into `data/plugins/` to try it. A plugin folder looks
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
| `permissions` | `network`: the hosts `plugin.http` may reach (`"export.arxiv.org"`, or `"*.example.org"` for a domain and its subdomains). `library`: `read` (and later `add`, `edit`). `mcp`: true to offer tools to Claude. Unknown permissions make the manifest invalid. |
| `python` | Python packages it needs, as requirements (`["scirate>=0.0.7"]`): installed with pip when it first starts (see [Python packages](#python-packages)). |
| `settings` | What the user can set, see below. |

Each setting has a `key` (lowercase, `_`), a `type`, a `label`, an optional `description` and
`default`:

| Type | Value | Extra |
|---|---|---|
| `bool` | true / false | |
| `int`, `float` | a number | `min`, `max` |
| `text` | a string (≤ 10,000 characters) | |
| `choice` | one of `options` | `options`: `["a", "b"]` or `[["a", "Label A"], …]` |
| `multi` | a list of strings | `options` (then the user ticks them) or none (a comma-separated field) |

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
| `plugin.http` | `get(url, params=…)`, `post(url, json=…)`, `request(method, url, …)`, `limit(host, seconds)`: the web, within the manifest's `network` (see [Reaching the web](#reaching-the-web)). |

Raise **`PluginError("…")`** for a problem the user or Claude should read: a route answers 400 with
it as the error, a tool returns it as an error Claude can act on, and it does not count as the
plugin failing. Any other exception from a route, a tool or a listener is logged with its traceback
and counts towards turning the plugin off.

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

Prefer `plugin.storage.sqlite` for records. Its `migrations` list brings the schema from version
`i` to `i + 1` (kept in `PRAGMA user_version`); only ever append to it, so every user's database
can be updated. Before migrating a database that already holds data, Synapse makes a backup (as
before other risky operations). A database written by a newer version of the plugin is refused
rather than damaged.

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
  JSON values. With a `key`, a job is not queued again while one with that key waits. A job that
  fails is tried again after 30 s, 2 min and 10 min (`retries` times); failing to reach the web
  (`HttpError`) does not count against the plugin, any other error does.
- Settings → Plugins → More lists a running plugin's schedules and how many jobs wait.

### Reaching the web

`plugin.http` is the only way a plugin should reach the web (`requests` would work, but skips all
of this):

- Only the hosts its manifest's `network` names, and only public addresses (not this computer or
  the local network), also after redirects.
- Requests to one host are spaced (0.5 s, 3 s for arXiv; more with `plugin.http.limit(host,
  seconds)`), across all plugins, so two plugins cannot overload one service.
- A User-Agent naming Synapse, the plugin and the contact address from Settings → Online
  Services, as scholarly APIs ask.
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
| `p.pages.register({id, title, icon, mount})` | A page: an entry under **Plugins** in the sidebar; when chosen, the page takes the place of the library (as Tasks does) and `mount(el)` draws it. `p.pages.setBadge(id, value)` puts a count on the entry, `p.pages.open(id)` shows it. |
| `p.commands.register({id, title, key, when, run})` | A command in the palette, named after the plugin. `key` like `Mod-Alt-r` (Mod: ⌘ on a Mac, Ctrl elsewhere) needs Mod or Alt and cannot be one of Synapse's keys; a key that cannot be used is left out (the console says why). |
| `p.itemPane.register({id, title, when, mount})` | A section at the end of the item pane, for the items `when(item)` accepts; `mount(el, item)` runs once the selection rests. |
| `p.itemActions.register({id, title, icon, when, run})` | An entry in the item menu for the selected items, also in the palette. |
| `p.notify(message, {type, action, timeout})` | A notification, named after the plugin. |
| `p.backend.get/post/put/delete(path, …)` | The plugin's own routes (`/api/plugins/<id><path>`), with the page's sign-in; resolves to the JSON answer. |
| `p.library.selected()`, `.item(id)`, `.show(id)` | The selected items, one whole item, showing an item in the library. |
| `p.onChange(fn)` | `fn(kinds)` when something changed: what the change feed reports (`library`, `notes`, `tasks`, `annotations`, `plugins`), and `library` when this window's item list reloads. |
| `p.settings()` | The settings' values (a promise). |
| `p.ui` | `icon(name)`, `escapeHtml(text)`, `confirm(…)`, `prompt(…)`, `formatDate(iso, withTime)`: the app's own. |
| `p.onUnload(fn)` | Runs when the plugin stops. |

Rules for a plugin's frontend:

- Draw only inside the element a mount function gets: the rest of the app's markup is not part of the
  API. Return a function from `mount` to clean up (stop timers, remove listeners).
- The element sits inside one with `data-plugin="<id>"`: start the stylesheet's rules with
  `[data-plugin="<id>"]` and use the colour tokens (`var(--text)`, `var(--accent)` …) so light and
  dark themes work.
- Everything registered through `p` is taken away when the plugin stops; errors in its functions
  are reported (a notification and the browser console) instead of breaking the app.
- The app loads the module again when the plugin restarts (its URL carries the start time). Modules
  it imports keep their URL, so the browser may keep the old ones until the page reloads: a single
  file (or a bundle) is the simplest.
- TypeScript: point `frontend` at the `.ts` file. Synapse bundles it (with what it imports) into
  one module with esbuild when the plugin starts: the copy Synapse builds its own frontend with
  (`frontend/node_modules`, after `npm ci`), else one on the PATH. A build error turns the plugin
  off with esbuild's message. Bundling also avoids the cache problem above. The bundle is in the
  plugin's data folder (`.build/`).

## How the host works

`backend/plugins/` sits just above `library` in the layers (it imports only `config`, `settings`,
`changes` and `library`; see [architecture](architecture.md#layers)). `Services` creates the `PluginHost` and connects it to the library: item changes
from `PaperRepository.on_items`, key changes from `on_citekey_change`, saved notes from
`NotesService.saved_listeners`. `create_app` starts it with `role="app"`, `mcp_server.py` with
`role="mcp"`.

| Module | What it does |
|---|---|
| `host.py` | `PluginHost`: finds plugins (`scan`), starts and stops them, runs their routes and tools with failure counting (`route`, `run`, `mcp_tools`), sends events (`emit`), and what Settings shows (`info`, `overview`). |
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
  lists plugins and when Claude lists tools).
- **Failures.** `fault()` logs the traceback; three within ten minutes turn the plugin off (from a
  separate thread, since the failure may be on the plugin's own worker), record the reason in
  `plugins.json` and tell open pages through the change feed (`plugins`).
- **Routes** are matched per plugin with a Werkzeug `Map` behind one catch-all endpoint
  (`/api/plugins/<id>/…` in `backend/api/plugins.py`), so plugins can be turned on and off while
  the app runs. Settings → Plugins talks to `/api/plugin-host/…`.
- **Tools** are asked for at each `tools/list` and `tools/call` (`Server.extra` in
  `mcp/protocol.py`); `build_server(..., remote=True)` (the `/mcp` endpoint) offers only those of
  plugins with `remote_mcp` on. A `PluginError` reaches Claude as a tool error.
- **Dev plugins.** With `--dev-plugins`, `roots()` adds that folder (source `dev`, replacing a
  plugin of the same id), and a thread compares the newest modification time of each running dev
  plugin's files (`loading.newest`) every second; a newer one restarts it (`check_changes`), which
  the change feed tells the open windows.
- **Data.** `data/plugins/` holds plugins (code, not in backups), `data/plugin-data/<id>/` what each
  stores plus dot files that are left out of backups: its log `.plugin.log`, `.python/`, `.build/`,
  `.jobs.sqlite`, `.schedules.json`. `data/plugins.json` is their state. Backups hold `plugins.json` and `plugin-data/` (SQLite files copied with the backup API);
  restoring stops the plugins, puts both back (keeping today's logs) and starts them again.
- **The frontend** (`frontend/src/plugins/host.ts`) asks `/api/plugin-host` which plugins run, then
  `import()`s each one's module from `/api/plugin-files/<id>/<path>?v=<started>` and loads its
  styles. It syncs at start, when the change feed names `plugins`, and after Settings turns one on
  or off (that window's own change is not in the feed). `PluginHost.asset` serves only the styles
  file and files in the folder of the frontend module, never Python files or the manifest.
- **Extension points** (`frontend/src/extend.ts`) are registries the app's views read: `commands`
  (the palette, `views/palette.ts`; `main.ts` registers Synapse's own and runs plugins' keys),
  `pages` (`views/pages.ts`, the sidebar's Plugins section), `itemSections` (the item pane) and
  `itemActions` (the item menu). The plugin host prefixes ids with the plugin's id, wraps every
  function so its errors are reported, and passes copies of items.
- **Trust.** Plugins are not sandboxed: Python plugins run inside the server with its access, and a
  plugin's frontend runs in the app's page, with its access.
  Permissions are shown before turning a plugin on and checked where the API can check them
  (`plugin.library`, `plugin.mcp_tool`), but they do not contain a plugin that ignores the API.

## Tests

`tests/plugins/` (manifests, the host, storage and backups, the files of a frontend; `test_http.py`
against a local server, `test_jobs.py` for cron rules, schedules and jobs, `test_tooling.py` for
background work in the app and the MCP server, packages with a stand-in for pip, TypeScript builds,
the scaffold and dev plugins; the example plugin is installed by the `example` fixture and
`make_plugin` writes small ones), the import rule
in `tests/test_layers.py`, `tests/js/extend.test.mjs` (registries, keys, the palette's ranking), and
the browser checklist `plugins` (Settings → Plugins and the example plugin's page, command, section
and action).
