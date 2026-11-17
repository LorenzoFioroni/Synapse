# Save to Library — Chrome extension

A toolbar button that saves the page you are reading to your local or remote Synapse
library: journal article pages, arXiv, bioRxiv, publisher landing pages, PDFs
opened in the browser, or any web page.

## How it connects

The extension never talks to the Synapse server and stores no address or token. Every library
request goes through **Synapse Companion**, a small program on your computer, over Chrome native
messaging ([docs/companion.md](../docs/companion.md)). The companion holds the connection to your
local or remote library, so no credential lives in the browser. It registers itself with Chrome,
Chromium, Brave, Edge and Vivaldi.

## Install (once)

1. Install Synapse Companion and connect it to your library (see the companion docs). It downloads
   this extension into a folder and registers the native host.
2. Open `chrome://extensions`, switch on **Developer mode** (top right).
3. Click **Load unpacked** and choose the extension folder the companion prepared (or this
   `browser_extension` folder in a checkout).
4. Optional: pin the button (puzzle icon → pin), and check the shortcut at
   `chrome://extensions/shortcuts` (default <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd>).

The extension has a fixed ID (`bfbakjklmpogndkpkbgcaoepnlanibjd`, from the `key` in
`manifest.json`), which the companion allows. Any unpacked extension with the same `key` gets the
same ID, so only load extensions you trust in developer mode.

Right-click the button → **Options** shows whether the companion is connected (and to which
library). If not, open Synapse Companion and connect it to your library. Upgrading from 1.x
removes the old saved address and token; revoke the old browser token in Synapse’s
**Settings → AI Access** if it is no longer needed.

Optional permissions are asked for only when you tick them in Options: the library's address, for
the page bridge below, and all HTTPS sites, for the PDF finder.

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

## Find Missing PDFs

The download icon in the popup opens a window that fetches the publisher's PDF of library items
that have a DOI and none. Each paper opens in a background tab of this browser, so the access
this browser has (campus network, VPN, your sign-ins) is used, and publishers' "checking your
browser" pages pass as when you open the paper yourself; the library attaches a PDF only if it is
that paper. This is what to use when Find Available PDF in the app gets nothing from publishers
because of such checks. The browser must be on the subscribing network (the server need not).

- It asks once for access to all HTTPS sites: the DOIs lead to any publisher.
- One paper every few seconds, in batches of 50: publishers block institutions that download in
  bulk. **Next batch** reaches older items even when the current papers fail. Completed attempts
  are remembered for this library and folder; reopening continues after them. **Start from newest**
  resets that position. **Stop after this paper** leaves the rest available to continue.
- If a site wants you to confirm you are human, do it in its tab, then **Retry**.
- Keep the window open while it runs.
- Under a paper it did not get, the row says what the publisher gave (a sign-in page, `HTTP 403`,
  the article page without a PDF link). A sign-in page usually means this browser is not on the
  subscribing network: try once by hand, on the VPN.
- Article titles open the publisher for review. A PDF with insufficient readable identity is
  labelled **needs review**, rather than being declared the wrong paper; it is not attached.

### Starting from Synapse

Tick **Let the library page ask this browser to find PDFs** in Options and reload your Synapse
page. **Find Available PDF**
(single or batch) and PDF lookup while adding/importing up to 200 items ask the extension to try publisher
PDFs for those DOI items first. The finder opens with just those items. Its first run asks you to
click **Find PDFs** to grant publisher access; subsequent app requests start automatically if that
access is still granted. Keep its window open and use **Next batch** for more than 50 selected items.
If the publisher attempt fails, other enabled PDF sources are queued in Synapse. Items without a
publisher DOI and larger imports use Synapse's normal PDF sources.

Both Synapse and the extension must be updated. The extension answers only requests from the
library the companion is connected to (scheme, host and port). If the companion is not running or the
server is older, the app keeps using its normal PDF sources. The Synapse page and publishers never
see any credential. The bridge is registered only on the library's host and rejects requests from
another origin or a subframe.

### Retrieval from your phone or another device

On the computer with campus/VPN access, open this extension's **Options**, enable
**Handle PDF retrieval from my other devices**. Grant publisher access when asked.
Keep that browser and Synapse Companion running. Synapse and the extension must both support live
PDF requests.

**Find Available PDF** on your phone then starts the finder on that computer using its browser
session and institutional access. The phone itself does not need the VPN. Interactive CAPTCHAs
or expired publisher sign-ins may still need attention on the computer.

This is an immediate handoff: the computer must already be listening, and has five seconds to
accept. With no available receiver, or if acceptance fails, the app uses its server sources.
There is no offline queue and nothing is fetched later merely because a browser reconnects.
Uncheck the option to stop receiving. The choice stays in this browser, bound to its library; it is not synced to other computers. Normal same-browser retrieval works
without enabling this option.

Permissions: `activeTab` + `scripting` to read the current page only when you
click the button (and the pages Find Missing PDFs opens, with the HTTPS access it asks for), `storage` for the settings, and access to `127.0.0.1` /
`localhost` to talk to a local library. Saving a remote connection asks for that server's HTTPS
permission; enabling cross-device PDF retrieval also asks for publisher HTTPS access. Citation
data and PDFs are sent to the configured library.

## Changing it

The extension is plain JavaScript that Chrome loads as it is: there is no build, so
**Load unpacked** works straight from the repository. `extract.js` runs inside the web page
(each of its functions is serialised by `chrome.scripting`, so it must not use anything from
outside itself); `popup.js` and `options.js` are the two pages.

The code is typed with JSDoc comments and type-checked by TypeScript together with the web
app: `npm run check` in `frontend/` (it uses `tsconfig.json` here and the `@types/chrome`
package installed in `frontend/`). `tests/e2e/extension_checklist.py` loads the extension into
Chromium and saves a test page with it.
