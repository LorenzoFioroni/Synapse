# Plugin API review — 23 October 2026

This review covers the open Plugin API stack through `2e619cc` (PR 82), against
[the architecture and conventions](architecture.md) and `AGENTS.md`. Each PR was compared with
its own base, then the integrated stack was checked for storage, lifecycle, API and offline
contracts. The original PRs remain unchanged; the corrections are a follow-up based on PR 82.
Include these corrections when merging the stack.

## Scope and documentation

Every PR below includes README and matching subsystem documentation with its code. The fixes
below address inconsistencies and defects found in that otherwise documented structure.

| PR and reviewed head | Responsibility and assessment |
|---|---|
| [70](https://github.com/LorenzoFioroni/Synapse/pull/70), `94390dd` | Host, manifest, routes, MCP, settings and storage. Public imports and layer boundaries are enforced by tests. Shared-file writes needed process coordination; corrected below. |
| [71](https://github.com/LorenzoFioroni/Synapse/pull/71), `840be55` | Frontend context, registries and palette. Registries own disposal; host cleanup and stopped-route handling needed corrections below. |
| [72](https://github.com/LorenzoFioroni/Synapse/pull/72), `08fe5b0` | Jobs, schedules, HTTP, dependencies, builds and development mode have separate modules and fake-service tests. The development watcher needed shutdown handling and local cron schedules needed DST handling. |
| [73](https://github.com/LorenzoFioroni/Synapse/pull/73), `5940236` | Field declarations/storage and application indexing are separated in `fields.py`/`itemfields.py`. Identity, null values, batching, filters and backups have regression coverage. No additional blocking finding identified. |
| [74](https://github.com/LorenzoFioroni/Synapse/pull/74), `2349382` | Outside records, sources, connections and application actions sit outside HTTP endpoints. The browser fixture depended on live metadata; corrected below. |
| [75](https://github.com/LorenzoFioroni/Synapse/pull/75), `6b84e4a` | Citation Counts uses the public Plugin API, batched field refresh and shared HTTP credentials. Provider failures and zero/unknown values have fake-service coverage. No additional blocking finding identified. |
| [76](https://github.com/LorenzoFioroni/Synapse/pull/76), `5738ffa` | arXiv Today separates RSS parsing and schema from orchestration; jobs, scoring, retention and frontend actions use the public API. Fake feeds and reference lists cover its behavior. No additional blocking finding identified. |
| [77](https://github.com/LorenzoFioroni/Synapse/pull/77), `1df88c2` | Related removal of Scholar HTML scraping; already included in PR 78. README/references explain remaining PDF sources. Do not apply it twice as a separate dependency. |
| [78](https://github.com/LorenzoFioroni/Synapse/pull/78), `2fef07f` | Core defaults, legacy settings and Autocompletion. Only bundled manifests acquire core privileges. Safe-mode synchronization/UI and stale documentation needed corrections below. |
| [79](https://github.com/LorenzoFioroni/Synapse/pull/79), `50a71a6` | PDF source plugins supply candidates; core downloader retains validation and persistence. Provider and download tests cover the boundary. No additional blocking finding identified. |
| [80](https://github.com/LorenzoFioroni/Synapse/pull/80), `a2d479b` | Graph code lives in its core plugin, exposed through typed note hooks; the data builder stays outside the route. Model and browser checks cover normal use. No additional blocking finding identified. |
| [81](https://github.com/LorenzoFioroni/Synapse/pull/81), `339f624` | Figures owns rendering/export hooks; core Markdown parsing and attachment retention still understand the stored format while disabled. Unit and gallery checks cover this boundary. No additional blocking finding identified. |
| [82](https://github.com/LorenzoFioroni/Synapse/pull/82), `2e619cc` | Tasks keeps records/integrity in core and compatibility routes/MCP schemas; Metadata Sources keeps normalization in core and provider logic in its plugin. Lifecycle, priority/fallback and browser coverage exercise the boundaries. No additional blocking finding identified. |

## Findings and corrections

1. **P1 — app/MCP storage writes can collide (70).** Per-instance thread locks did not protect
   `plugins.json` or JSON stores between hosts. Both used a fixed temporary filename. Simultaneous
   read/modify/write could lose another host's keys or fail during replacement; SQLite migrations
   could also read the same old schema concurrently. `files.py` now supplies reentrant thread and
   process locks plus unique, atomic JSON replacement. Migration versions are read under that lock.
   A regression starts four independent processes and checks that every key survives and the shared
   database initializes once. Formats and existing data stay unchanged.
2. **P2 — partial frontend activation leaks controls (71).** An activation that registered a page
   or command and then threw left those registrations installed. The host now unloads partial
   activation, disposes late registrations and retries that frontend only after a plugin restart.
   A browser fixture registers UI then throws and checks that the UI is gone.
3. **P2 — disappearing routes can hang frontend synchronization (71).** The request wrapper
   deliberately returned an unsettled promise for a stopped plugin. Async activation could then
   prevent every later synchronization. It now rejects with `AbortError`; guarded callbacks do not
   report cancellation as a plugin fault. Stopped frontends unload before field/source refresh.
   A browser fixture disables a plugin as its activation request arrives and checks sync finishes.
4. **P2 — the development watcher outlives its host (72).** Its unbounded loop continued after
   `stop()`. Shutdown now signals and joins the watcher before unloading plugins; restart creates
   a fresh watcher. A regression checks the old thread exits and exactly one replacement starts.
5. **P2 — safe-mode core changes are inconsistent (78).** `refresh()` skipped all synchronization
   in safe mode, although core plugins run there. Settings disabled their switches and hid Restart.
   Both now honor the same core-only rule as startup. Two hosts test off/on synchronization; the
   browser checks that installed plugins stay suppressed while core switches and Restart work.
6. **P2 — local cron schedules drift after daylight-saving changes (72).** Capturing the local
   timezone with `datetime.now().astimezone().tzinfo` retained today's fixed UTC offset. A daily
   09:00 job could run at 08:00 or 10:00 after the seasonal change. Default-local cron schedules
   now resolve the next wall time through the OS's rules for that date. Regressions cover both
   spring and autumn transitions; explicit zones and elapsed-time intervals retain their behavior.
7. **Maintainability — discovery and core migration crowded the host (70/78).** `catalog.py` now
   owns manifest discovery, precedence and initial legacy-preference migration. `host.py` keeps
   lifecycle/dispatch orchestration; `files.py` owns file coordination. The module map documents
   both. Contradictory safe-mode instructions and a reference to an unavailable design document
   were corrected in the plugin guide.

## Browser reliability

- Tab shortcuts now use Control on macOS and Alt elsewhere; editor undo uses Cmd/Ctrl.
- Scroll assertions wait for the requested document and target its visible retained editor.
  Previously they scrolled a hidden editor and measured paragraph 1 repeatedly.
- The shutdown check navigates away before closing the headless target and keeps the browser
  alive for the goodbye request. Playwright's target destruction does not dispatch `pagehide`;
  native window closure remains a manual desktop check, not a claim of this automated test.
- Outside-paper checks turn off metadata providers for synthetic identifiers. A real arXiv paper
  had started replacing a fixture title; the check now has the same expected result offline.

See [testing](testing.md#reliable-tab-scroll-and-plugin-checks) for the test mechanics and
[plugins](plugins.md#how-the-host-works) for the ownership map. Plugin code is trusted code running
inside Synapse, not an isolation boundary; this review does not change that documented contract.

## Validation

- The latest existing CI workflow for every reviewed head (70–82) succeeded.
- Integrated suite: **704 passed, 2 skipped**, including the JavaScript suite. Ruff lint and
  formatting, mypy, and frontend/browser-extension TypeScript checks passed.
- Browser checks: tabs **74/74**, render **40/40**, plugins **48/48** (including safe mode and
  failed/interrupted activation), outside papers **34/34**, Tasks **79/79**, Graph **38/38**,
  Figures/gallery **51/51**, Autocompletion **29/29**.
- Tabs and rendering were repeated without retry mode. These are the relevant checklists,
  not a claim that every manual browser checklist or native window manager was tested.

The process-lock implementation supports POSIX and Windows, but local execution here was on
macOS. Native window-close behavior and Windows-specific locking still need their platform's
manual checks; the automated farewell test deliberately states its narrower scope.

## Follow-up: plugin interface consistency

The interface audit covered all eight bundled plugins, including their disabled state. It found
additional visible leftovers after the initial review:

| Plugin | Placement and lifecycle result |
|---|---|
| Tasks | Opens under Plugins in the sidebar; its toolbar workspace button is removed. Its plugin owns the entry, open/overdue badge and disposal through the internal navigation registry. Task deep links, note controls and the keyboard shortcut remain supported. |
| Graph | Now has its own sidebar entry under Plugins, with active-state highlighting. Contextual local-graph controls and Show in Graph remain alongside notes/items; stopping the plugin removes its entry and graph controls. |
| Autocompletion | Its Settings section is removed immediately while Settings stays open, and reactivation mounts exactly one section. Removed active sections return Settings to General. Status and save responses that were superseded or belong to a removed section are ignored. Stopping the plugin clears visible ghost text and aborts pending suggestions in retained editors. |
| Figures | Remains an inline rendering/export integration; disabling it redraws notes as ordinary images and Markdown, and reactivation restores carousels and numbering. No standalone empty page is added. |
| Citation Counts | Remains item fields, filters and columns. The active field definitions control visibility while stored values and column preferences survive disable/enable. |
| arXiv Today | Its two paper lists already use plugin sources in the sidebar; disabling it removes those lists and returns an open source view to the library. |
| Metadata Sources | Supplies lookup providers to library actions; those actions remain shared entry points because other plugins may supply providers too. Disabling the bundled providers preserves records and reports when no matching source remains. |
| Open-Access PDFs | Supplies PDF candidate sources to the shared library action; disabling it preserves attachments and still permits other plugins' sources. |

`extend.navigation` is an internal registry for existing core workspaces, rather than a new public
Plugin API. The sidebar renders its entries without importing Tasks or Graph. Third-party plugins
continue to use `p.pages` and item sources. `views/settings/pluginSections.ts` owns the mounted
Settings sections and their disposable registry subscription, preserving unrelated controls.

Browser regressions cover repeated off/on cycles for Tasks and Graph, open Settings sections,
late status responses, and disabling a visible suggestion without reloading. The plugin checklist
waits for startup before sending its Settings shortcut, including when testing a safe-mode server.

Follow-up browser validation: Autocompletion **37/37**, Graph **44/44**, Tasks **82/82**, plugin
host **48/48**, Figures/gallery **51/51**, outside papers **34/34**, tabs **74/74**. The selected
provider/field/JavaScript suite passed **199 tests**. A fixed-sleep autosave unit test exposed by
the full run now awaits the public saved status, so it does not race the fake server on busy CI.

After those corrections, the full suite passed **704 tests, 2 skipped**; Ruff lint/format, mypy
and frontend/extension type checks passed.
