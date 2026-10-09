# Save to Library — Chrome extension

A toolbar button that saves the page you are reading to your local Zotero Clone
library: journal article pages, arXiv, bioRxiv, publisher landing pages, PDFs
opened in the browser, or any web page.

## Install (once)

1. Start the library: `python server.py` (in `clone_app/`).
2. Open `chrome://extensions`, switch on **Developer mode** (top right).
3. Click **Load unpacked** and choose this `browser_extension` folder.
4. Optional: pin the button (puzzle icon → pin), and check the shortcut at
   `chrome://extensions/shortcuts` (default <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd>).

If you run the library on another port (`--port 5050`), set the address in the
extension's options (right-click the button → Options).

## What it does

- Reads the page's citation data: `citation_*` meta tags (used by nearly all
  publishers and preprint servers), Dublin Core/PRISM tags, JSON-LD, DOIs and
  arXiv ids in the URL.
- With a DOI or arXiv id, the library completes the metadata online (Crossref,
  OpenAlex, arXiv); the page's own data fills any gaps.
- Lets you pick the folder and add tags before saving (the last folder is remembered).
- Attaches the PDF. It is downloaded *inside the page*, so your own login and
  institutional access apply; if that is not possible the library tries to
  download it itself. Login pages are recognised and not attached.
- A PDF tab is saved too: the DOI is read from the PDF to fetch its metadata.
- Duplicates (same DOI, or same title and year) are detected: open the existing
  item or save anyway.

Permissions: `activeTab` + `scripting` to read the current page only when you
click the button, `storage` for the settings, and access to `127.0.0.1` /
`localhost` to talk to the library. Nothing is sent anywhere else.
