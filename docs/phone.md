# The phone layout

A phone gets a layout of its own, built for a small touch screen: a tab bar at the bottom, lists
of papers and notes as rows, pages that slide in on top with a back button, menus as sheets that
rise from the bottom, and notes that open for reading. Tablets and computers keep the desktop
layout (with its touch support). Both layouts share everything underneath: the store, the
actions, the API client, the change feed and the plugins.

## Which layout a page gets

`phone/layout.ts` decides once, when the page loads:

- A **small touch screen** gets the phone layout: `(pointer: coarse)` and at most 600px wide, or at
  most 500px tall (a phone on its side). A mouse never counts, so a narrow browser window on a
  computer stays on the desktop layout.
- A device can override it, stored in its browser (`localStorage` `zc.layout`):
  - on a phone, the settings button's **Use the Desktop Layout**;
  - on a touch screen showing the desktop layout, **Use the Phone Layout** in the toolbar's More
    menu or the command palette;
  - `?layout=phone`, `?layout=desktop` or `?layout=auto` in the URL (handy for tests).

The page loads `js/main.js` either way; at its end it hands over to `phone/main.ts` (`bootPhone`)
instead of building the desktop views. The decision is not revisited while the page runs, so
turning a phone or resizing a window never swaps the layout under the reader.

## The shell

`phone/shell.ts` builds the frame:

- **The tab bar**: Library, Notes, the **+** button, one **pinned** plugin destination and
  **Plugins**. Without any plugin list or page usable on a phone it is Library · + · Notes. Its
  badges come from the plugins (a list's badge, a page's `setBadge`); the Plugins tab adds up the
  badges of what is not pinned.
- **The pinned tab** is the reader's choice (long-press it, or Plugins → **Tab next to Notes**;
  stored per device as `zc.phone.pin`). By default it is the first plugin destination usable on a
  phone. When its plugin stops, the next one takes its place.
- **Stacks of screens**: each tab keeps its own stack. A screen pushed on top (a paper, a note, a
  plugin's sub-screen) gets a back button named after the screen below; the browser's Back (the
  phone's Back gesture) and a swipe from the left edge go back too. Choosing the tab shown again
  goes back to its first screen.
- **Sheets**: `ui/menu.ts` draws every menu as a sheet from the bottom on a phone
  (`menusAsSheets`), so the menus the desktop shares (an item's actions, choosing a folder) need
  nothing special. `phone/ui.ts` has the shell's own sheets (the + sheet, a citation).

What other parts of the app ask for (`show-item`, `open-note`, `open-view`, `open-page`,
`open-tasks`, `open-settings`) is done the phone's way, on top of the tab shown (`phone/main.ts`).
Settings is the desktop's Settings page, which is already laid out for narrow screens.

## The screens

| Module | Screens |
|---|---|
| `phone/library.ts` | The Library tab: search, folders and smart folders as chips, sorting, a **Folders & tags** sheet (also Unfiled, Duplicates, Trash and tag filters); a paper's page (Read, Paper note, Copy key, Share; abstract; the notes that cite it; folders and tags; references; all details, each tapped to edit; **From plugins**: plugin fields as rows and plugin item sections as cards to fold); its ⋯ sheet. |
| `phone/reader.ts` | A PDF: the app's viewer (`viewer.html`) over the whole screen under a thin bar. |
| `phone/notes.ts` | The Notes tab: recent notes, folders, search; a note opened for **reading** (no front matter, citations as chips, wide equations shrunk or scrolled sideways); **Edit** turns it into the editor with a bar above the keyboard (cite, link, math, image, formatting, and the buttons plugins add); a tapped citation opens a sheet (the PDF at the cited page, the paper note, the paper). |
| `phone/plugins.ts` | The Plugins tab; a plugin's list of papers; a plugin's page. |
| `phone/model.ts` | The decisions without a DOM (unit-tested in `tests/js/phone.test.mjs`). |

The note screen uses the same pieces as the desktop's notes: `NoteSession` (saving, drafts,
conflicts), `createEditor`, `createRenderer`, and the notes workspace state (`ws`) for citations,
links and tasks while that note is shown.

## Plugins on a phone

Library and Notes are Synapse's own; everything else comes from plugins, which may be on or off.
What plugins already declare shows up without phone-specific code:

| What a plugin declares | Where a phone shows it |
|---|---|
| A list of papers (`@plugin.item_source`) | A destination in the Plugins tab (and the pinned tab). The papers of its first group are **cards** (badges, the plugin's score, its first reason, Add / PDF / Not for me); later groups are **rows** to swipe: right adds, left dismisses. Its variants (days, say) are chips. |
| A paper's `group` in such a list | The heading the paper is shown under (arXiv Today's Read, Skim, Rest). |
| An item action with `role: 'dismiss'` | "Not for me" and the swipe to the left. |
| Item sections, per-item fields, item actions | A paper's page under **From plugins**; actions in its ⋯ sheet. |
| A page with a `phone` layout (`p.pages.register({…, phone})`) | A destination. A page with only a desktop `mount` is listed as needing a larger screen. |
| `p.capture.register` | An entry in the **+** sheet, under **From plugins**. |
| `p.editor.toolbar.register` | A button above the keyboard while a note is edited. |

Turning a plugin off takes its tab, entries, cards, actions and buttons away at once. See
[plugins](plugins.md#the-phone-layout-111) for the API.

## Tests

`tests/e2e/phone_checklist.py` (port 8824) runs the phone layout in Chromium's Pixel 5 profile
with a throw-away plugin that offers a grouped list, a dismiss action, a phone page, a capture
entry and an editor button. `touch_app` and `portable_touch` keep checking the desktop layout on
a phone (they choose it with `zc.layout`).
