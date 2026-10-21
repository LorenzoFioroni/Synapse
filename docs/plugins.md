# Plugins

A plugin adds a feature to Synapse without changing Synapse's own code: a folder with a
`manifest.json` and some Python, which gets a `plugin` object to register what it offers. This page
is both the guide for writing one and the description of how the host runs them.

The design and the plan for the rest of the plugin API (the frontend side, background jobs, per-item
fields, paper feeds) are in the design doc "Synapse plugin API v1". What exists today is the
**backend host**: plugins can serve routes, offer tools to Claude, hear what happens in the library,
read it, have settings and store data.

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

Plugins that come with Synapse live in the repository's `plugins/` folder and are off until turned
on. A plugin in `data/plugins/` with the same id replaces the one that comes with Synapse.

## Writing a plugin

The example in [`docs/examples/recently-added/`](examples/recently-added/) uses every part of the
API once; copy it into `data/plugins/` to try it. A plugin folder looks like this:

```
data/plugins/recently-added/      the folder's name is the plugin's id
  manifest.json
  backend/                        a package (or a single .py file)
    __init__.py                   def activate(plugin): ...
    store.py
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
| `frontend`, `styles` | Accepted and listed; the app does not load them yet (planned for the frontend part of the API). |
| `permissions` | `network`: host names it will connect to (shown to the user; enforced once plugins get the shared HTTP client). `library`: `read` (and later `add`, `edit`). `mcp`: true to offer tools to Claude. Unknown permissions make the manifest invalid. |
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

### Trying a change

Edit the code, then **Restart** in Settings → Plugins (or turn the plugin off and on): the host
loads the code again. Watch the plugin's **Log** for errors.

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
- **Data.** `data/plugins/` holds plugins (code, not in backups), `data/plugin-data/<id>/` what each
  stores plus its log `.plugin.log` (dot files are left out of backups), `data/plugins.json` their
  state. Backups hold `plugins.json` and `plugin-data/` (SQLite files copied with the backup API);
  restoring stops the plugins, puts both back (keeping today's logs) and starts them again.
- **Trust.** Plugins are not sandboxed: Python plugins run inside the server with its access.
  Permissions are shown before turning a plugin on and checked where the API can check them
  (`plugin.library`, `plugin.mcp_tool`), but they do not contain a plugin that ignores the API.

## Tests

`tests/plugins/` (manifests, the host, storage and backups; the example plugin is installed by the
`example` fixture and `make_plugin` writes small ones), the import rule in `tests/test_layers.py`,
and the browser checklist `plugins` (Settings → Plugins).
