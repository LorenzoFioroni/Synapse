# Offline use

Synapse can keep working when the server cannot be reached: an iPad without a connection, a laptop
away from the computer that runs Synapse. Nothing is saved on a device unless its user turns this
on, so the default is the plain app, and PDFs are never downloaded in bulk: each one is chosen.

## Turning it on

**Settings → Offline Use** has one switch per browser (the choice is kept in that browser's
`localStorage`, key `zc.offline`; it is not a server setting, because the saved copy lives in that
browser). Turning it on:

1. registers the service worker (`frontend/sw.js`, started by `frontend/src/offline.ts`);
2. saves the app: every page, script, style, font and image, listed by the server at
   `GET /api/offline/shell` (`backend/frontend.py`, with a `version` that changes when any of them does);
3. saves the library: the list, each item's details and what the app asks for when it starts.

No PDF is saved by this. Progress and the result show in the page (`views/settings/offline.ts`,
`offlineSync.ts`). The save is repeated quietly a few seconds after the app starts and whenever the
browser comes back online, so new papers and a new version of Synapse are saved too (files already
saved are not fetched again). It also fetches again the annotation lists of the PDFs made available
offline, and forgets saved PDFs that left the library. **Save Now** repeats it by hand.

## Choosing PDFs

While offline use is on, the item list's menu (one paper, or several selected) and a PDF's own menu
(its row in the list; its ⋯ menu on the phone) have **Make Available Offline**, which saves the PDF
and its annotation list, or **Remove Offline Copy** once all of them are saved, which deletes the
copies (`setPdfsOffline`, `offlineMenuEntry` in `offlineSync.ts`). A PDF opened online is not saved
by that. A small green dot on a paper's PDF mark shows that it is available offline.
Turning the switch off cleans up after itself: it stops a save in progress, unregisters the worker
(which stops saving at once, even for pages still open), deletes the saved files, the queue of waiting
annotation changes (after asking, when some are waiting) and what the browser remembered about the
last save (`zc.offline*` in `localStorage`).

It needs a secure page: https, or Synapse open as `localhost`. Elsewhere the switch says so.
Saved files count against the browser's storage quota for Synapse's address; the page shows the use.

## Notes

While offline use is on, **Make Available Offline** in the notes list's menu (a note, several, or a
folder: its notes now and later) keeps notes on the device (`frontend/src/offlineNotes.ts`). Their
text lives in IndexedDB (`synapse-offline-notes`), not in the shared Cache Storage, and is refreshed
by every save made online and by the background save. **Locked notes are never kept**: a note that
answers as locked is forgotten. A green dot in the notes list marks a kept note; offline, notes not
kept are dimmed and opening one says so.

The note calls of `api.ts` (`getNote`, `checkNote`, `saveNote`, `createNote`, `notes`) go through
`notesOffline`, so the editor (`NoteSession`) and the phone work unchanged:

- **Offline**, a kept note opens from IndexedDB and each save becomes a *local version* (its text
  and the time it was made; saves less than 2 minutes apart are one version). The save answers with
  a local hash (`local:…`), and the note keeps saving locally until its versions are in Synapse. An
  amber dot marks it. **New notes** can be created offline (the listing shows them); renaming,
  moving, deleting and locking need Synapse.
- **Back online** (the browser's `online` event, or Synapse answering again), `pushNotes`
  (`offlineSync.ts`) sends each note's versions with the text and hash it started from to
  `POST /api/notes/offline` (`NotesService.apply_offline`):
  - the note did not change in Synapse: each version is written and committed on its own, by this
    device, dated when it was made, with the subject "Edited … offline"
    (`NoteHistory.offline`): the history stays linear, as if the device had been online;
  - it changed: each version is first merged three-way with Synapse's text (`NoteHistory.merge`,
    `git merge-file`), then recorded the same way (`status: "merged"`);
  - a merge overlaps (`status: "conflict"`) or the note was deleted (`"deleted"`): nothing is
    written. The note's next opening (or at once, when it is open) asks
    (`views/notes/offlineConflict.ts`): **Keep the version in Synapse**, **Keep my offline version**,
    or **Fix by hand**, which puts the merge with Git's conflict markers (`<<<<<<< Saved in Synapse`,
    `>>>>>>> Your offline edit`) in the editor to edit
    and save as usual. A deleted note can be saved again or let go.
  - A locked note (423) keeps its versions until it is unlocked on this page.
- An open note learns the result by its regular check (`checkNote`): the local hash is no longer the
  note's, so its text becomes Synapse's (the merge included). A save sent with the local hash after
  the versions went in unchanged is based on the hash they became (`synced`).

Turning offline use off asks first when notes have edits that are not in Synapse yet.

## What it does

- **Reading.** Pages, data and PDFs that succeed are kept in the Cache Storage (`synapse-offline-v1`).
  Pages and data come from the network first (6 s timeout) and fall back to the saved copy; a chosen
  PDF comes from the saved copy first, and other PDFs straight from the network, unsaved. The viewer fetches whole files (`disableRange`) so they can be saved.
  Never kept: streams (`/api/changes`, `/api/presence`), secrets (passkeys, devices, backups, updates,
  AI tokens) and the text of notes (`/api/notes/note`, history, links, locks), so a locked note
  cannot outlive its lock in a cache.
- **Which entries are available.** While Synapse cannot be reached (the browser is offline, or the
  server stopped answering) a paper whose PDFs are not saved is dimmed in the list and on the phone,
  its PDF mark is greyed with "Not saved for offline use", and opening such a PDF says it is not
  saved instead of failing. A paper without a PDF is just its record and is available. A small
  pill at the bottom says "Offline" or "Synapse can't be reached".
- **Annotations.** A create, change or delete that cannot reach the server gets a `202` reply at once.
  It is queued in IndexedDB (`synapse-offline` / `outbox`) and also applied to the saved annotation
  list, so a reload while offline still shows it. The queue is sent in order when the browser comes
  back online, after any successful request, and every 30 s while items wait. An entry the server
  refuses (4xx other than 401/403) is dropped and reported.
- **Not covered:** renaming, moving, deleting or locking notes offline, library edits, searching and other folders or
  views than the library list the app opened with, and the iOS/Safari storage limits.

## Where it lives

| What | Where |
|---|---|
| Worker: caching, the queue, saving lists of files in small batches, pruning | `frontend/sw.js` |
| Registering, removing, reachability, the pill, which PDFs are saved | `frontend/src/offline.ts` |
| What to save, progress, the automatic repeat, choosing PDFs | `frontend/src/offlineSync.ts` |
| The settings section | `frontend/src/views/settings/offline.ts` |
| The menu entries and the marks in the list and on the phone | `views/itemlist/menus.ts`, `views/itemlist/rows.ts`, `phone/library.ts`, `phone/reader.ts` |
| The list of the app's files | `backend/frontend.py` (`shell_files`) |
| Kept notes, local versions, the note calls offline | `frontend/src/offlineNotes.ts` |
| Bringing offline edits in: apply, merge, record | `NotesService.apply_offline` (`backend/notes/service.py`), `NoteHistory.offline`/`merge` (`recorder.py`), `POST /api/notes/offline` |
| The choice on a conflict | `frontend/src/views/notes/offlineConflict.ts` |

Checklists: `python tests/e2e/run.py offline` (port 8872), `python tests/e2e/run.py offline_notes`
(port 8873). Tests: `tests/notes/test_offline_notes.py`.
