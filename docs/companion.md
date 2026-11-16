# Companion

The companion is a small program on your own computer that connects a Synapse server, often a
remote one behind a tunnel, to services only your computer has. Two jobs so far:

1. **Ask AI from inside Synapse.** A question in a note or about a selection in a PDF goes to the
   companion, which runs your own AI command-line tool (Claude Code; Codex if you allow it)
   restricted to Synapse's tools. The answer streams back into the app. The server holds no API
   key and never runs the tool.
2. **Browser extension install and updates.** The companion downloads the extension from the
   server into a folder for the browser's "Load unpacked" and keeps it current.

For the extension itself see [browser extension](browser-extension.md); for the app side of Ask AI
see [frontend](frontend.md).

| Piece | Where |
|---|---|
| The program (one file, Python standard library only) | `synapse_companion.py` |
| The in-memory rendezvous (`Companions`), one per `Services` | `backend/companion/hub.py` |
| The question the AI tool receives | `backend/companion/prompt.py` |
| The downloads: extension zip and script, with build hashes | `backend/companion/bundle.py` |
| Endpoints (thin) | `backend/api/companion.py` |
| The token rule | `backend/access/guard.py`, `backend/access/tokens.py` |
| Tests | `tests/test_companion_hub.py`, `tests/test_companion_api.py`, `tests/test_companion_cli.py` |

## Setting up

On the computer running Synapse, **Settings → AI Access → New Companion Token…** makes a token
(`syn_companion_…`, shown once). On your own computer, with Claude Code installed and signed in:

```bash
python synapse_companion.py connect https://synapse.example.com   # asks for the token
python synapse_companion.py run
```

`connect` checks the server, then stores `{server, token, client, name}` in a profile under the
user config directory (`~/.config/synapse-companion/`, `~/Library/Application Support/…`,
`%APPDATA%\…`; `SYNAPSE_COMPANION_HOME` overrides it), file mode 0600. A server on another computer
must be `https://`; plain `http://` is accepted for loopback addresses only. `--profile NAME`
keeps connections to several servers apart; `--name` is how the computer shows in Synapse.
Prefer the prompt or `SYNAPSE_COMPANION_TOKEN` to `--token`, which shows in the process list.

The script can be downloaded without a checkout: Settings → AI Access has a Download button
(`GET /api/ai/companion-script`, normal device auth, an attachment named `synapse_companion.py`), and
`GET /api/companion/script` with the token serves the same file to the companion's own `update`.

| Command | |
|---|---|
| `connect URL [--token T] [--name N]` | Store the profile. |
| `run [--allow codex]` | Poll for questions, run them (at most two at once, enforced here whatever the server hands out), update the extension folder at start and hourly if one was installed (see the review rule below), say when a newer script exists. Ctrl+C or SIGTERM stops the running AI tools at once and ends their questions with an error. |
| `extension [--dir D] [--yes]` | Download or refresh the extension folder (default beside the profile), show which security keys an update changes and ask before applying it (`--yes` for scripts), and print the Load-unpacked steps once. |
| `status` | The connection, script and extension freshness, which AI tools were found. |
| `update` | Replace the script with the server's. Only on this explicit command. |

## How a question travels

```
app ──POST /api/ai/ask──► Companions ◄──POST /api/companion/poll (long poll ≤ 20 s)── companion
app ◄─GET /api/ai/jobs/ID?after=N (long poll ≤ 20 s)─ events ◄─POST …/jobs/ID/events── companion ── claude ──POST /mcp──► Synapse
```

Plain HTTPS requests and long polls: no WebSocket or event stream, so it works behind Cloudflare
Tunnel with Werkzeug's threaded server, like the live PDF handoff (`online/browser_pdfs.py`). No
new port. Everything is in memory in `Services.companions`; nothing is saved, a restart ends open
conversations, and independent instances share nothing.

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
- A conversation is pinned to its companion and provider (`resume: true` on follow-ups). One turn at
  a time per conversation (409). A follow-up whose companion is gone answers 409, "start a new
  conversation". Conversations are forgotten after a day.
- Event posts: at most 64 events and 256 KB per request, 2 MB of text and 20,000 events per job;
  unknown event types are dropped; consecutive text events merge. Finished jobs stay readable for 10
  minutes (at most 100), then 404.
- A companion ID (`client`) is bound to the token that first used it: a poll or event post from another
  token is refused (409), and one token may have at most 4 companions (of 16).
- Request bodies on `/api/companion/*` and `/api/ai/*` are limited to 512 KB (413 beyond).
- Posting events touches neither the library nor the change feed.

**Events** from the companion: `{type:"text", text}` (a delta), `{type:"tool", name}` (a tool call
began), `{type:"done"}`, `{type:"error", message}`.

## What the AI tool receives

`prompt.build` (not the API handler) writes the question: the user's words, then where they are (the
note's path and title, or the paper's citation key, title, PDF page, attachment id when the item has
several PDFs) between `<context>` tags, and the selected text or quote between `<selection>` /
`<quote>` tags: all data the model must not obey (a tag cannot be closed from inside, titles are one
line). The system text asks for the Synapse tools,
note Markdown with `[@key, p. N]` citations, brevity and changes to notes only when asked.
Selections are limited to 8,000 characters, the question to 20,000; an unknown note or item is a 404.

## The AI tools, and why they are restricted

**Claude Code** (`claude`), verified against 2.1.x:

```
claude --print --output-format stream-json --verbose --include-partial-messages
       --tools "" --strict-mcp-config --mcp-config <0600 temp file> --allowedTools mcp__synapse
       --setting-sources "" --disable-slash-commands --append-system-prompt-file <0600 temp file>
       (--session-id ID | --resume ID)
```

The prompt goes to stdin and the working directory is an empty private one (`work-<profile>/` beside
the profile, stable so sessions can resume). `--tools ""` removes the built-in tools (shell, files,
web), `--strict-mcp-config` ignores every other MCP server, `--setting-sources ""` ignores user and
project settings (hooks, memory) and `--disable-slash-commands` disables skills and commands. The
Synapse server is given in a temp config file with the token in a header, and the server's system text
is in a second temp file, so **no server-supplied string and no token is in argv** (on Windows a
`claude.cmd` is run through cmd.exe, which quotes argument lists unsafely); the session id is a UUID
derived from the server's URL and the conversation id. Both files are 0600 and deleted when the tool
ends. As a second guard the companion reads the `init` event's tool list and aborts the job (killing
the process) if any tool is not `mcp__synapse__…` or the list is empty (the server was unreachable).

**What the tool can change.** The companion's token is *restricted* on `/mcp`: the server never lists
or runs the tools that change the library (`add_paper`, `trash_papers` …; see [MCP](mcp.md)) for a
companion token, whatever Settings → AI Access allows other apps (`Server.restricted`, read where
`build_server` offers those tools). Notes and tasks stay changeable, as
the system text allows only when the user asks; every change is attributed and can be undone in the
app. This is decided on the server, not in the companion.

**Codex** (`codex exec --json`) is **off unless you run `run --allow codex`**, and `run` then prints a
warning. Codex always has a shell tool. Here it runs in a read-only sandbox, but it can still read
files on your computer, so **text inside a paper can instruct it to read local files and write their
content into a note on the server**; and the MCP servers in your `~/.codex/config.toml` stay enabled
(and its own instructions apply). Claude Code, run as above, has neither problem. The Synapse server is
passed with `-c mcp_servers.synapse.url=…` and `bearer_token_env_var`, the token travelling in the
child's environment (nothing the server sent is in Codex's argv either). Codex has no system-prompt
option, so the instructions are put in front of the question. Follow-ups resume the thread the companion
remembers in memory (a restarted companion starts a fresh thread).

Provider commands are argument lists, never shell strings. The companion starts at most two AI tools
at once itself (a repeated job id is ignored and an excess job is answered with an error without
spawning anything). A cancelled job's process group is terminated (killed after 3 s); SIGTERM and
Ctrl+C do the same for every running job at once. Text from the server is stripped of control
characters before it is printed.

## Tokens and the guard

`/api/companion/…` accepts only `Authorization: Bearer` with a **companion**-scope token, from this
computer too; a request with any `Origin` is refused (403), device cookies do not count, and the
Host check applies. `/mcp` accepts `mcp` or `companion` tokens (the companion gives its AI tool the
same token); `/api/connector/…` still takes connector tokens only. Tokens are made and removed
through `/api/mcp/tokens` with `scope: "companion"`; see [MCP](mcp.md#over-http-mcp) and the guard
section of [sync and devices](sync-and-devices.md#ai-apps-mcp). The companion never follows a redirect
with its token.

## Downloads and the extension folder

`GET /api/companion/hello` → `{app, version, extension: {build}, script: {build}}`. A build is a
short SHA-256: of the script's bytes, and of the extension's files (names and contents). `GET
/api/companion/extension` is a deterministic zip of `browser_extension/` (regular files only; hidden
files and links left out) plus a generated `build.json` `{"build": …}`; the extension compares it
with the build it loaded with to reload itself ([browser extension](browser-extension.md)). A
repository checkout has no `build.json`.

**The extension's code comes from the server.** Whoever controls the server (or a companion token and
its host) controls what the installed extension does in your browser. The companion therefore guards
the manifest: an update that changes any of `permissions`, `optional_permissions`, `host_permissions`,
`optional_host_permissions`, `content_scripts`, `externally_connectable`, `web_accessible_resources`,
`background` or `content_security_policy` is **not** applied by `run` (hourly or at start): it logs one
line saying to run `extension`, which lists the changed keys and asks (`--yes` skips the question).
This does not review the extension's JavaScript or HTML, which can change freely with a hash change
and run with the permissions already granted; install from a server you control.

The companion unpacks into a new temporary folder beside the target (refusing absolute or `..` paths,
backslashes, drive letters, links and other non-regular entries, oversized archives and corrupt zips,
and requiring `manifest.json`), then swaps it for the folder with **two renames** (folder to a
temporary `.old-…` name, new folder into place). That is not one atomic step: a crash between the
two leaves the old copy under the `.old-…` name; a failure of the second restores it. A non-empty
folder is replaced only if it holds a `build.json` from an earlier download, and folders the program
did not create (including a `<dir>.new` or `<dir>.old` of your own) are never touched. Whether the
folder is up to date is decided by hashing its files as the server does, so a locally edited,
added or removed file is repaired by the next sync.

## Limits

- Needs Python 3.10+ and a signed-in `claude` (or `codex`) on the same computer.
- An unreachable server, a restart or a vanished companion end open answers with an error; nothing
  is retried or kept.
- Provider output formats can change between releases: the Claude Code and Codex readers ignore
  what they do not know, and a stream that ends without a result is reported as an error.
- Phone entry points for Ask AI are not provided yet.
