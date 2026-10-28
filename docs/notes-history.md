# Notes history

Every version of the notes' text is kept, with who made it — you (and on which device), an AI app,
or another program — in a Git repository of its own. The app shows the versions of a note, or of
all notes, with exactly what each change did, puts any version back, and marks milestones (how
all notes were at a moment, a submitted draft say).

Code: `backend/notes/history/` (recording and reading), `frontend/src/views/notes/historyView.ts`
(the dialogs) and `historyGroups.ts` (grouping, no DOM). Issue #101 has the design discussion.

## The repository

| What | Where |
|---|---|
| Git directory | `data/notes-history.git` (`Config.notes_history_dir`) |
| Work tree | `data/notes/`, passed on every call (`GIT_WORK_TREE`); the notes folder never gets a `.git` |
| Tracked | `*.md` only (`EXCLUDE` in `git.py`, written to `info/exclude`) |
| Not tracked | `attachments/` (images pile up; an old version may show an image that is gone), `.trash/`, the old `.history/`, `.folders.json`, every dot file, every other file |
| Branch | `main`, linear: one history for the whole vault, like an Overleaf project |
| Milestones | annotated tags `refs/tags/m-YYYYmmdd-HHMMSS`, the user's name for it as the tag message |
| Pending edits | `synapse-pending.json` in the Git directory |
| Lock | `data/.notes-history.git.lock` |

Nothing is ever pruned, with one exception: edits you undo (below). Git packs the versions as
deltas itself (`gc.auto` 2000).

The repository is Synapse's alone and is isolated from the user's Git setup (`Repo` in `git.py`):
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `HOME` pointed into the Git directory (for
Git before 2.32), every inherited `GIT_*` variable dropped, an empty hooks directory, and `-c`
overrides for `commit.gpgsign`, `autocrlf`, `quotepath` and `precomposeunicode`. Pathspecs are
literal (`GIT_LITERAL_PATHSPECS`), output is read with `-z`. A global `commit.gpgsign = true`, a
hooks path or `autocrlf` therefore never changes or breaks what is recorded.

Without Git (or with Git older than 2.25) the history is off: `NoteHistory.enabled` is false,
`status()` says why, Settings → Backups says how to install it, and AI changes are still kept the
old way (`legacy.py`: one JSON file per change in `notes/.history/`) so they stay undoable.

## Who made a commit

Each commit message ends with trailers the app reads (`reading.py`):

```
Synapse-By: human | ai | outside
Synapse-Kind: start | earlier | edit | ai | outside | rename | delete | restore | bulk
Synapse-Agent: Claude Code        (AI changes: the MCP client's name, library/agent.py)
Synapse-Device: iPad              (your changes: "This computer" or a paired device's name)
Synapse-Restored: <commit>        (a version put back)
```

The author is the device or the AI app (`you@`, `ai@`, `outside@synapse.local`), so `git log` read
by hand says the same. The device comes from the request (`set_device` in `app.py`, from the
guard's `g.device`), like the AI app's name comes from `acting_as`.

## When commits are made (`recorder.py`)

| Change | Recorded as |
|---|---|
| You save a note in the app (`NotesService.save`, `create`, appending a quote) | *Pending*: the save is wrapped in `NoteHistory.saving`, which notes the path, device and time in `synapse-pending.json`. |
| …left alone for `IDLE` (120 s) | A checkpoint, one commit per device ("Edited plan", "Created plan", "Edited 3 notes"); its author date is the last save. The app's thread (`start`, every `TICK` = 30 s) runs `due()`. |
| …the note is switched away from or its tab closed | A checkpoint at once (`POST /api/notes/history/checkpoint`, from `checkpointAfterSave` in `notesView.ts`). |
| …saved next from another device | That device's pending edits are committed first. |
| An AI app changes a note (`NotesService.edit`, `write_note`) | The note's pending edits are committed first, then the AI change alone, with its summary as the message. |
| Rename or move (`move`, `move_folder`) | A pure-rename commit, after committing what was pending for the note, so Git always follows the note (`log --follow`). |
| Delete, restore from the trash | A commit removing / adding the files. |
| Citation-key rename (`rename_key`) | One commit for every note it rewrote. |
| …that brings the notes back to a state of a moment ago (you changed something and undid it, and a checkpoint fell in between) | No version: `_drop_detour` moves the branch back to that state (`reset --soft`), so the versions in between leave the Git history. Only your own edits on the same device, at most `DETOUR_GAP` (30 min) apart and without a milestone on them, are dropped; an AI change, another device's edit, an outside change or a milestone in between keeps them. The whole tree must match, so no other note's change is lost. |
| A version put back from the history | Your edit, recorded as `restore` with `Synapse-Restored`. |
| A milestone restored | One commit for every note it changed (newer notes go to the notes trash). |
| A backup restored | One commit "Restored the backup …": the history is not rolled back, so nothing recorded after the backup is lost. |
| A note changed by another program | Any difference that is not pending is committed as `outside` before anything else touches the note: before a save of it, before an AI change, in `due()`, at startup and on Rescan. |

`change()` wraps an operation that is its own commit: it takes the lock, commits what was waiting
for the paths it names (all notes with `None`), runs the operation, and commits the paths the
operation reports in `Change.paths`. The index is kept equal to `HEAD` between commits
(`_clean_index`), so a commit holds exactly the paths asked for.

**Two processes.** The web app and the MCP server (`mcp_server.py`) write the same notes. Every
recording step holds `FileLock` (an OS lock on a file, plus a re-entrant thread lock; one object per
file per process, `lock_for`), and a note is *written* inside the lock too: neither process can
see the other's half-recorded change and take it for an outside one. Pending edits live in a file
for the same reason: the MCP server commits the app's pending edits before an AI change.

**Never in the way.** A failing Git call is logged (`last_error`, shown in Settings) and the note
is saved anyway; it is recorded with a later checkpoint. A save waits at most 10 s for the lock.

**Creating the repository.** On first use (`_ensure`) the repository is made, the old JSON history
is brought in (`migrate.py`, below), and the notes as they are become "Start of the history". A
damaged repository is moved aside (`notes-history.git.damaged-<time>`) and a new one started.

## Bringing in the old JSON history (`migrate.py`)

The old files held, for each AI change, the text before it and a hash of the text after it. Per
note, in time order: the text before the first change becomes "Earlier history"; each AI change
whose result is known (the next change's "before" has its hash, or the note now does) becomes an
AI commit dated when it was made; otherwise the AI change and the user's edits after it become one
commit, saying the AI's own version was not kept. All notes' events are sorted by time and
written with one `git fast-import`. The JSON files are left where they are.

## Reading (`reading.py`)

- `log(path)` — commits, newest first, with the files each touched (`-M90%`: Synapse's renames are
  exact; a lower threshold pairs two different short notes sharing most of their front matter).
  With a path, `--follow`.
- `note_log(path)` — a note's versions with its path before and after each and the SHA-256 of both
  texts (the app compares them with the note it has open: the AI bar, "the note now").
- `version(commit, path, from_path)` — the text after a commit and before it.
- `diff(a, b)` — the notes that differ between two states and both texts (at most 60 notes or
  4 MB). A state is a commit, `<commit>^` (before it; the empty tree before the first), a
  milestone id, or `now` (the notes on disk, via a scratch index, without committing).
- `reverted(commit, path, text)` — the note with one change taken out and later edits kept
  (`git merge-file`); refused when later edits touch the same lines. `undo_note_edit` uses it.

## HTTP API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/notes/history?path=&limit=` | A note's versions (`enabled`, `entries`; without Git the AI changes kept the old way). |
| GET | `/api/notes/history/version?id=&path=&from=` | The note's text after and before one version (`id` a ULID: an old JSON entry). |
| GET | `/api/notes/history/status` | `enabled`, `git` version, `reason`, last `error`, the Git `folder`. |
| GET | `/api/notes/history/log?limit=&skip=` | Every change (after a checkpoint) and the milestones. |
| GET | `/api/notes/history/diff?from=&to=` | See `diff` above; 404 for an unknown state. |
| POST | `/api/notes/history/checkpoint` | `{path?, restored_from?}`: record what is waiting now. |
| POST | `/api/notes/history/milestones` | `{label}`: mark how all notes are now. |
| PUT/DELETE | `/api/notes/history/milestones/<id>` | Rename (`{label}`) or delete a milestone (the versions stay). |
| POST | `/api/notes/history/milestones/<id>/restore` | Put every note back as it was then: `restored`, `trashed`, `trash_id`. |

Commits are announced in the change feed as `history` (`on_commit`), so an open history list
reloads; the history endpoints themselves do not record a `notes` change, except restoring a
milestone.

## The app

- **A note's history** — **History** in the note's head (beside Export; only its icon on a
  phone), the note's details (**Versions…**) or the palette (**History of This Note…**). Left: the versions; your edits less than 30 minutes
  apart on the same device form one row ("Edited · 4 versions", open it for each one), and every
  AI change, rename, restore or outside change is a row of its own. A row of your edits that ends
  where it began is left out (`changesNothing`: the note's hashes, or for all notes the trees
  before and after, which `log` gives as `tree` and `parent_tree`); such detours are dropped when
  recorded, so this only hides ones recorded before that. Right: what the selected row
  changed (line diff with the changed words marked, `diff.ts`), or **Compared with now**.
  **Restore This Version** puts it in the editor (Cmd/Ctrl+Z takes it back) and records a restore.
- **All notes** — the palette (**Notes History…**), the notes list's menu, Settings → Backups. Each
  change with every note it touched; a milestone shows what changed since, and **Restore All
  Notes…**, **Rename…**, **Delete**. **Mark Milestone…** (also in the palette and the notes list's
  menu) names the state of all notes now.
- Settings → Backups says whether the history is on, where it is, or how to install Git.

## Backups

A backup holds `notes-history.bundle` (`git bundle create --all`, after a checkpoint). Restoring a
backup keeps the live history and adds the restored notes as one commit; only a history that was
missing, damaged or empty before the restore began (`lost()`, a new data folder say) is rebuilt
from the bundle (`after_restore`), the one made meanwhile kept as `notes-history.git.replaced-<time>`.

## Tests

`tests/notes/test_history.py` (isolation from the user's Git config, checkpoints, devices, outside
changes, AI commits, pure renames, deletes, key renames, milestones, restores, reverts, two
processes, no Git, migration, backups, the API), `tests/js/historyGroups.test.mjs`, the
`notes_history` browser checklist and `claude_edits` (the AI bar and the note's history).

The worker also calls `Services.sync_external_notes` on each tick, independently of Git being
available or making a commit. This refreshes the disposable notes index and publishes `notes`
changes only when files were added, changed or removed. History failure does not disable this
index refresh; errors in the refresh are logged and retried at the next tick.
