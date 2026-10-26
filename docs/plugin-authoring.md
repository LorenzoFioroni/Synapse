# Plugin authoring quickstart

Start with a small plugin that does one job. This guide walks through the runnable
[Hello Library example](examples/hello-library/); [Plugins](plugins.md) is the complete API
reference. [Recently Added](examples/recently-added/) shows events, SQLite migrations, fields,
schedules, MCP tools and contextual UI when you need them.

## Run the example

From the repository root, after installing Synapse's development dependencies:

```sh
plugin_sandbox=$(mktemp -d)
python server.py --data-dir "$plugin_sandbox" --dev-plugins docs/examples --port 8806 --no-browser
```

Open `http://127.0.0.1:8806`, then Settings → Plugins → Hello Library → Turn On. Its page appears
in the sidebar's list. Open Greeting is also in the command palette, with Cmd/Ctrl+Alt+Y.
Press the plugin's **Settings** button and change Message in its section; the page reads that value from its own backend route. Refresh reads
it again immediately. With `--dev-plugins`, saving a plugin file restarts that plugin.

The example needs no network or additional packages. The temporary data directory keeps this
exercise separate from your library. To install normally, copy the example's `hello-library`
folder into your library's `data/plugins/` and press Look Again in Settings → Plugins.

## Keep the pieces small

```text
hello-library/
  manifest.json           identity, entry points and declarative settings
  backend/__init__.py     activate(plugin), registering GET /message
  frontend/main.js        activate(p), registering a page and command
```

The folder name and manifest `id` must match. Choose a new id when adapting the example, keep
registration ids stable within that plugin, and set `api` to the minimum compatible API version
(the example uses `^1.0`). Declare only the library, network and MCP capabilities you use; this
example needs none. Permissions describe capabilities and gate supported helpers; plugins execute
trusted Python/JavaScript code and are not a security sandbox.

Use the public Python import `backend.plugins` and the frontend `PluginContext` passed to
`activate`. Copy [`frontend/src/plugins/api.ts`](../frontend/src/plugins/api.ts) beside the frontend
as `api.ts` for editor type checking of the example's JSDoc. That file contains types only. App
modules, core navigation, note hooks and `plugin.synapse` are internal interfaces for bundled core
plugins, not third-party extension points.

As the plugin grows, keep activation as wiring. Move domain logic and queries into modules in
its backend package. Routes parse/validate inputs and shape responses; event listeners, routes
and MCP tools call the same domain functions. Return JSON from routes and call them with
`p.backend.get/post/put/delete`; paths are relative to `/api/plugins/<plugin-id>/`. Raise
`PluginError` for expected user-facing validation errors; unexpected exceptions count as faults.

## Choose the interface that fits

| Feature | Extension point |
|---|---|
| A standalone screen | `p.pages.register`; open with `p.pages.open` |
| A list of papers | Backend `@plugin.item_source`, frontend `p.sources.open` |
| An item attribute | `plugin.fields.declare`, with stored or refreshed values |
| Contextual details or actions | `p.itemPane.register`, `p.itemActions.register` |
| A command or shortcut | `p.commands.register` (respect reserved keys) |
| Simple preferences | Manifest `settings`; standard controls appear in the plugin's Settings section |
| A status panel or richer preferences | `p.settingsPage.register` (a part of that section; `shows` for settings it draws itself) |
| Lookup providers | Backend `@plugin.metadata_source` / `@plugin.pdf_source` |
| Reference lists (API 1.1) | Backend `@plugin.reference_source` |
| Text exports (API 1.1) | Backend `@plugin.export_format` |

Do not add a standalone page just to expose a provider or item field. A page belongs to the host:
it handles workspace switches and browser history. Use `p.library.show` to open an item instead of
setting workspace attributes or manipulating Synapse's sidebar yourself.

## Own settings, data and cleanup

Read backend values from `plugin.settings` when needed. In the frontend, use `await p.settings()`
and `await p.saveSettings(changes)`; settings can change without reactivation. Use
`@plugin.settings.on_change` when backend work must react to a change. Never write `plugins.json`
or the app's settings file directly.

Use `plugin.storage.json` for independent small values and `plugin.storage.sqlite` for records.
Append migrations; never rewrite earlier migrations or a user's database. App and standalone MCP
hosts can run at the same time: individual JSON updates are coordinated, but a `get` followed by
`set` is not an atomic transaction. Use a SQLite session for counters or other read/modify/write
operations. Schedules and queued jobs run in the app host; avoid duplicate background threads in
`activate`, which also runs in the standalone MCP host.

Frontend registrations are removed when the plugin stops. Each page, item section or Settings
`mount(el)` must return a **synchronous cleanup function**, rather than making `mount` itself
async. It may start async work internally. Hello Library demonstrates both an `alive` flag and
request sequence: a late response cannot paint a removed mount or replace a newer result. Treat
`AbortError` from a stopped plugin as cancellation. Backend helpers do not expose an abort signal;
use those guards even when a request cannot be cancelled.

Mount cleanup removes mount-owned subscriptions, listeners and resources. Use `p.onUnload` for
activation-wide resources, for example:

```js
const controller = new AbortController();
window.addEventListener('resize', onResize, { signal: controller.signal });
p.onUnload(() => controller.abort());
```

Dispose a registration early using the function `register` returns when it is no longer needed.
On the backend, register teardown with `@plugin.on_unload` (or `deactivate(plugin)`) for resources
you create. Cleanup should be safe during disable, restart and failed activation. Put rendered
text in `textContent` or escape it with `p.ui.escapeHtml`; scope CSS to `[data-plugin="your-id"]`.

## Verify and document the result

The example's smoke test is `tests/plugins/test_examples.py`: activation, route output, changed
settings, disable and reactivation with settings preserved. Run it with:

```sh
python -m pytest -q tests/plugins/test_examples.py
node --check docs/examples/hello-library/frontend/main.js
```

For your plugin, test domain behavior and migrations with temporary storage and fake network
responses. Test public routes and lifecycle with the plugin fixtures in `tests/plugins/conftest.py`.
Follow the repository's AGENTS.md conventions and run the [repository checks](testing.md) before
committing changes to Synapse.

For UI changes, use an isolated library and check entry points from Library, Notes, Graph and
Tasks; Back/Forward; the command palette and shortcut; disable/restart while open; late responses;
and no duplicate controls or subscriptions after repeated reactivation. The `plugins`, `outside`
and `navigation` browser checklists show these patterns, including the reusable host test hooks.

Document what the plugin does, where users find it, its settings, data format/migrations and any
network dependencies alongside the code. Synapse must run offline; Python dependencies must work
offline once installed and frontend assets must be local, with any vendored library's licence and
build instructions. Link new subsystem documentation from [Architecture](architecture.md).
