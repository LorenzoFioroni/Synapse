# Companion

The companion is a small program on your own computer that connects a Synapse server, often a
remote one behind a tunnel, to what only your computer has. It runs in the background, starts when
you log in, and has a window for its settings and state. Two jobs:

1. **Ask AI from inside Synapse.** A question in a note or about a selection in a PDF goes to the
   companion, which runs your own AI command-line tool (Claude Code; Codex if you turn it on)
   restricted to Synapse's tools. The answer streams back into the app. The server holds no API
   key and never runs the tool.
2. **The browser extension's way to the library.** The extension talks only to the companion
   (Chrome native messaging), which passes its requests to the server with the companion's token.
   The browser holds no token and never reaches the server itself. The companion also installs
   the extension's folder and keeps it current.

For the extension itself see [browser extension](browser-extension.md); for the app side of Ask AI
see [frontend](frontend.md).

| Piece | Where |
|---|---|
| The program: package `synapse_companion/`, Python standard library only (the tray icon optionally uses PySide6), served as one runnable `synapse_companion.pyz` | `synapse_companion/` |
| Command line; files, profile and settings; requests to the server | `cli.py`; `common.py`; `remote.py` |
| The AI tools and their output | `providers.py` |
| The poll loop and one thread per question | `runner.py` |
| Background service: connection, settings, extension, updates, the window's state | `service.py` |
| The window (127.0.0.1 page and its API) | `ui.py`, `static/` |
| Extension folder and native messaging registration; the native host | `extension.py`; `native.py` |
| Start at login; the tray icon | `autostart.py`; `tray.py` |
| The in-memory rendezvous (`Companions`), one per `Services`; per-question tokens | `backend/companion/hub.py` |
| The question the AI tool receives | `backend/companion/prompt.py` |
| The downloads: extension zip and `.pyz`, with build hashes | `backend/companion/bundle.py` |
| Endpoints (thin) | `backend/api/companion.py` |
| The token rules | `backend/access/guard.py`, `backend/access/tokens.py` |
| Tests | `tests/test_companion_hub.py`, `tests/test_companion_api.py`, `tests/test_companion_cli.py`, `tests/test_companion_app.py`; browser: `tests/e2e/extension_checklist.py` |

## Setting up

On the computer running Synapse, **Settings → AI Access → New Companion Token…** makes a token
(`syn_companion_…`, shown once) and offers **Download synapse_companion.pyz** (`GET
/api/ai/companion-script`, normal device auth). On your own computer, with Python 3.10+ and Claude
Code installed and signed in:

```bash
python3 synapse_companion.pyz
```

The companion starts in the background and opens its window, an app window of Chrome, Chromium,
Brave or Edge when one is installed (else the default browser). There you enter the server's
address and the token, then:

- **Ask AI**: which tools Synapse may use, and which of their models and efforts it may choose;
  how many questions at once (1 or 2). Codex is off until you turn it on (see below).
- **Browser extension → Install** downloads the extension folder, registers the companion with
  the browsers, and shows the one-time **Load unpacked** steps. Its state says whether a browser
  knows the companion, when the extension last said hello and whether it runs the current files.
- **Start Synapse Companion when I log in.**

**The tray icon** (the menu bar on macOS) shows the connection and how many questions are being
answered, and offers **Open Synapse Companion**, **Open Synapse in the Browser** and **Quit**; a
click opens the window. It needs PySide6 in the Python that runs the companion, the one optional
package outside the standard library: `python3 -m pip install --user PySide6-Essentials` (without
`--user` in a virtual environment; the window shows the exact command), then quit and start the
companion again. GNOME shows tray icons only with the AppIndicator extension (Ubuntu has it on).

Running the program again (or the applications-menu entry on Linux) opens the window of the
companion already running. `python3 synapse_companion.pyz run` runs it in the foreground instead
(what starting at login does); `connect URL` and `status` work without a window.

**Files.** Everything lives in the user config directory (`~/.config/synapse-companion/`,
`~/Library/Application Support/…`, `%APPDATA%\…`; `SYNAPSE_COMPANION_HOME` overrides it):
`<profile>.json` with `{server, token, client, name, extension, settings}` (mode 0600), the
window's `<profile>.window.json` (port and key, 0600), a log `<profile>.log` (cut at 1 MB), the
extension folder `<profile>-extension/`, the native host launcher `native-host` and the AI tool's
empty working folder `work-<profile>/`. A server on another computer must be `https://`; plain
`http://` is accepted for loopback addresses only. `--profile NAME` keeps connections to several
servers apart (one at a time serves the browser extension: the last one installed).

**Starting at login**, per user, no administrator rights: an XDG autostart entry
(`~/.config/autostart/synapse-companion.desktop`) and an applications-menu entry on Linux, a
LaunchAgent (`~/Library/LaunchAgents/org.synapse.companion.plist`) on macOS, a `Run` value under
`HKEY_CURRENT_USER` started with `pythonw` on Windows. Each runs `run --profile NAME` with the
Python and the program path in use when it was turned on, so move the `.pyz` before, not after.

**What the companion token is worth.** It opens no `/mcp`, but whoever holds it can save to the
library through the connector and can poll as a companion, receiving questions (and their
per-question `/mcp` tokens) meant for your computer. Keep it as private as an MCP token; remove it
in Settings when a computer is gone, which also ends its running questions' tokens.

**Updates.** The server's hello gives the `.pyz` build (the SHA-256 of the file it serves, built
reproducibly from `synapse_companion/`); the window offers **Update and restart** when it differs
from the program's own file. Only on that click, and never for a repository checkout. Like the
extension's code, the program comes from the server you connected to: an update runs whatever that
server (or anything that can answer for its HTTPS address) serves, so connect only to a server you
control.

## How a question travels

```
app ──POST /api/ai/ask──► Companions ◄──POST /api/companion/poll (long poll ≤ 20 s)── companion
app ◄─GET /api/ai/jobs/ID?after=N (long poll ≤ 20 s)─ events ◄─POST …/jobs/ID/events── companion ── claude ──POST /mcp──► Synapse
```

Plain HTTPS requests and long polls: no WebSocket or event stream, so it works behind Cloudflare
Tunnel with Werkzeug's threaded server, like the live PDF handoff (`online/browser_pdfs.py`). No
new port. The rendezvous is in memory in `Services.companions`: a restart ends open questions, and
independent instances share nothing. What must survive a restart, the conversations, is in `ai.db`
(see [Conversations](#conversations)).

**Hub rules** (`hub.py`):

- A companion is *connected* while polling or for 30 s after its last poll; at most 16.
  `GET /api/ai/status` lists them with `busy` counts.
- `ask` picks the least busy connected companion that offers the provider (default `claude`) and
  queues the job for it alone; a companion is given at most two running jobs.
- A job nobody picks up in 15 s ends with an `error` event: "The companion is busy with other
  questions" when its companion is connected and already at its two jobs, else "No companion picked up the
  question". A running job whose companion sends no poll and no events for 60 s ends with an error too,
  and no job lives longer than 15 minutes, however its companion behaves. Cancel, expiry and size limits all end a job the same way: the companion is told
  to stop it, by `cancelled` in its poll answer or in the answer to its next event post.
- A conversation is pinned to its companion and provider, which the store keeps and `ask` passes to the
  hub with each follow-up (`resume: true`). One turn at a time per conversation (409). A follow-up
  whose companion is not connected answers 409 naming the computer ("… is on <name>, which is not
  connected"); one whose companion no longer offers the provider answers 409 too.
- Event posts: at most 64 events and 256 KB per request, 2 MB of text and 20,000 events per job;
  unknown event types are dropped; consecutive text events merge. Finished jobs stay readable for 10
  minutes (at most 100), then 404.
- A companion ID (`client`) is bound to the token that first used it: a poll or event post from another
  token is refused (409), and one token may have at most 4 companions (of 16).
- Request bodies on `/api/companion/*` and `/api/ai/*` are limited to 512 KB (413 beyond).
- Each hand-out carries a fresh `mcp_token` for that question's AI tool (only its SHA-256 is kept).
  `/mcp` accepts it while the job is `running`, restricted like below, attributed to the
  companion's token; it stops working when the job ends in any way.
- Posting events touches neither the library nor the change feed.

**Events** from the companion: `{type:"started"}` (its AI tool is running, sent once at spawn),
`{type:"text", text}` (a delta), `{type:"tool", name}` (a tool call began), `{type:"usage",
input_tokens, output_tokens, cost_usd?, duration_ms?}` (once, before `done`; numbers only, bounded, anything
else dropped), `{type:"done"}`, `{type:"error", message}`. Claude's `input_tokens` counts everything sent
to the model, including what came from its cache; Codex reports no cost.

**Models and efforts.** A companion's providers carry `models: [{id, label}]` and `efforts: [id]`, built
in per provider (`GET /api/ai/status` shows them; empty means no selector). `ask` takes optional `model`
and `effort` and answers 400 for a value the chosen companion's provider did not advertise; absent means
the tool's default. The ids match `[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,63}` (never a flag-like start). The
companion looks the value up in its own built-in list again and ignores anything else, so no
server-chosen string reaches a command line unchecked. The window's settings narrow what it
advertises and accepts: a tool turned off is not offered, and only the models and efforts left
ticked are (all ticked is stored as "all", so ones a later version adds are included).

| Provider | Models | Efforts | Checked |
|---|---|---|---|
| Claude Code | `fable`, `opus`, `sonnet`, `haiku` | `low`, `medium`, `high`, `xhigh`, `max` | Each alias started a session on the matching model (2.1.325); the CLI warns about and ignores an unknown effort, and fails on an unknown model |
| Codex | none (account-specific; Codex keeps the user's default) | `low`, `medium`, `high`, `xhigh` | `low` ran; the API's own error names `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |

## Conversations

`ai.db` (`backend/companion/store.py`, path `Config.ai_db_path`, in backups, see [storage](storage.md))
holds each conversation: title, provider, model and effort of its last question, the computer it is pinned
to (companion id and name), a short context of its last question (where it came from; a selection or quote
kept to 300 characters; one context as an object, several as a list) and its messages, each question with
its own contexts (a list, in the same short form). The server records the user's question when `ask` accepts it (before the
job can be taken) and the answer when the job ends (`Companions.on_finish`): its text, tool names, usage and
final state, also when it ends in error or is cancelled, with what arrived. The newest 200 conversations
are kept, each with at most 400 messages.

| Endpoint | |
|---|---|
| `GET /api/ai/conversations?limit=50&q=` | Newest first: `{id, title, created, updated, provider, model, effort, companion: {id, name}, context, available, running}`. `q` searches titles and messages. `available`: a connected companion with that client id offers the provider. `running`: the job still answering, or null. |
| `GET /api/ai/conversations/<id>` | The same fields plus `messages: [{role, text, tools, usage?, state?, error?, context?, created}]` (`context`: the question's list). |
| `PATCH /api/ai/conversations/<id>` `{title}` / `DELETE …` | Rename (at most 80 characters) / delete. |
| `POST /api/ai/ask` with `conversation` | Continues a conversation, after a server restart too; the provider stays, model and effort may change. |

The companion derives Claude's session id from the server URL and the conversation id, so a restarted
companion on the same computer resumes the same session. When the tool cannot resume (its local session is
gone: Claude answers "No conversation found"; a restarted Codex has lost its thread), the job ends with an
`error` event, "This conversation's history is no longer on that computer. Start a new one."

## What the AI tool receives

`prompt.build` (not the API handler) writes the question: the user's words, then what they added to it
(`context`: one object or a list of at most `MAX_CONTEXTS`, 8; for each, the note's path and title, or
the paper's citation key, title, PDF page, attachment id when the item has several PDFs) between `<context>` tags, and the selected text or quote between `<selection>` /
`<quote>` tags: all data the model must not obey (a tag cannot be closed from inside, titles are one
line). Papers and notes the user names in the question (`[@key]`, `[@key, p. N]`, `[[note]]`, typed
with the composer's completion) are found with the notes parser and resolved as notes resolve them,
then listed between `<mentions>` tags: each paper's key, title, first author, year and cited pages, each
note's title and path, or "not in the library" / "not found"; at most `MAX_MENTIONS` (20) of each. The
library is read only when the question names a paper. The system text asks for the Synapse tools,
note Markdown with `[@key, p. N]` citations, brevity and changes to notes only when asked.
Selections are limited to 8,000 characters, the question to 20,000; an unknown note or item is a 404.

**PDFs in the notes folders** ([notes](notes.md#pdfs-in-the-notes-folders)) can be cited too. A
context `{kind: "notepdf", path, page?, quote?}` is the PDF the viewer shows (its page, the words
selected); the path is never used as sent: it goes through `Vault.clean_rel(suffix=".pdf")` and must
be a PDF of the registry (`NotePdfs.by_path`), else 404. The prompt names its path as the `paper` for
`read_paper`, `search_in_paper`, `view_page` and `get_paper_outline`, and asks for answers to cite it as
`[[folder/file.pdf#p=N]]` (quotes as `> words [[folder/file.pdf#p=N]]`). A `[[file.pdf#p=3]]` in the
question is a mention line `pdf [[file.pdf#p=3]]: folder/file.pdf, N pages` (the count once indexed) or
"not found in the notes folders". In the panel, `[[` completes PDF names (labelled PDF; `MentionData`
keeps the `pdfs` of `GET /api/notes`), such links in the question and in answers are PDF links, and a
click opens the viewer at the page, flashing the words of a quote it ends (`AskHost.openNotePdf`: the app's
tabs, or the PDF viewer's own panel host, which scrolls the PDF it shows and opens others as tabs). A viewer
showing a notes PDF offers it, at the page in view and with the selected words, as chips like a library paper.

## The AI tools, and why they are restricted

**Claude Code** (`claude`), verified against 2.1.x:

```
claude --print --output-format stream-json --verbose --include-partial-messages
       --tools "" --strict-mcp-config --mcp-config <0600 temp file> --allowedTools mcp__synapse
       --setting-sources "" --disable-slash-commands --append-system-prompt-file <0600 temp file>
       [--model ALIAS] [--effort LEVEL] (--session-id ID | --resume ID)
```

The prompt goes to stdin and the working directory is an empty private one (`work-<profile>/` beside
the profile, stable so sessions can resume). `--tools ""` removes the built-in tools (shell, files,
web), `--strict-mcp-config` ignores every other MCP server, `--setting-sources ""` ignores user and
project settings (hooks, memory) and `--disable-slash-commands` disables skills and commands. The
Synapse server is given in a temp config file with the question's own `mcp_token` in a header (never the
companion's token, which also opens the connector and the companion endpoints), and the server's system text
is in a second temp file, so **no server-supplied string and no token is in argv** (on Windows a
`claude.cmd` is run through cmd.exe, which quotes argument lists unsafely); the session id is a UUID
derived from the server's URL and the conversation id. Both files are 0600 and deleted when the tool
ends. As a second guard the companion reads the `init` event's tool list and aborts the job (killing
the process) if any tool is not `mcp__synapse__…` or the list is empty (the server was unreachable).

**What the tool can change.** The question's token is *restricted* on `/mcp`: the server never lists
or runs the tools that change the library (`add_paper`, `trash_papers` …; see [MCP](mcp.md)) for it,
whatever Settings → AI Access allows other apps (`Server.restricted`, read where `build_server`
offers those tools), and it opens nothing but `/mcp`. Notes and tasks stay changeable, as the system
text allows only when the user asks; every change is attributed and can be undone in the app. This
is decided on the server, not in the companion.

**Codex** (`codex exec --json`) is **off until you turn it on in the window**, which then shows a
warning. Codex always has a shell tool. Here it runs in a read-only sandbox, but it can still read
files on your computer, so **text inside a paper can instruct it to read local files and write their
content into a note on the server**; and the MCP servers in your `~/.codex/config.toml` stay enabled
(and its own instructions apply). Claude Code, run as above, has neither problem. The Synapse server is
passed with `-c mcp_servers.synapse.url=…` and `bearer_token_env_var`, the question's token travelling in the
child's environment (nothing the server sent is in Codex's argv either). Codex has no system-prompt
option, so the instructions are put in front of the question. Follow-ups resume the thread the companion
remembers in memory (a restarted companion starts a fresh thread).

Provider commands are argument lists, never shell strings. The companion starts at most two AI tools
at once itself (one if the window says so) (a repeated job id is ignored and an excess job is answered with an error without
spawning anything). A cancelled job's process group is terminated (killed after 3 s); SIGTERM and
Ctrl+C do the same for every running job at once. Text from the server is stripped of control
characters before it is printed.

## Tokens and the guard

`/api/companion/…` and `/api/connector/…` with a token accept only `Authorization: Bearer` with a
**companion**-scope token, from this computer too; a request with any `Origin` is refused (403),
device cookies do not count, and the Host check applies. `/mcp` accepts `mcp` tokens and the
per-question tokens above, never the companion token itself. Browser tokens (scope `connector`)
are no longer made or accepted; old ones are listed in Settings to be removed. Tokens are made and
removed through `/api/mcp/tokens`; see [MCP](mcp.md#over-http-mcp) and the guard section of
[sync and devices](sync-and-devices.md#ai-apps-mcp). The companion never follows a redirect with
its token.

## The browser extension through the companion

```
extension ──connectNative("org.synapse.companion")──► native host (synapse_companion native-host)
          ◄── length-prefixed JSON over stdin/stdout ──  └── HTTPS, companion token ──► /api/connector/…
```

Chrome starts the native host for the extension: a launcher script in the companion's folder
(`native-host`, or `native-host.bat` on Windows) that runs this program with `native-host --profile
NAME`. It is a separate short-lived process; it reads the profile, so it works whether or not the
background companion is running. **Install** in the window registers it per user: a manifest
`org.synapse.companion.json` in the `NativeMessagingHosts` folder of each Chromium browser that is
installed (Chrome, Chromium, Brave, Edge, Vivaldi; on Windows, registry keys under
`HKEY_CURRENT_USER\Software\…\NativeMessagingHosts` naming one manifest), whose
`allowed_origins` is the extension's id only. The id is fixed by the public `key` in the
extension's manifest (`extension.extension_id`), so it does not depend on the folder; any unpacked
extension carrying the same key gets the same id, so load only extensions you trust in Developer
mode. Snap-packaged Chromium cannot start programs outside its sandbox.

Messages (`native.py`): `{id, type:"hello", version, build}` → `{id, type:"hello", server, name}`,
and records when the extension last said hello and which build it runs (`<profile>-extension-seen.json`,
shown in the window). `{id, type:"request", method, path, json? | form?, timeout?}` → one or more
`{id, type:"response", status, body, more}`: the server's own answer, whatever its status, cut into
parts of at most 128 K characters (Chrome limits a message to the extension to 1 MB). `form` entries
are `[name, text]` or `[name, {data: base64, filename, type}]` and become multipart form data.
Only `GET` and `POST` on `/api/connector/…` paths (letters, digits, `/`, `_`, `-` and a plain query)
are passed on; anything else, and every failure, is `{id, type:"error", message}`. Requests may be
up to 64 MiB, Chrome's own limit (a PDF in base64), and wait at most 60 s.

## Downloads and the extension folder

`GET /api/companion/hello` → `{app, version, extension: {build}, script: {build}}`. A build is a
short SHA-256: of the `.pyz`'s bytes, and of the extension's files (names and contents). `GET
/api/companion/script` and `GET /api/companion/extension` are deterministic zips (fixed dates and
modes; regular files only, hidden files, links and `__pycache__` left out): the `.pyz` holds
`synapse_companion/` and a `__main__.py` that runs it; the extension zip holds `browser_extension/`
plus a generated `build.json` `{"build": …}`, which the extension compares with the build it loaded
with to reload itself ([browser extension](browser-extension.md)). A repository checkout has no
`build.json`.

**The extension's code comes from the server.** Whoever controls the server (or a companion token and
its host) controls what the installed extension does in your browser. The companion therefore guards
the manifest: an update that changes any of `permissions`, `optional_permissions`, `host_permissions`,
`optional_host_permissions`, `content_scripts`, `externally_connectable`, `web_accessible_resources`,
`background` or `content_security_policy` is **not** applied by the hourly check: the window shows
the changed keys and **Apply the update**. This does not review the extension's JavaScript or HTML,
which can change freely with a hash change and run with the permissions already granted; install
from a server you control. Automatic updates can be turned off in the profile's settings
(`extension_updates`).

The companion unpacks into a new temporary folder beside the target (refusing absolute or `..` paths,
backslashes, drive letters, links and other non-regular entries, oversized archives and corrupt zips,
and requiring `manifest.json`), then swaps it for the folder with **two renames** (folder to a
temporary `.old-…` name, new folder into place). That is not one atomic step: a crash between the
two leaves the old copy under the `.old-…` name; a failure of the second restores it. A non-empty
folder is replaced only if it holds a `build.json` from an earlier download, and folders the program
did not create (including a `<dir>.new` or `<dir>.old` of your own) are never touched. Whether the
folder is up to date is decided by hashing its files as the server does, so a locally edited,
added or removed file is repaired by the next sync.

## The window

`ui.py` serves `static/` and a small JSON API on `127.0.0.1` at a free port, only while the
companion runs. Its key is in `<profile>.window.json` (0600, in a 0700 folder). Opening the window
asks the running companion for a one-time ticket (valid 60 s), proving it knows the key with an
HMAC over a fresh nonce and checking the answer's HMAC in turn, so a stale file never hands the key
to another program that now has the port. The browser is started on a private redirect file
(`<profile>.open.html`, 0600) rather than on `/?ticket=…`, because other users of the computer can
read command lines. The page exchanges the ticket for the key, keeps the key in that tab's session
storage and removes the ticket from the address. Every request must name `127.0.0.1:<port>` as its Host (no DNS
rebinding); every change must carry the key in `X-Companion-Key`, be JSON and come from the page's
own origin (or none). Pages have a strict Content Security Policy and nothing is cached. The state
it shows: the connection (`connecting`, `connected`, `unreachable`, `refused`), the tools found and
the settings, questions running, the extension (installed, registered browsers, last hello, review
waiting), start at login, the tray icon, an available update and the recent log.

**The tray icon** is a child process (`synapse_companion tray`), because Qt ends the whole process
when it cannot reach the display, and the companion must survive that. The companion starts it when
there is a desktop and PySide6 can be imported, and writes `{port, key}` to its standard input
(never its command line). The icon reads `/api/state` every 2 s like the window, and sends
`/api/open` (the companion opens the window), `/api/quit` and `/api/tray` (it is shown). It quits
after three failed requests (the companion is gone or restarting; a new one starts its own icon), and
the companion ends it when it stops. Exit status 4 means the desktop has no tray; any other failure
is logged with Qt's last words. The window says which, or the command that installs PySide6.

## Limits

- Needs Python 3.10+ and a signed-in `claude` (or `codex`) on the same computer.
- The browser extension works only through a connected companion on the same computer.
- Starting at login, the tray icon and the browsers' native messaging registration are written for
  Linux, macOS and Windows; only Linux is exercised by the tests (the icon on a virtual X display).
- An unreachable server, a restart or a vanished companion end open answers with an error; nothing
  is retried or kept.
- Provider output formats can change between releases: the Claude Code and Codex readers ignore
  what they do not know, and a stream that ends without a result is reported as an error.
- Phone entry points for Ask AI are not provided yet.
