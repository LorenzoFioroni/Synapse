# The app window, tabs and the desktop shortcut

Synapse can run like a desktop program: in a window of its own rather than a tab of the browser you
browse with, started from an icon, with the server stopping when the window is closed. PDFs open
in tabs inside the app, next to the library, in that window and in a browser tab alike.

| Piece | Where |
|---|---|
| `python server.py --window`, `--quit-when-closed`, `--make-shortcut` | `server.py` |
| Opening the window | `backend/desktop/window.py` |
| The server stopping with its last window | `backend/desktop/presence.py`, `backend/api/window.py`, `frontend/src/presence.ts` |
| The desktop shortcut | `backend/desktop/shortcut.py`, the icon `frontend/assets/Synapse.icns` (drawn by `tools/icons/make_icns.py`) |
| The app's tabs | `frontend/src/tabs/model.ts` (no DOM), `frontend/src/tabs/tabs.ts`, the hook in `frontend/src/api.ts` |
| The viewer inside a tab | `frontend/src/viewer/inTab.ts`, `frontend/src/viewer/jumpHistory.ts`, `links.ts` |

## The window: `--window`

```bash
python server.py --window
```

starts the server as usual and then opens the app in a window of its own. In order of preference:

1. **Synapse installed as an app** (macOS). Chrome, Edge and Brave can install any page as an app:
   open `http://127.0.0.1:8000` in Chrome and choose **⋮ → Cast, Save and Share → Install Page as
   App…** (Edge: **Apps → Install this site as an app**). The browser then keeps a small
   `Synapse.app` in `~/Applications/Chrome Apps` (or `Edge Apps`, `Brave Browser Apps`), with the
   Synapse icon in the Dock and in the app switcher. `--window` finds it by the address in its
   `Info.plist` (`CrAppModeShortcutURL`): same scheme and port, on this computer. Install it from
   the port you run Synapse on; an app installed for another port is not used.
2. **A Chromium browser in app mode** (Chrome, Edge, Brave, Chromium, Vivaldi): the browser started
   with `--app=<address>`, a window without tabs or address bar. Its Dock icon is the browser's,
   which is why the server prints how to install the app (1).
3. **The default browser**, when no Chromium browser is found.

If Synapse already runs on that port, `--window` only opens the window and exits, so the shortcut
can be clicked again at any time. If another program has the port, it says so and exits.

The window is the same web app as in a browser tab, on the same address, so it shares the browser's
storage (column widths, settings of the views and so on) with tabs of that browser. The app's own
tabs belong to each window (see [Windows](#windows)).

## Stopping with the window: `--quit-when-closed`

A server started with `--quit-when-closed` stops by itself, as if Ctrl+C were pressed, once no
Synapse window is open:

- Each app page (and a PDF viewer in a browser tab of its own) says hello to
  `POST /api/window/ping` when it opens and every 30 seconds, and goodbye
  (`POST /api/window/bye`, sent with `keepalive` on `pagehide`) when it closes. A viewer inside the
  app's tabs is counted with the app around it.
- The server stops 15 seconds after the last window said goodbye (a reload says hello again sooner),
  when no window was heard from for 10 minutes (it was closed while the computer slept, the browser
  crashed; a hidden window still says hello at least once a minute), or 3 minutes after starting
  if no window ever opened.
- A server started without the option answers `{"watch": false}`, and the pages stop asking.

`Presence` (`backend/desktop/presence.py`) keeps the windows; `server.py` calls `watch()` with
`_thread.interrupt_main`, so the server finishes what it is answering and exits normally. The MCP
server that Claude Desktop starts (`mcp_server.py`) is a separate program and keeps working.

## The desktop shortcut: `--make-shortcut`

```bash
python server.py --make-shortcut                     # the defaults
python server.py --make-shortcut --data-dir ~/MyLibrary --port 5050
```

makes an icon that runs `server.py --window --quit-when-closed` with the other options given
(`--port`, `--data-dir`, `--lan`, `--host`, `--cert`/`--key`, `--allow-host`), then exits. It
remembers this checkout's folder, the Python that ran the command (the virtual environment's) and
the `PATH` (so that Node.js is found when the frontend must be rebuilt after a `git pull`). Run it
again after moving the folder or the virtual environment, or to change the options.

**macOS.** `~/Applications/Synapse.app`, which Spotlight and Launchpad find and which can be kept
in the Dock, and a link to it on the Desktop. It is a small app bundle:

- `Contents/MacOS/synapse`, a shell script. It starts the server in the background (`nohup`),
  writing to `~/Library/Logs/Synapse/server.log` (moved to `server.log.1` past 1 MB), and returns
  once the server runs, or once it has opened the window of a server that was running already. If
  the server stops with an error, an alert shows the end of the log.
- `Contents/Resources/Synapse.icns`, the Synapse icon drawn on the macOS icon grid.
- `Info.plist` with `LSUIElement`: the launcher has no Dock icon of its own; the window has one.

If `~/Applications/Synapse.app` exists and was not made by Synapse (Safari's **Add to Dock** names
its web apps after the site), the shortcut is called `Synapse Launcher.app` instead.

**Linux.** `synapse.desktop` in `~/.local/share/applications` (the applications menu) and on the
Desktop, with the icon `frontend/assets/icon-512.png`.

**Windows** is not supported: the command says what a shortcut made by hand should run.

## The app's tabs

The Synapse tab (the library, Notes and Tasks; it is named after the one shown) always comes first.
Each PDF opened from the library, a citation, a card or a menu gets a tab after it, holding the
viewer page (`viewer.html`) in a frame. The bar appears once a PDF is open in a tab.

- **One tab per PDF.** Opening a PDF that has a tab shows that tab, scrolled to the cited spot
  (`zcViewerGoto`, as for browser tabs before). Cmd/Ctrl+click (or a middle click on a citation)
  opens another tab of it; of two tabs of one PDF, the one shown last is reused.
- **Closing** (the ×, a middle click, ⌃W on a Mac or Alt+W elsewhere) shows the tab the PDF was
  opened from if the reader has not switched tabs since, else the tab to its right, else the one to
  its left, as browsers do. ⌃⇧T / Alt+Shift+T reopens the last closed tab (up to ten are kept).
  Cmd+W stays the browser's: it closes the window.
- **Switching**: click, Ctrl+Tab and Ctrl+Shift+Tab (also Ctrl+PageDown/PageUp), ⌃1…⌃9 or
  Alt+1…Alt+9 (1 is the Synapse tab, 9 the last tab). In a browser tab the browser keeps
  Ctrl+Tab (and, outside macOS, Alt+digits) for its own tabs; in the app window they reach the app.
  The keys work while a PDF has the keyboard too: `viewer/inTab.ts` passes them to the page around.
- **Order**: drag a tab. **Right-click** a tab for Close Other Tabs, Close Tabs to the Right, Reopen
  Closed Tab, Move to New Window, Open in a Browser Tab, Show in Library and Reload; right-click the
  Synapse tab for New Window and Close All PDF Tabs.
- **A reload** brings the window's tabs back, their order and the shown one, each at the page it
  was left on. A tab's viewer is loaded only when the tab is first shown.
- **Back/Forward inside a PDF.** In a browser tab of its own the viewer's jumps (links, citations)
  are browser history entries. Inside the app's tabs they cannot be, because the page around shares
  that history: the app's Back would step through the PDF's jumps out of sight. So there they are
  kept in memory (`JumpHistory`), with the same toolbar arrows, Alt+←/→, ⌘[ / ⌘] and the mouse's
  back and forward buttons, and the frame's address is only replaced, never pushed. Going Back or
  Forward in the app, showing an item or opening a note shows the Synapse tab first.
- **Settings → PDF Viewer → Open PDFs in** (`pdf_open_in`: `tabs` or `browser`) sends PDFs back to
  browser tabs of their own, as before.

### Windows

Each window has its own tabs. They are kept in the window's `sessionStorage` (`zc.tabs`), which
belongs to that window alone and lasts as long as the window: a reload brings them back, a new
window starts with only the Synapse tab, and a window opened after the app was closed (the desktop
shortcut, the next morning) starts afresh with the library. No list of windows is kept.

New windows are opened from the menus, at a place of the app named after `#` in their address
(`windows.ts`, read once by `openFromHash` in `main.ts`):

| Menu | Entry | Opens |
|---|---|---|
| A PDF tab | Move to New Window | that PDF, at its page, in a tab of a new window (and closes it here) |
| The Synapse tab | New Window | the library |
| An item | Show in New Window, Open PDF in New Window (one PDF) | the library with the item selected (in the folder shown); its PDF in a tab |
| An attachment row, the item pane's attachment menu | Open in New Window | that PDF in a tab |
| A folder, a smart folder (sidebar) | Open in New Window | the library at that folder |
| A note (notes list) | Open in New Window | the note in Notes |
| A task (Tasks page) | Open in New Window | the Tasks page with the task selected |

The address is `/#item=…`, `#view=folder:<id>`, `#note=<path>&line=…`, `#task=<id>`,
`#pdf=<viewer address>`, each with `window=new`. `readPlace` leaves out anything that does not look
like a place (a view that is not a folder, a PDF address that is not `/viewer.html?…`). `#item=`
alone is also what the browser extension's "Open in Library" uses. `window=new` makes `tabs.ts`
start without tabs even where a browser copied the opener's session storage into the new window.
The window is opened with `window.open(…, 'popup,noopener,…')` at the size of the current one, a
little offset: `popup` asks for a window rather than a browser tab, and `noopener` keeps the two
apart. When PDFs open in browser tabs (Settings), a PDF's "Open in New Window" opens the viewer page
itself in a window.

How the new window looks depends on where Synapse runs: from Synapse installed as an app it should
be another app window; from Chrome's app mode or a browser tab, a window without tabs and with a
read-only address bar. Neither could be checked on a Mac here.

How it fits together: `tabs.ts` registers itself in `api.ts` (`setPdfTabOpener`), so
`openAttachment`, `pdfTabFor` and `pointPdfTab` open a Synapse tab when the setting says so, and
`api.ts` does not import the tabs. `main.ts` names the Synapse tab after the workspace
(`setHomeLabel`), calls `showHome()` before navigating, and gives `tabShortcut` the keys first. While
a PDF tab is shown, the toolbar, the panes and the status bar behind it are `inert`. Hidden tabs'
frames keep their size (`visibility`, not `display`), so their viewers keep their layout and their
place; the Synapse tab gets the keyboard back where it had it. `window.zcTabs` is what a viewer in
a tab calls (`shortcut`, `touched`) and what the browser checklist reads (`list`).

## Tests

- `tests/js/tabs.test.mjs`: the tab model (opening, reuse, closing and where it goes, reopening,
  moving, the n-th tab, saving and restoring, addresses), `JumpHistory`, and the address of a new
  window (`placeHash`, `readPlace`).
- `tests/test_desktop.py`: `Presence` with a fake clock, the endpoints and the guard, the setting,
  finding the installed app and the browser, `synapse_at`, the shortcut on macOS (bundle, script
  checked with `bash -n`, icon, Desktop link, another app of the same name) and Linux, and
  `server.py --make-shortcut` / `--window` run for real.
- `tests/e2e/tabs_checklist.py` (ports 8796 and 8797): the tabs in the browser, each window with
  its own tabs, Move to New Window, New Window and Open in New Window from the item, folder, note
  and task menus, and a `--quit-when-closed` server stopping after its page closes.

Not verifiable in the test container: a real Mac (the app bundle, the Dock, Chrome's installed app),
Chrome's app mode, and the tab keys in a real app window.
