# Sync and devices

This page covers two related jobs: keeping every open window, tab and device in step with the
library, and deciding who may talk to the server at all. For where these pieces sit in the
backend, see [`architecture.md`](architecture.md).

| Area | Code |
|---|---|
| Change feed | `backend/changes.py`, `backend/api/changes.py`, `frontend/src/changes.ts`, `pollChanges` in `frontend/src/viewer/annotations.ts` |
| Viewer tab and app tab | `frontend/src/api.ts` (tab reuse), `frontend/src/viewer/notesBridge.ts`, `listenForQuotes` in `frontend/src/views/notes/notesView.ts` |
| Guard | `backend/access/guard.py` |
| LAN mode and pairing | `server.py`, `backend/access/devices.py`, `backend/access/lan.py`, `backend/api/devices.py`, `frontend/pair.html`, `frontend/src/views/settings/devices.ts` |

## The change feed

There is no push channel. Every page polls `GET /api/changes` and refreshes what the answer
names. This works the same for a second tab, an iPad on the LAN, background jobs and the MCP
server writing to `data/` from another process.

### Sequence numbers and the epoch

`ChangeFeed` (in `backend/changes.py`) is one object in `Services`, kept in memory. It holds:

- `seq`, a counter that goes up by one with every recorded change;
- `epoch`, a random string made when the server starts;
- a log of the last `KEEP` (2000) entries, each `(seq, client, kinds)`.

A page sends back the `seq` and `epoch` of its last answer: `GET /api/changes?since=N&epoch=E`.
`ChangeFeed.since` answers `{epoch, seq, changes, reset}`:

| Situation | `changes` | `reset` |
|---|---|---|
| First poll (no `since`), or `since` is ahead of the server | `[]`: start from here | `false` |
| `epoch` differs from the server's (the server restarted) | all three kinds | `true` |
| `since` is older than the oldest logged entry | all three kinds | `true` |
| Otherwise | the kinds logged after `since` that this client did not make | `false` |

Keeping the log in memory is a deliberate choice. A restart loses it, but the epoch tells every
page, and a page that cannot know what it missed simply refreshes everything.

### Kinds of change

| Kind | Recorded when |
|---|---|
| `library` | A write to `library.db` commits (items, folders, tags, attachments, smart folders). |
| `annotations` | A write to `library.db` commits during a request to `/api/annotations…` or `/api/attachments/<id>/annotations` (`kind_of`). |
| `notes` | A notes endpoint (`backend/api/notes.py`) answers a POST, PUT, PATCH or DELETE with a status below 400. |

Writes to `library.db` reach the feed through `Database.on_write`, which `Services` points at
`_library_written`. `Database.session` calls it after each commit that changed rows. A failure
there is swallowed, because telling others must never undo a saved change.

Writes made outside this server are noticed too. At each poll `_notice_outside_writes` compares
the modification time and size of `library.db`, `notes_index.db` and their `-wal` files with what
it saw last. A difference it cannot explain counts as `library` and `annotations`, or `notes`.
This is how a note written by the MCP server shows up (see [`mcp.md`](mcp.md)). A note file
edited by hand is only noticed once something updates `notes_index.db`, such as a rescan
(`POST /api/notes/rescan`) or the next start (see [`notes.md`](notes.md)).

### Not hearing about your own edits

Each page makes a random id when it loads and sends it on every request as the
`X-Synapse-Client` header (`CLIENT_HEADER`):

- the app: `CLIENT_ID` in `frontend/src/api.ts`;
- the PDF viewer: `CLIENT` in `frontend/src/viewer/annotations/model.ts`.

A change made during a request is logged with that id, and `since` leaves it out for the same
client. Without this, a window would reload its list after every edit it made itself.

Changes logged with no client are reported to everyone, including the window that caused them:

- background work outside a request (metadata and reference fetching, the open-access PDF search
  after a save from the browser button);
- requests without the header: the browser extension, the notes bridge's `fetch` calls in
  `notesBridge.ts`, and the last-chance save in `flushOnUnload`;
- writes noticed on disk.

### What each window refreshes

**The app** (`frontend/src/changes.ts`) polls every 4 seconds (`EVERY`) while visible, and at once
when it becomes visible again. It skips a poll while one is running or the page is hidden.

| Kind | Action |
|---|---|
| `library` | `A.loadItems()`: the item list, counts and sidebar. At most once every 2.5 s (`MIN_GAP`), because background fetching can write often. |
| `notes` | `notesChanged()` in `notesView.ts`: the open note checks the server copy, and the notes tree is redrawn. |
| `annotations` | Ignored: the viewer looks after its own. |

A refresh waits while the user is busy (`editing()`): a dialog or autocomplete list is open, a
form field has focus, or the note editor was typed in during the last 4 seconds. Fields save when
they lose focus, so reloading under them would lose what is typed. Pending kinds stay in
`pending` and are tried again at the next poll. `window.zcChanges` exposes the state for tests.

**The PDF viewer** (`pollChanges` in `frontend/src/viewer/annotations.ts`) polls on its own every
4 seconds, and when it becomes visible, once its attachment is known. It reacts only to
`annotations`: `refresh()` reloads the attachment's annotations and redraws them if any `id` or
`modified` changed. It also refreshes on window focus, and never while a text box is being edited.
Two viewer tabs on one PDF have different client ids, so each sees the other's edits. The record
format is in [`storage.md`](storage.md).

## Viewer tab and app tab

The viewer runs in its own tab (`viewer.html`). Two small mechanisms connect it to the app.

**One tab per PDF** (`frontend/src/api.ts`). `openAttachment` opens the viewer in a window named
`zc-pdf:<file url>` and remembers it in `pdfTabs`. A second click on a citation of the same PDF
finds that window and calls `window.zcViewerGoto({page, q, rect})` on it, then focuses it,
instead of opening a new tab. `notesBridge.ts` defines `zcViewerGoto`, which jumps there as a new
Back/Forward entry. The viewer also names its own window after its file, so a tab opened another
way can still be found by name. Cmd/Ctrl+click passes `newTab` and always opens a fresh tab.
`pdfTabFor` and `pointPdfTab` split this in two for callers that only know the target after an
`await`: the window must be opened synchronously inside the click, or popup blockers stop it.

**Quote and snapshot to note** (`notesBridge.ts`). The viewer asks on `BroadcastChannel('zc-notes')`
which app tabs have a note open:

```
viewer → {type: 'who', req}                    every Notes tab with an open note answers
notes  → {type: 'here', req, tab, path, title}
viewer → {type: 'insert', req, tab, markdown}  only the addressed tab inserts
notes  → {type: 'inserted', req, tab, path}
```

It waits 250 ms for answers. With none, or if the chosen tab does not confirm within 1.5 s, the
block goes to the item's paper note through `POST /api/notes/append` instead. The note side is
`listenForQuotes` in `notesView.ts`. The citation format is described in [`notes.md`](notes.md).

## The guard

The server has no accounts. `guard.install` adds a `before_request` hook that every request goes
through, pages and static files included. It makes three checks, in this order.

### 1. Host

The `Host` header must name the server by:

- a loopback name: `127.0.0.1`, `localhost` or `::1` (`LOOPBACK`);
- a name given with `--allow-host`, or the address given with `--host <address>`;
- in LAN mode only: any IP address literal, or this computer's `.local` name (`lan.local_hostname`).

Otherwise the answer is 403. This stops **DNS rebinding**: a page at `evil.example` that makes
its name resolve to 127.0.0.1 still sends `Host: evil.example`. An IP literal is safe to accept
because a page whose origin is that IP is the server itself.

### 2. Origin (requests that change something)

Only POST, PUT, PATCH and DELETE (`UNSAFE`) are checked. This is the **CSRF** defence.

| Request comes from | Allowed when |
|---|---|
| This computer, with `Origin` | `Origin` is `http://<Host>` or `https://<Host>`. On the connector endpoints (`/api/connector/…`, `CONNECTOR_PREFIX`) an origin starting with `chrome-extension://`, `moz-extension://` or `safari-web-extension://` is accepted too. |
| This computer, no `Origin` | `Sec-Fetch-Site` is not `cross-site` or `same-site`. Covers curl, scripts, the test client and typed URLs (`none`). |
| Another device | `Origin` is `http://<Host>` or `https://<Host>`, or there is no `Origin` and `Sec-Fetch-Site` is `same-origin`. Extension origins are refused. |

A different app on `localhost:3000` is a different origin and is refused. `Origin: null` is
refused too.

GET requests skip this check. Other websites cannot read the answers: the browser blocks
cross-origin reads, and only the connector endpoints add CORS headers, for extension origins
(see [`browser-extension.md`](browser-extension.md)). The Host check covers rebinding.

### 3. Device

Whether a request comes from **this computer** is decided by `lan.is_this_computer` from the
connection's address (`request.remote_addr`), never from a header. Loopback counts, and so do
this computer's own LAN addresses (`lan.own_addresses`, cached 30 s). The result is stored as
`g.this_computer`.

Requests from this computer need nothing more. Any other request needs the cookie of a paired
device. Without it, only `PAIRING_PATHS` are served (`/pair`, `/api/devices/pair` and the icon).
Everything else answers `not_paired()`: 401 with `{error, paired: false}` under `/api/`, and a
"This device isn't paired" page otherwise. The app's `request()` in `api.ts` reloads the page
1.5 s after such a 401, so a device that was just removed lands on that page.

### The browser extension

The extension's popup calls the server from its own `chrome-extension://` origin, from this
computer. Check 2 accepts extension origins only for the connector endpoints (`/api/connector/…`),
which is all the extension uses: `ping`, `save` and `file`. Every installed extension sends an
extension origin, so accepting them elsewhere would let any extension edit or delete the library.
Requests that change something elsewhere with an extension origin answer 403, whatever the
extension. The connector API is described in [`browser-extension.md`](browser-extension.md).

## LAN mode and pairing

### Starting

`python server.py --lan` listens on `0.0.0.0` instead of `127.0.0.1`. `config.lan` is true
whenever the host is not a loopback one, so `--host 0.0.0.0` also counts. The server prints the
`.local` name and IP addresses, and tips if `segno` or `waitress` is missing.

`server.py` refuses `--debug` together with `--lan`, or with any `--host` that is not loopback,
and exits with a message. Werkzeug's debugger is WSGI middleware that answers before the guard's
`before_request` hook, and its console runs Python code. On the network it would let anyone who
reaches the port run code on this computer.

In LAN mode the server runs on **waitress** (12 threads,
`channel_timeout=300`) rather than Flask's development server, because several devices and long
PDF downloads need a real WSGI server. Both packages are in `requirements.txt`. Without waitress
the server falls back to Flask; without segno pairing works through the link alone.

### Pairing

1. On this computer, **Settings → Devices → Add a Device** calls `POST /api/devices/pairing`. It
   answers 403 unless `g.this_computer`, and 409 unless `config.lan`.
2. `Devices.new_pairing_code` makes a code with `secrets.token_urlsafe(18)`. It is valid for
   5 minutes (`PAIRING_TTL`) and kept only in memory, so a restart voids it. Only one code exists
   at a time: a new code replaces the previous one, which stops working at once.
3. The answer has one link per address, the `.local` name first: `http://<addr>:<port>/pair#code=…`.
   The code is in the URL fragment, so it never appears in a request line or a log. Each link has
   a QR code as inline SVG (`_qr_svg`), or `qr: null` when `segno` is not installed.
4. The other device opens the link. `frontend/pair.html` is self-contained, because an unpaired
   device may load nothing else. It reads the code from the fragment, removes it from the address
   bar, suggests a name (it tells an iPad from a Mac by touch points), and posts to
   `POST /api/devices/pair`.
5. `Devices.redeem` removes the code (it works once), stores a new device and sets a cookie with
   a fresh `secrets.token_urlsafe(32)` token.
6. Settings on the computer polls `GET /api/devices` every 3 seconds while the code is shown, and
   says when a new device has appeared.

**Cancel**, and closing Settings while a code is shown, call `cancelPairing` in `devices.ts`. It
sends `DELETE /api/devices/pairing` (403 unless `g.this_computer`), and `Devices.cancel_pairing`
voids the code, so a link that was copied or photographed stops working too. A code that simply
runs out on screen needs nothing: it has expired on the server as well.

After 10 failed attempts from one address within 10 minutes (`FAILED_LIMIT`, `FAILED_WINDOW`),
`redeem` answers 429 until the window passes. The record of failures is kept in memory, and each
attempt drops every address whose failures are all older than the window, so addresses that
stop trying do not stay in memory.

### Cookies and tokens

| | |
|---|---|
| Cookie | `synapse_device` (`COOKIE`), `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` only over HTTPS |
| Lifetime | 400 days (`COOKIE_MAX_AGE`, the most browsers keep), renewed while the device is in use |
| Storage | `data/auth.db`, table `devices`: SHA-256 hashes of tokens only |

Codes and tokens are long random strings, so a plain hash is enough. `auth.db` is kept out of
`settings.json` and out of backups, so restoring a backup never brings back a removed device (see
[`storage.md`](storage.md)). `Devices.check` updates `last_seen` and `last_addr` at most once a
minute (`TOUCH_EVERY`); when it does, the guard's `after_request` hook sets the cookie again, which
keeps it from running out. `public()` is what the API shows: never the token hash.

### Settings → Devices

`GET /api/devices` answers `lan`, `this_computer`, `current` (the id of the device making the
request, if any), `devices` and `addresses`. `frontend/src/views/settings/devices.ts` shows:

- without LAN mode, how to start with `--lan`;
- on another device, that devices are added on the computer running Synapse;
- on this computer, the **Add a Device** button, then the QR code, a countdown, a copy button and
  a choice of address when there are several;
- the list of devices with **Rename…** (`PATCH /api/devices/<id>`) and **Remove…**
  (`DELETE /api/devices/<id>`). On the device itself the button reads **Sign Out**.

Only the computer running Synapse renames or removes any device. Another device may rename or
sign out only itself: for any other id both endpoints answer 403 (`_may_manage` in
`backend/api/devices.py`), before checking that the id exists. The page shows those buttons only
where they work: on another device, only on its own row.

Removing a device deletes its row, so its next request fails the device check. When a device
removes itself, the answer also deletes its cookie and the page reloads to the "not paired" page.

## Security model

What is protected:

- **Other websites** in the user's browser cannot change anything (Origin check) and cannot read
  anything (same-origin policy, plus the Host check against DNS rebinding).
- **Other devices** on the network get nothing but the pairing page until they are paired. Only
  the computer running Synapse can make a pairing code. Only the newest code works, once, for at
  most 5 minutes, and Cancel voids it.
- **Stored secrets** are hashes. A copy of `auth.db` does not give anyone a working token.
- **Pages from another device** must also pass the Origin check, so a paired device's browser
  cannot be used for CSRF either.

What is assumed:

- **Everything on this computer is trusted.** Any local process can use the whole API without a
  token. Browser extensions can change things only through the connector endpoints (add an item,
  file an existing one); like any local process they can still read the API, and an extension
  allowed to rewrite request headers could get around the Origin check.
- **The local network is trusted.** The connection is plain HTTP. Anyone who can watch the
  traffic can read the library and copy a device cookie. The README says to use `--lan` only on a
  trusted network.
- **A paired device is trusted with the library.** It has the same API as this computer, apart
  from making or cancelling pairing codes and renaming or removing other devices.
- **No proxy in front.** "This computer" is judged by the connection's address, so a reverse proxy
  on the same machine would make every request look local.
- **`--debug` is for development, on this computer only.** `server.py` refuses it with `--lan`
  or a non-loopback `--host`, because the debugger runs before the guard.

## Where to change things

| To change | Edit |
|---|---|
| A new kind of change | `ChangeFeed.since` and `kind_of` in `backend/changes.py`, `ChangeFeed` in `frontend/src/types.ts`, `apply()` in `frontend/src/changes.ts` |
| How often pages poll | `EVERY` and `MIN_GAP` in `frontend/src/changes.ts`; the interval in `frontend/src/viewer/annotations.ts` |
| When a refresh must wait | `editing()` in `frontend/src/changes.ts` |
| Messages between viewer and notes | `notesBridge.ts` and `listenForQuotes` in `notesView.ts`, together |
| Which hosts or origins are accepted | `host_allowed`, `allowed_origin` and `refusal` in `backend/access/guard.py` |
| What an unpaired device may open | `PAIRING_PATHS` in `guard.py` (and keep `frontend/pair.html` self-contained) |
| Code lifetime, rate limits, cookie lifetime | the constants at the top of `backend/access/devices.py` |
| Who may rename or remove a device | `_may_manage` in `backend/api/devices.py`, and the buttons in `refreshDevices` in `devices.ts` |
| Addresses shown for pairing | `backend/access/lan.py`, `addresses()` in `backend/api/devices.py` |
| The Devices screen | `frontend/src/views/settings/devices.ts` |

Tests: `tests/test_changes.py`, `tests/test_guard.py` and `tests/test_devices.py`. Browser
checklists: `tests/e2e/changes_checklist.py`, `tests/e2e/devices_checklist.py` and
`tests/e2e/pdf_tabs_checklist.py`. See [`testing.md`](testing.md).
