# Autocompletion

While you write a note, a model running on this computer suggests how the text goes on. The
suggestion shows in grey after the cursor ("ghost text"), as in code editors. It is a core plugin
(`plugins/autocompletion`, see [plugins](plugins.md#core-plugins)): off until turned on in
Settings → Plugins, and only local models served by [Ollama](https://ollama.com) are supported:
nothing is sent to another computer.

| Key | What it does |
|---|---|
| Tab | Accept the suggestion (one undo step) |
| Esc | Dismiss it |
| Cmd/Ctrl+→ | Accept its next word |
| typing | Typing the suggestion's own characters keeps the rest of it |
| Alt/⌥+\ | Ask for a suggestion now (also right after moving the cursor) |
| click | Clicking the grey text accepts it (an iPad has no Tab key) |

## Setting it up

1. Install Ollama from ollama.com and start it.
2. Install a model: `ollama pull qwen2.5-coder:1.5b` (the default; small and quick), or
   `qwen2.5-coder:7b` (better, about 8 GB of memory).
3. Settings → Plugins: turn on **Autocompletion**. Its own section, Settings → Autocompletion,
   then says whether Ollama and the model are there and what to do if not; the model field offers
   the installed models.

Models that **fill in the middle** (Ollama lists the `insert` capability: `qwen2.5-coder`,
`codestral`, `starcoder2`, `codegemma` …) see the text after the cursor as well, so a suggestion
fits what follows. Other text models only continue the text before the cursor; the status says so.

The plugin's settings (its manifest; they took over the values of Synapse's former
`notes_ai_model`, `notes_ai_length` and `notes_ai_url`, and the plugin started on when
`notes_ai_complete` was on):

| Setting | Default | What it is |
|---|---|---|
| `model` | `qwen2.5-coder:1.5b` | The Ollama model (`name:tag`; no tag means `:latest`) |
| `url` | `http://127.0.0.1:11434` | Ollama's address. Only `localhost`, `127.x.x.x` and `::1` are accepted (`local_url` in `server/ollama.py`): any other address makes the status say so, and nothing is sent |
| `length` | `line` | `line`: the rest of the line · `paragraph`: up to the end of the paragraph (at most 12 lines) |

## Safeguards

Most computers have no Ollama, or not the chosen model, so nothing is asked of a model before
the status has found both:

- **Status first.** `GET /api/plugins/autocompletion/status` lists Ollama's models (`/api/tags`), looks for the
  chosen one and reads its capabilities (`/api/show`). The answer is remembered for 60 s when all
  is well and 15 s when something is missing (`server/completer.py`), and at once again
  when the address or model changes. The editor (`frontend/src/plugins/core/autocompletion/source.ts`) asks for it when a note
  opens and then every 2 minutes (30 s while something is missing), and sends no suggestion
  request while the answer is not "ready".
- **One notice.** When the plugin is on but suggestions cannot be made, the editor shows one
  notice per page with the reason and a button to Settings; it never shows an error per keystroke.
  `POST /api/plugins/autocompletion/complete` always answers 200, with `reason` set when there is no suggestion.
- **When Ollama goes away** while you write (stopped, model removed), the next request says so,
  the editor stops asking and waits for the next status check.
- **Timeouts.** Connecting to Ollama gives up after 1 s (it is on this computer); a status check
  after 4 s; a suggestion after 60 s (the first one may load the model). The model is loaded in
  the background when a note opens (`warm`), and Ollama keeps it loaded for 30 minutes.
- **No pile-up.** Requests run one at a time. One that is still waiting when a newer one arrives
  gives up (`superseded`), and the editor aborts the request it no longer needs as soon as you type.
- **Where suggestions never appear**: in front matter, code and comments, while the citation or
  link list is open, while text is selected, and in the middle of a line (only when the rest of
  the line is empty or closing marks such as `$`, `)` or `]`). A pause of 350 ms after typing comes
  first, and a suggestion needs a few characters before the cursor.

## How it works

```
frontend/src/views/notes/ghost.ts       CodeMirror extension: state field (the suggestion), widget,
  │                                       keys, view plugin (waits for a pause, cancels on typing)
frontend/src/views/notes/ghostLogic.ts  no DOM: where a suggestion may appear, text sent, typing through
frontend/src/views/notes/hooks.ts       noteHooks.ghost: the source a running plugin gives the editor
frontend/src/plugins/core/autocompletion/
  index.ts                              activate: sets the source, adds the Settings section
  source.ts                             the source: status checks, the notice, POST …/complete
  settings.ts                           Settings → Autocompletion (status, model, length, address)
  │
plugins/autocompletion/
  manifest.json                         a core plugin, off by default; its settings
  server/__init__.py                    routes GET /status (fresh=1, warm=1) · POST /complete {prefix, suffix}
  server/completer.py                   Completer: status cache, one request at a time, superseded requests
  server/prompt.py                      the request (prefix/suffix, stop, length) and cleaning the answer
  server/ollama.py                      the HTTP client (every failure becomes Unavailable), local_url
```

The editor itself has the ghost-text extension always; it asks `noteHooks.ghost` when it needs a
suggestion, so turning the plugin on or off takes effect in an open note too.

**The request** (`prompt.build`). The editor sends up to 6,000 characters before the cursor and
2,000 after it; the server keeps whole lines, at most 4,000 and 1,500. A fill-in-the-middle model
gets them as `prompt` and `suffix` (an empty suffix becomes a line end: with an empty suffix
Ollama would use the model's chat template, and the model would answer like a chat). Other models
get the text before the cursor with `raw: true`, so it is continued as it is. `temperature` 0.2,
at most 64 tokens for a line and 220 for a paragraph, and generation stops at the end of the line
(or paragraph) when the cursor's line already has text. Models that think are asked not to.

**The answer** (`prompt.clean`) is cut to one line (always when text follows the cursor on its
line) or one paragraph; special tokens a model prints are dropped; a leading space is dropped
after a space; and text that repeats what follows the cursor is removed (for models that did not
see it, also a closing `$` or bracket that is already there).

**The editor.** The suggestion is a widget decoration at the cursor, so it is never part of the
note: saving, undo and the change feed do not see it. Its keys come before the editor's other
keymaps (Tab accepts a suggestion rather than indenting, and still indents when there is none).
Accepting inserts the text as one change with the user event `input.complete`; accepting a word
counts as typing it, so the rest stays.

## Tests

- `tests/plugins/test_autocompletion.py`: the plugin (off by default, settings carried over from
  Synapse's, a turned-off plugin has no routes), its settings (only addresses on this computer), every status (Ollama not
  running, something else on the port, model missing, embedding model, no fill-in-the-middle),
  nothing sent while off or while the model is missing, the request sent, superseded requests,
  and `clean` on typical answers. It uses `tests/fake_ollama.py`, a stand-in for Ollama's API.
- `tests/js/ghost.test.mjs`: `ghostLogic.ts`.
- `tests/e2e/assist_checklist.py` (port 8793): turning the plugin on, the notice and no requests without Ollama,
  Settings → Autocompletion (status, steps, refused address, Check Again), and the ghost text
  (Tab, Esc, typing through, word by word, Alt+\, clicking, undo, not mid-line, not in code, not
  with the link list open, requests stop when the model goes away, off sends nothing).

Not tried here: a real model in Ollama (the tests use the stand-in), Safari, and the iPad.
