# Notification center

The bell beside Settings opens notification history. Its red badge counts unread entries;
opening the center does not mark them read. Mark individual notices or all notices read,
dismiss one, or clear completed entries. Running jobs stay visible until they finish. Escape
closes the panel and returns focus to the bell. The small browser-tab favicons also show the count,
while the 192 px install/launcher icon remains stable; installed-app icons use the browser's
Badging API when available.

Info, Success, Warning and Error describe severity. **OS delivery is separate from severity**:
arXiv Today's papers-ready notice is Success and requests OS delivery. OS delivery is considered
on once browser permission exists; enable or disable it under Settings → Notifications. The
notification center itself only manages history. A denied/unsupported OS capability never prevents
a notice appearing in the center. Synapse does not request permission on startup: the Settings
button is the explicit permission request. Support depends
on the browser, secure context and OS; mobile browsers and the desktop webview may only support
the in-app center. No background push service is installed: the app must be open to deliver notices.

PDF searches/imports, bulk reference fetching, adding papers, background metadata retrieval
and full-text indexing report progress in the center, including their result and available
Cancel/Review actions. Short confirmations and time-sensitive Undo messages may still use toasts.

## Ownership and persistence

`frontend/src/notifications/model.ts` owns serializable records, unread state and retention.
`center.ts` owns the bell, panel, live job handles and callbacks; `desktop.ts` isolates optional
OS notifications, their browser-local Settings control, app badging and the favicon overlay. The toolbar mounts the center once.
`ui/toast.ts` retains `progressToast` as an adapter for existing callers; `finish(message, options)`
updates the same job entry with its result rather than making a new bottom banner.

History is local to this browser profile and origin, stored under one
`synapse.notification.<id>` localStorage key per notice. Storage events update other windows;
separate keys prevent unrelated writes replacing one another. Keep the latest 100 completed
entries plus running jobs. Read state is shared across these windows, not across devices. If
storage is unavailable the page retains notices in memory. Leaving/reloading a page marks its
running jobs interrupted, since client progress tracking stops; the server may still finish work.
`ownership.ts` also holds a Web Lock for each window's notification owner. At startup, every
10 seconds and on focus it checks the browser's held/pending locks, marking orphaned running
notices interrupted so Dismiss and Clear completed work after a crash or reboot. Live owners
in other windows are preserved; elapsed time alone never makes a job stale. Results are re-read
before recovery so concurrent completions are not overwritten. Where Web Locks are unavailable
or fail, notices owned by another/previous window offer Dismiss as a fallback; this only removes
the notification and does not cancel server work.

Action callbacks and Cancel handlers belong to the window that created them and are not stored.
Reloading keeps text/history but drops callbacks. Plugin stop drops its callbacks. Plugins can
supply `sourceId` for a persistent Show action that opens their source even after reloading.
arXiv's existing backend `/news` claim emits its daily ready notice in one window; the claim is
atomic to prevent concurrent windows publishing duplicates. This history does not replace the
plugin's daily notification preference.

## Plugin interface

`p.notify(message, {type, desktop, sourceId, action, transient, timeout})` posts to the center by
default. `type` is `info`, `success`, `warning` or `error`; `desktop: true` independently offers OS
delivery when the user enabled it. `sourceId` is scoped to the plugin. `action` is a callback in
this window, cleaned up on plugin stop. Use `transient: true` for a short toast (for example Undo);
`timeout` applies only to those toasts. Old callers remain valid but their default destination is
the center. An OS notification click focuses Synapse and opens the center.

Verification: `tests/js/notifications.test.mjs` checks persistence, retention, read state,
independent delivery/severity, multi-window writes, orphan recovery, concurrent completions
and interrupted owners.
`python tests/e2e/run.py notifications` checks the actual bell, progress/results, arXiv plugin
notification, OS opt-in/fallback, keyboard access, reload, vanished/live owners, manual fallback
dismissal and cross-window state.
