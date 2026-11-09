# Synapse

A local, web-based reference manager: a Flask backend with SQLite storage and a
TypeScript frontend. Everything runs on your machine.

## ✨ Your research, connected

- 🏠 **Local first, offline ready** — keep your library, PDFs and Markdown notes on your own machine.
- 📚 **Build your library in seconds** — add papers by DOI, arXiv ID or ISBN, drop in PDFs,
  or save articles with the browser extension.
  **Find Available PDF** gets the publisher's version when your network has access (a
  university's network or VPN) and an open-access copy otherwise.
  The extension supports localhost or a remote HTTPS server with a dedicated browser token;
  see [extension setup](browser_extension/README.md#remote-server). Its Find Missing PDFs fetches
  publishers' PDFs through your browser, with your institution's access. Searches resume across
  batches, so failed papers do not block older items. **Find Available PDF** and new-item PDF
  lookups try publisher DOI items through the requesting browser's extension, or a connected
  computer whose extension has **Handle PDF retrieval from my other devices** enabled. This lets
  a phone use that computer's campus/VPN access. A receiver must accept within five seconds;
  requests never wait for a later reconnect. Otherwise retrieval uses the server's network.
- 🔎 **Find the passage you remember** — search metadata and PDF full text, then organise
  your collection with folders, coloured tags and smart folders.
- 🖍️ **Read, highlight, capture** — annotate PDFs with highlights, drawings and comments,
  and send quotes or snapshots straight into your notes.
- 📝 **Write notes that connect** — live Markdown preview, LaTeX maths, diagrams, images,
  paper citations and links between notes. Export Formats adds LaTeX projects with figures and
  a BibTeX bibliography; optionally include linked notes as a multifile project.
- 🕸️ **See the bigger picture** — explore a graph of your notes, papers and citation links.
- ✅ **Turn ideas into action** — track tasks, due dates and subtasks right alongside your notes.
- 🤖 **Bring your AI tools** — connect Claude, Codex and other AI apps through MCP;
  optionally complete notes with a local Ollama model.
- 🔄 **Move references freely** — import and export BibTeX, RIS, EndNote and CSL JSON,
  with customisable citation keys. Keys are unique across the library and Trash (ignoring case)
  and use only letters, digits and `_ - : . / +`; an edit that breaks this is refused, and an
  import gives such entries a usable key and tells you which ones changed.
- 🧩 **Make it yours** — extend Synapse with plugins, including an arXiv feed that ranks
  each morning's papers by how well they match your library and highlights authors you follow and
  papers that cite you, and choose light or dark mode.
- 📱 **Take research around the house** — a paired phone gets an interface of its own (tabs at
  the bottom, papers and notes to read and write, your plugins' lists one tap away); tablets use
  the full interface with touch.
- 🛟 **Keep a way back** — automatic library and notes backups, note version history,
  and Trash with Undo.

Explore the [full feature guide](docs/features.md), or [get started](#run-it).

## Run it

You need Python 3.10+ and [Node.js](https://nodejs.org) 20+ (Node only builds the web app's
scripts; the app itself runs offline).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --require-hashes --only-binary=:all: -r requirements.txt
(cd frontend && npm ci)           # once, and again when frontend/package-lock.json changes
python server.py                  # http://127.0.0.1:8000 opens in your browser
python server.py --port 5050 --no-browser
python server.py --data-dir ~/MyLibrary
python server.py --window         # in a window of its own, like a program
python server.py --make-shortcut  # an icon on the Desktop that does that (see below)
```

The requirements files pin direct and transitive Python dependencies, with hashes and platform
markers. CI installs the same runtime pins. To change dependencies, edit the `.in` inputs and
regenerate the locks; see [Python dependencies](docs/dependencies.md).

`server.py` compiles the web app's scripts (`frontend/src` → `frontend/js`) whenever they changed,
for example after a `git pull`.

The server only answers to `127.0.0.1` / `localhost`, and only the app's own pages and the
browser button can change the library: other websites open in your browser cannot.

### As a program of its own

`python server.py --window` opens Synapse in a window of its own instead of a tab of your
browser: Synapse installed as an app by Chrome or Edge if you did that (it then has the Synapse
icon in the Dock: open `http://127.0.0.1:8000` in Chrome, **⋮ → Cast, Save and Share → Install
Page as App…**), else a Chrome/Edge/Brave window without tabs or address bar. Right-click the
installed app's icon in the Dock for **New Window**.

`python server.py --make-shortcut` (macOS and Linux) puts a **Synapse** icon on the Desktop and,
on a Mac, in `~/Applications` (Spotlight, Launchpad, the Dock). Double-clicking it starts the
server in the background and opens the window; closing the window stops the server. Options given
with it (`--data-dir`, `--port`…) are used by the shortcut. Make it again after moving the folder.
Details: [docs/desktop.md](docs/desktop.md).

Notes and PDFs open in tabs inside Synapse (in a browser tab too), after the first tab, which shows
the library or Tasks. Notes open as in VS Code: in a preview tab (italic title) that the next note
replaces, until you double-click the tab, double-click the note in the notes list, or edit it. Each
note tab is its own editor, with its own mode (Live, Source, Read), place and undo history, so
going back to it is instant; when several notes are selected quickly, the last click always wins.
PDFs get a tab each. Ctrl+Tab switches, ⌃W (Alt+W elsewhere) or a
middle click closes, ⌃⇧T reopens. Each window has its own tabs, which a reload brings back; a new
window, or the app started again, opens on the library alone. Drag a tab onto another Synapse
window to move it there, or outside every window for a new one (or right-click it: **Move to New
Window**); right-click items, PDFs, folders, notes and tasks for **Open in New Window**. Settings →
PDF Viewer → Open PDFs in → Browser Tabs keeps PDFs in browser tabs of their own.

Notes edited in another program are detected every 30 seconds while Synapse is running.
Lists and backlinks refresh automatically; an open note reloads if clean, or offers the
conflict dialog while preserving unsaved text. This also works without Git history. A note
renamed or moved in another window or on another device follows to its new name.

Give a notes folder a colour (right-click it in the notes list → Colour): its notes show it on
their tab and in the app window's title bar, which is otherwise neutral like the tab bar.

### From an iPad or phone

```bash
python server.py --lan            # also reachable on your network (paired devices only)
```

1. On the computer, open **Settings → Devices → Add a Device**. It shows a QR code.
2. Scan it with the iPad's camera (or open the link it shows) and tap **Pair This Device**.

The iPad stays paired until you remove it in Settings → Devices on the computer (or sign it
out on the iPad itself; a paired device removes others only after confirming with a passkey). Without pairing, another
device sees only a "pair this device" page — the library, PDFs and notes stay closed. The
computer running Synapse never needs pairing. Both devices must be on the same network, and the
computer must be awake (System Settings → Energy, or `caffeinate -s python server.py --lan`).
If the `.local` address in the QR code doesn't open, pick the IP address instead.

On a phone Synapse has a layout of its own: Library, Notes, a **+** button to add a paper, a note
or a task, the plugin list you use most (long-press its tab to choose another) and **Plugins** for
the rest. Notes open for reading; **Edit** brings a bar of helpers above the keyboard (cite, link,
math, images). A paper's ⋯ button has everything its right-click menu has on the computer. To use
the full layout on a phone instead, choose **Use the Desktop Layout** from the settings button
(back again with **Use the Phone Layout** in the More menu). See [the phone layout](docs/phone.md).

`--debug` only works without `--lan`: Flask's debugger must never be reachable from the network.

Synapse's Content Security Policy blocks inline scripts, JavaScript eval, scripts from other
origins, embedded objects and changes to the document's base URL. It also prevents other sites
from framing signed-in pages. Local modules, the embedded PDF viewer and its WebAssembly
decoders remain supported. See [the guard](docs/sync-and-devices.md#the-guard).

The connection is plain HTTP, so use it on a network you trust (your home Wi-Fi), or serve it
over HTTPS (below). Paired devices are kept in `data/auth.db` (hashed), which backups leave out on
purpose.

#### Signing in with a passkey

Pairing needs the computer at hand. With a passkey, a device signs itself in: on the "This device
isn't paired" page, tap **Sign In with a Passkey**. Synapse shows when it is waiting for your
browser or password manager, including when adding a passkey or confirming an administrative action.
A passkey saved in iCloud Keychain or Google Password Manager reaches your other devices by itself; a security key or a phone nearby works too.

Browsers only use passkeys on HTTPS pages opened by a name (not an IP address), so Synapse needs a
certificate your devices trust. [mkcert](https://github.com/FiloSottile/mkcert) makes one, offline:

```bash
mkcert -install                                   # once: a local certificate authority on this computer
mkcert Lorenzos-MacBook.local                     # your computer's .local name (Synapse prints it)
python server.py --lan --cert Lorenzos-MacBook.local.pem --key Lorenzos-MacBook.local-key.pem
```

With `--lan` and HTTPS, Synapse opens its browser tab or `--window` at that computer's `.local`
name rather than at an IP address, so the address matches the certificate. The certificate must
name the `.local` address Synapse prints; if the Mac's sharing name changes, make a new certificate
for the new name.

Then install mkcert's authority on each device: `mkcert -CAROOT` shows the folder; send
`rootCA.pem` to the iPad (AirDrop or mail), install the profile in Settings → General → VPN &
Device Management, and turn it on in Settings → General → About → Certificate Trust Settings. On
Android: Settings → Security → Encryption & credentials → Install a certificate → CA certificate.

Open `https://Lorenzos-MacBook.local:8000/` (on the computer too), go to **Settings → Devices →
Add a Passkey…** and save it. A device just paired can add one too, for a few minutes after it pairs
(afterwards, add one after confirming with an existing passkey, or pair the device again). Passkeys
work only at the address they were made on. Remove
one in Settings → Devices on the computer, or on a device signed in over HTTPS after confirming with
a passkey; devices it signed in stay until you remove them too. Passkeys need the `cryptography` package (in
`requirements.txt`).

#### Installing on Android

Open the trusted HTTPS `.local` address above in Chrome, then choose **⋮ → Add to Home screen →
Install app**. Synapse opens in its own window and uses its red Synapse icon. Chrome deliberately
does not offer an app installation from a plain `http://` LAN address or from HTTPS whose
certificate the phone does not trust; in that case it only offers a basic browser shortcut. If an
old iconless shortcut already exists, remove it and add or install Synapse again after opening the
HTTPS address.

### From anywhere (a tunnel)

Synapse can also run on another computer, such as a Linux server, and be used from your devices
anywhere, with no app or VPN on them, through an HTTPS tunnel such as Cloudflare Tunnel:

```bash
python server.py --no-browser --public-url https://synapse.example.com
python server.py --pairing-link   # on that computer (over SSH): a one-time link + QR code for your phone
```

`--public-url` requires a loopback host (`127.0.0.1`, `localhost` or `::1`); combining it with
`--lan` or a non-loopback `--host` is refused so direct network access cannot bypass the tunnel.
Every request through the tunnel needs a signed-in device, whatever address it comes from. Pair
your phone with the link, add a passkey there, and sign your other devices in with it. Pairing more
devices, making AI access tokens, removing devices or passkeys, changing plugins and changing the
backup settings, and installing an update, work from a signed-in device after confirming with the passkey (Face ID / Touch ID). The full setup, with a systemd service and the
Cloudflare steps: [docs/remote-access.md](docs/remote-access.md).

Or run it all in Docker: Synapse, the tunnel and, for autocompletion, Ollama (on an NVIDIA GPU if
there is one), restarted by Docker after a reboot and reached only through the tunnel:

```bash
cp .env.example .env              # the public address, the tunnel's token, the data folder
docker compose up -d --build
docker compose exec synapse python server.py --pairing-link
```

Settings → Backups offers **This computer** or **This computer + remote (rclone)** storage. Configure the provider,
credentials and destination entirely in Settings; the Docker image includes rclone. Supported
providers include Google Drive, OneDrive, Dropbox, S3-compatible storage, WebDAV, SFTP and FTP/FTPS.
FTP offers explicit TLS, implicit TLS and plain FTP. Password connections also accept an existing
`rclone obscure` value by selecting **Already obscured by rclone** beside the password.
FTP/FTPS, WebDAV and S3 connections can upload a trusted PEM certificate or bundle (for example,
`ca-certificates.crt`) directly in Settings, including for servers using a private certificate authority.
Test connection checks upload, download and deletion, including on FTP servers that hide dotfiles.
Cloud accounts use browser sign-in with Web application credentials entered in Settings.
Remote backups retain local safety copies and retry failed uploads; remote archives can be
restored from the same page, including on a fresh installation. PDFs are still excluded.
See [backup setup and recovery](docs/storage.md#remote-storage-with-rclone).

See [docs/docker.md](docs/docker.md). Settings → Updates shows loading feedback while checking,
preparing a backup, installing and reconnecting after a restart. Built from GitHub, it shows newer
commits, and with the updater container **Update Now** installs them: a backup, a rebuild and a restart, with the
previous version kept to fall back on ([docs/updates.md](docs/updates.md)).

Your library lives in `data/` (or `--data-dir` / `SYNAPSE_DATA_DIR`):

| File | What |
| --- | --- |
| `library.db` | items, attachments, annotations |
| `fulltext.db` | extracted PDF text (FTS5); safe to delete, it is rebuilt |
| `pdfs/` | attached files |
| `notes/` | your Markdown notes (the files are the source of truth) |
| `notes_index.db` | links and search index of the notes; safe to delete, it is rebuilt |
| `citations.db` | reference lists fetched online (the citation graph); if deleted they can be fetched again |
| `backups/` | automatic backups of the library, settings and notes (`library-*.tar.gz`, see below), and note backups taken before a citation-key rename (`notes-*.tar.gz`); both go to the backup folder chosen in Settings instead, if there is one |
| `settings.json` | preferences from the Settings page |
| `auth.db` | devices paired with `--lan` or signed in with a passkey, the passkeys, and AI apps' access tokens; not in backups |

Back up the `data/` folder to keep everything. It is git-ignored: code and data
never mix.

## Features and guides

Synapse combines a reference library, linked Markdown notes, PDF annotations and optional
plugins. The [feature guide](docs/features.md) covers daily workflows, the command palette,
sidebar and bundled plugins. Read the [Notes guide](docs/notes.md) for the note format,
[Plugins](docs/plugins.md) for installation and permissions, and the
[authoring guide](docs/plugin-authoring.md) to build a plugin.

The [architecture map](docs/architecture.md) links each subsystem and explains where code goes.

## AI access (MCP)

`mcp_server.py` lets AI apps — Claude Desktop, Claude Code, Codex, or any other app that speaks MCP
(Model Context Protocol) — read your library: ask a question about a project note and the AI can
read the note, the papers it cites,
the relevant pages of their PDFs and their references. It reads the same `data/` folder as the app
(which does not need to be running); from another computer, see below. When you ask it to save an
answer it adds a new note in `notes/ai/`; when you ask it to change a note, the edit is checked
against the version on disk and recorded, so the app can show it and undo it. Figures it cuts out of
a PDF go to `notes/attachments/`. It never changes the library's items.

Set it up once, from the repository root with the virtual environment active:

```bash
python mcp_server.py --config     # prints the exact entries for your paths
```

- **Claude Desktop:** Settings → Developer → Edit Config, merge the printed `mcpServers` entry into
  `claude_desktop_config.json`, and restart Claude. "synapse" then appears in the tools menu of a chat.
- **Claude Code:** run the printed `claude mcp add …` line.
- **Codex** (CLI, IDE extension, app): run the printed `codex mcp add …` line, or add the printed
  `[mcp_servers.synapse]` entry to `~/.codex/config.toml`.
- **Other apps:** run the same command as a stdio MCP server.

Whatever an AI app changes is shown with its name: "Codex edited this note", "Claude changed this
task", and in each note's history, all with Undo.

**From another computer.** An AI app reaches the library through the running app instead.
Use `python server.py --lan` for another computer on the same network, or
`python server.py --public-url https://synapse.example.com` behind an HTTPS tunnel
([remote setup](docs/remote-access.md)). These are alternatives: do not combine `--lan` and
`--public-url`. Settings → AI Access shows both setup options when access is local-only;
when configured, it shows the advertised MCP address, using the public URL for a tunnel.
On the computer running Synapse, open
**Settings → AI Access → New Token…**, name it, pick the app (Claude Code, Codex or another) and
run what it shows on the other computer. For Claude Code:

```bash
claude mcp add --scope user --transport http synapse http://Lorenzos-MacBook.local:8000/mcp \
  --header "Authorization: Bearer syn_…"
```

For Codex, keep the token in an environment variable (put the `export` line in your shell profile):

```bash
export SYNAPSE_TOKEN=syn_…
codex mcp add synapse --url http://Lorenzos-MacBook.local:8000/mcp --bearer-token-env-var SYNAPSE_TOKEN
```

The token is shown once; Settings lists when each token was last used, and **Remove…** locks its
app out at once. The tools are the same. Over plain HTTP the token crosses the network unencrypted,
like everything else in `--lan`; with `--cert` the address starts with `https://` (and the AI app
must trust the certificate, for Claude Code: `NODE_EXTRA_CA_CERTS="$(mkcert -CAROOT)/rootCA.pem"`).

Add `--data DIR` if your library is not in `./data`. Tools:

| Tool | What it gives |
| --- | --- |
| `search_library` | items and notes matching a query (same syntax as the search box: `folder:`, `tag:`, `year:`…), with snippets |
| `list_collections` | folders, tags, smart folders and notes folders with counts |
| `get_note` | a note's text, the papers it cites (with line and page), its links and backlinks, the images it shows |
| `view_image` | the images of a note (pasted pictures, snapshots — with the page and region they came from), or one by its path; large ones are scaled down when Pillow is installed |
| `get_paper` | metadata, abstract, PDFs and page counts, your paper note, notes citing it, reference-list summary |
| `search_in_paper` | the pages of a PDF containing some words, with snippets |
| `read_paper` | the text of some PDF pages (up to 10 per call), marked by page |
| `view_page` | an image of a page with a 0–1 grid; figures, tables and numbered equations with proposed regions |
| `snapshot` | cuts a figure, table or equation — named by its number (`figure="Figure 2"`, `equation="3"`, page found by itself) or as a region — into `notes/attachments/` like the viewer's camera button, and returns the Markdown for it |
| `get_references` | what a paper cites (library items marked, others with DOI/arXiv) and which library items cite it |
| `get_citation_graph` | the neighbourhood of some papers, or a library-wide overview of the most cited works you do not have |
| `write_note` | saves a new note in `notes/ai/` (`author: ai`), never overwriting anything — after checking it |
| `edit_note` | changes an existing note (replace passages, append, or new text) — checked the same way; a version of its own in the note's history |
| `undo_note_edit` | takes the last AI edit back out of a note, keeping what you changed since (unless you changed the same lines) |
| `list_tasks`, `get_task` | your tasks (open ones unless asked), filtered like the Tasks page; one task with its details, sub-tasks and the notes that show it |
| `create_task`, `update_task`, `delete_task` | make, change (title, details, open/done, due date, parent) or delete tasks, and put a new task's line in a note — marked as the AI app's (with its name), undoable in the app |

Page numbers are PDF pages counted from 1, so the AI can cite `[@key, p. N]` and the citation opens
the right page in the app. The AI app is told the notes syntax (KaTeX math, quotes as `> words [@key, p. N]`,
snapshots, callouts…), and `write_note` refuses a note — saving nothing — when a `[[link]]` or `#heading`
does not resolve, a citation key or page does not exist, a quote's words are not on the cited page, an
image is missing, or formulas are written in Unicode instead of `$…$`; the AI then corrects it. A link
written with a note's title is fixed to its file name. `edit_note` is checked the same way, but only
problems the change adds block it. Every change an AI app makes to a note is a version of its own
in the [notes history](docs/notes-history.md): an open note takes the change as one step, so ⌘Z (Ctrl+Z)
undoes it; a bar over the note says which app changed what ("Codex edited this note"), with Show
changes and Undo; and the note's history lists each change with the app's name, its diff and a
way back. The diff highlights changed words and wraps long paragraphs to the dialog's width.
Equations are found by the number in
parentheses at the end of their line, and the snapshot covers all their lines (never half an
equation). `view_page` and `snapshot` need `pypdfium2`
(in `requirements.txt`). Note text and PDF passages the AI reads are sent to the AI app's provider
(Anthropic for Claude, OpenAI for Codex …) as part of the conversation, as if you had pasted them.

## Project layout

For how the code fits together and where new code goes, see
[`docs/architecture.md`](docs/architecture.md) (and [`CLAUDE.md`](CLAUDE.md) for the working rules).

```
./
├── server.py              starts the web app
├── mcp_server.py          MCP server for AI apps: Claude, Codex … (stdio)
├── backend/               Flask + SQLite backend (packages described in docs/architecture.md)
├── frontend/              the web app: index.html, viewer.html, src/ (TypeScript; built to js/), css/, vendor/ (third-party libraries)
├── browser_extension/     Chrome extension (Manifest V3) – load unpacked
├── tools/                 build scripts for the vendored libraries and the macOS icon
├── tests/                 pytest suite, js/ unit tests, e2e/ browser checklists
├── plugins/               plugins that come with Synapse, off until turned on (Citation Counts, arXiv Today)
├── docs/                  technical documentation
├── Dockerfile, compose*.yaml, .env.example   Synapse, its tunnel and Ollama in Docker (docs/docker.md)
└── data/                  your library (git-ignored)
```

Notes are plain Markdown files in `data/notes/` (paper notes in `data/notes/papers/`,
images in `data/notes/attachments/`, deleted notes in `data/notes/.trash/`, images no note uses any
more in `data/notes/.trash-attachments/`). The files are
the source of truth; `data/notes_index.db` is derived from them and can be deleted at any
time — it is rebuilt on the next start.

The frontend never talks to external services directly; all lookups go through
the backend.

## Tests

Set up the development environment once using [Development](docs/development.md#setup).
While editing, run affected tests; after the final code change, verify the installed tools with:

```bash
python tools/check.py
```

The command checks lint, formatting and types, builds the frontend and runs the Python/JavaScript
unit suite, without installing dependencies or fixing source files. Browser checks are separate.
CI (`.github/workflows/ci.yml`) runs the same checks on every pull request, plus lock/runtime checks.
CI limits each test job to twenty minutes, cancels obsolete runs of the same workflow/ref, and
reports slow tests and stalled threads. Unit/API tests fake LAN discovery and window launches
and release plugin resources between tests; see [Testing](docs/testing.md).

Browser checklists run automatically for relevant PR changes, nightly and on demand through
[Browser checks](.github/workflows/browser-checks.yml). PRs select affected checklists, with full
coverage for unknown/shared application changes; nightly/manual runs stay full.
Chromium runs every checklist; WebKit covers Notes, portable iPad/iPhone touch flows and passkey
UI/error paths. Chromium retains raw gesture and virtual-authenticator coverage. Runs retain
complete logs, available screenshots and a summary; see [Testing](docs/testing.md#browser-checklists).

See [Security checks](docs/security-checks.md) for secret/dependency scans and disposable container
checks. Coding assistants share [named roles](.agents/README.md), maintained once for Codex and
Claude. Triage is manually invoked and can file deduplicated GitHub issues; it is not scheduled.

Browser checklists also run locally (need Playwright; each uses a throw-away data folder).
`python tests/e2e/run.py` runs them all and prints a summary, `python tests/e2e/run.py notes render`
only those, and `--retries 1` gives a failed one a second try. Each checklist reports its
passed/total count and exits nonzero on failure; the runner rejects a missing summary.
`python tests/e2e/run.py task_indexes` checks the Tasks note index and reports `4/4` when healthy.
Or run one directly:

```bash
pip install playwright requests && playwright install chromium
python tests/e2e/operation_feedback_checklist.py # pending passkeys and updates, with safe service stubs
python tests/e2e/notes_checklist.py      # editor, saving, conflicts, files
python tests/e2e/render_checklist.py     # live preview and read mode (tests/e2e/kitchen-sink.md)
python tests/e2e/links_checklist.py      # autocomplete, citation cards, side panels, item pane
python tests/e2e/library_checklist.py    # key rename, notes in search, quote/snapshot from the PDF viewer
python tests/e2e/citation_spot_checklist.py  # a quote's / snapshot's citation opens the PDF at that spot and flashes it
python tests/e2e/usability_checklist.py  # multi-select, Delete + Undo, folders, Back/Forward, item-list markers
python tests/e2e/viewer_links_checklist.py  # PDF links: jump, hover preview, Back/Forward in the viewer
python tests/e2e/tabs_checklist.py       # PDFs in the app's own tabs; a --quit-when-closed server stops with its window
python tests/e2e/export_checklist.py     # Export: HTML (self-contained, offline), Markdown, PDF via the print dialog
python tests/e2e/extensions_checklist.py # provider/export lifecycle, spoilers in Live/Read, static export
python tests/e2e/navigation_checklist.py # workspace switches, sidebar selection, history, shortcuts and disabled plugins
python tests/e2e/graph_checklist.py      # Graph: local graph, full graph, levels, filters, paper card, Show in Graph, Back/Forward
python tests/e2e/attachments_checklist.py  # note attachments: unused ones to the trash, Attachments dialog, retention
python tests/e2e/adding_checklist.py     # bulk Retrieve Metadata (parallel, grouped, background), Settings → Adding Items
python tests/e2e/claude_edits_checklist.py  # Claude's note edits: appear live, ⌘Z / bar Undo / Show changes, the note's history
python tests/e2e/notes_history_checklist.py # notes history: versions grouped, diffs, restore, outside changes, milestones
python tests/e2e/equations_checklist.py  # equation numbers, \eqref, macros, clicks into formulas, panels and width
python tests/e2e/extension_checklist.py  # the browser button: options, reading an article page, saving it
python tests/e2e/find_pdfs_checklist.py  # the extension's Find Missing PDFs against a fake publisher
```

The notes editor (CodeMirror 6) is vendored as a pre-built bundle in `frontend/vendor/cm6/`;
to rebuild it from the pinned versions: `cd tools/cm6 && npm ci && ./build.sh`. The renderer
(markdown-it, KaTeX, Mermaid) likewise lives in `frontend/vendor/md/` and `frontend/vendor/mermaid/`
(`tools/md`), and the graph library (force-graph) in `frontend/vendor/force-graph/` (`tools/graph`).

## License

Synapse is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version. It is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full text.

The libraries vendored under `frontend/vendor/` keep their own licences (Apache-2.0, MIT, BSD, ISC
and others), listed with their versions in [`frontend/vendor/README.md`](frontend/vendor/README.md)
and in the `LICENSE*` files next to them.
