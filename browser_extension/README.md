# Save to Library — Chrome extension

A toolbar button that saves the page you are reading to your local or remote Synapse
library: journal article pages, arXiv, bioRxiv, publisher landing pages, PDFs
opened in the browser, or any web page.

## Install (once)

1. Start the library: `python server.py` (in the repository folder).
2. Open `chrome://extensions`, switch on **Developer mode** (top right).
3. Click **Load unpacked** and choose this `browser_extension` folder.
4. Optional: pin the button (puzzle icon → pin), and check the shortcut at
   `chrome://extensions/shortcuts` (default <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd>).

If you run the library on another port (`--port 5050`), set the address in the
extension's options (right-click the button → Options).

## Remote server

Reload the extension in `chrome://extensions` after updating it. Right-click the button →
**Options**, enter the server’s HTTPS address (for example `https://synapse.example.com`),
and paste a browser token from Synapse’s **Settings → AI Access → New Browser Token…**. Name the
token “Browser extension”. Click **Save** and allow Chrome to connect to that server;
**Connected** confirms the setup. The server must also be updated to support connector tokens.

The token is saved in this browser’s local extension storage, accessible only to trusted
extension pages, never page-injected scripts or Chrome Sync. It survives browser restarts and
extension updates, so setup is needed only once. The address is synced, so each browser needs
its own token and server permission. Changing the address clears the token field.

Browser tokens allow only connector saves and filing; they cannot use MCP or other library APIs.
They have no automatic expiration. Revoke the extension’s token with **Remove…** in AI Access
if you stop using this browser or suspect it has been compromised. AI tokens do not work for
the browser button. Browser sign-in cookies are not used by the extension. Remote connections
require HTTPS; localhost may use HTTP.

Persistent extension storage is not an OS credential vault: someone who compromises this
browser profile may steal its token. Connector-only scope limits the access that token grants.

Updating from an earlier build removes its persisted extension token. Create a dedicated browser
token and revoke any general AI token you previously used for the button if it is no longer needed.

To switch back to a local server, enter `http://127.0.0.1:8000` (or its configured port),
leave the token empty, and save. One server is active at a time.

## What it does

- Reads the page's citation data: `citation_*` meta tags (used by nearly all
  publishers and preprint servers), Dublin Core/PRISM tags, JSON-LD, DOIs and
  arXiv ids in the URL.
- With a DOI or arXiv id, the library completes the metadata online (Crossref,
  OpenAlex, arXiv); the page's own data fills any gaps. Untick "Complete metadata
  online" to keep the page's data; it starts unticked when Settings → Adding Items
  → Retrieve metadata is *Never*. The reference list and an open-access PDF follow
  the other Adding Items settings (with *Ask*, the popup shows a checkbox).
- Lets you pick the folder and add tags before saving (the last folder is remembered).
- Attaches the PDF. It is downloaded *inside the page*, so your own login and
  institutional access apply; if that is not possible the library tries to
  download it itself. Login pages are recognised and not attached.
- A PDF tab is saved too: the DOI is read from the PDF to fetch its metadata.
- Duplicates (same DOI, or same title and year) are detected: open the existing
  item or save anyway.

Permissions: `activeTab` + `scripting` to read the current page only when you
click the button, `storage` for the settings, and access to `127.0.0.1` /
`localhost` to talk to a local library. For remote libraries, optional HTTPS host permission
is requested only for the server you enter. Citation data and PDFs are sent to that server.

## Changing it

The extension is plain JavaScript that Chrome loads as it is: there is no build, so
**Load unpacked** works straight from the repository. `extract.js` runs inside the web page
(each of its functions is serialised by `chrome.scripting`, so it must not use anything from
outside itself); `popup.js` and `options.js` are the two pages.

The code is typed with JSDoc comments and type-checked by TypeScript together with the web
app: `npm run check` in `frontend/` (it uses `tsconfig.json` here and the `@types/chrome`
package installed in `frontend/`). `tests/e2e/extension_checklist.py` loads the extension into
Chromium and saves a test page with it.
