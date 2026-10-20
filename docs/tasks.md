# Tasks

A **task** is a to-do with a title, a status (open or done), an optional due date, priority and
tags, a Markdown body and sub-tasks to any depth. Tasks are records in `library.db`, not text in a
note: a note *shows* a task with a `{{task:<id>}}` line, which the editor draws as the task's
checkbox and title. Ticking the box or editing the body changes the task, never the note. Plain
Markdown checkboxes (`- [ ] …`) stay checkboxes.

This page covers the records (`backend/tasks/store.py`), the lines in notes
(`backend/tasks/service.py` and the notes parser), the HTTP API (`backend/api/tasks.py`), Claude's
task tools and the frontend. Related pages: [notes](notes.md) (the parser and the editor),
[storage](storage.md) (the tables and backups), [mcp](mcp.md).

## The records (`backend/tasks/store.py`)

`TaskStore` keeps two tables in `library.db`. It registers its `SCHEMA` with
`Database.add_schema`, so `Database.migrate` (also run after restoring a backup) creates them.
Tasks are therefore in every library backup, restored together with the items and the notes, and
other windows and devices hear about changes through the change feed like any other
`library.db` write.

| Column | Meaning |
|---|---|
| `id` | 8 characters from `abcdefghjkmnpqrstuvwxyz23456789` (no 0/o, 1/l/i), starting with a letter (`new_id`). |
| `parent_id`, `position` | The tree: `NULL` at the top level; `position` (a float) orders siblings. `_position` puts a task before or after a sibling by taking the midpoint, and renumbers the siblings when two positions get too close. |
| `title` | One line of inline Markdown (`clean_title` joins lines and spaces), at most 500 characters. |
| `body` | Markdown (any length up to 200,000 characters); line endings normalised to `\n`. |
| `status` | `open` or `done` (`STATUSES`). `done_at` is set when it becomes `done` and cleared when it reopens. |
| `due` | `YYYY-MM-DD` or `NULL` (`clean_due` refuses anything else). |
| `priority` | `high`, `medium`, `low` or `NULL`. |
| `tags` | A JSON list of lower-case tags without `#` (`clean_tags`). |
| `created_at`, `updated_at`, `created_by`, `updated_by` | UTC times and `human` or `ai`. |
| `deleted_at`, `deleted_batch` | Set while the task is in the trash. |

Every change goes through `create`, `update`, `set_status`, `move`, `delete` or `restore`, which
validate the values (raising `TaskError`, or `TaskMissing` for an unknown or trashed task) and
record a row in `task_history` with the values before and after.

- **Moves** refuse to put a task under itself or one of its sub-tasks.
- **`set_status(…, with_subtasks=True)`** gives the whole subtree the same status. The app asks
  before ticking a parent whose sub-tasks are open; ticking the last open sub-task never ticks
  the parent.
- **Deleting** moves a task and its whole subtree to the trash as one batch, so one Undo
  (`restore(batch=…)`) brings it all back. Restoring one task brings back what was deleted with
  it; a task whose parent is gone for good comes back at the top level.
- **Emptying the trash**: `purge(older_than)` deletes trashed tasks for good. `Services` runs it
  with each hourly maintenance (`Attachments.on_maintain`), with the notes trash retention
  (`notes_trash_retention`).
- **Undo** (`undo(history_id)`) puts back what a change replaced, but only if the task still has
  the values that change left; otherwise it refuses and overwrites nothing. Undoing a creation
  trashes the task, undoing a deletion restores it.
- **Images in bodies** are stored with the notes' images (`notes/attachments/`). The attachment
  sweep counts them as used (`Attachments.extra_texts`, labelled `task:<id>`), trashed tasks
  included.

## Tasks in notes (`backend/tasks/service.py`)

A note shows a task with `{{task:<id>}}`. The notes parser (`_TASK` in `parser.py`, ported to
`mdscan.ts`) reports it as a link of kind `task`, so the index knows which notes show which tasks
(`NotesIndex.task_refs`, with the heading above each line). As with links, nothing inside code,
math or comments counts. The line may be indented, in a list item (`- {{task:…}}`) or in a quote;
a token inside a sentence is shown as an inline chip.

`TaskService` connects the records and the notes:

- `refs()`: task id → the notes that show it (path, note title, line, heading).
- `add_to_note(task_id, path, after_line=None)` writes the task's line: after `after_line`; else
  after the note's last task line, with the same indentation and list marker; else at the end,
  after a blank line (`insert_ref`). `remove_from_note` deletes the lines that only show the task
  and cuts it out of sentences (`remove_refs`). Both are hash-checked saves, re-read and retried up
  to three times when the note is saved meanwhile. Claude's changes go through
  `NotesService.edit`, so they appear in the note's history and can be undone in the app.
- `create(data)` creates a task and, with `data["note"]`, shows it in that note; if the note
  cannot take it, the new task is trashed again.
- `convert(text)` turns a Markdown checkbox list into tasks: every `- [ ]`/`- [x]` item becomes a
  task (done for `[x]`), nested items become sub-tasks, and the lines indented under an item (text,
  `$$` blocks, plain bullets) become its body. It returns the text with each top-level item
  replaced by its `{{task:…}}` line; everything else is kept, including checkboxes inside code.
  The editor sends the selected lines and puts the result in their place, so the change is one
  ordinary edit of the open note.

A line pointing at a task that is in the trash shows it as deleted, with Restore and Remove. A
task can be shown by several notes, or by none: it lives on in the Tasks page either way.

## HTTP API (`backend/api/tasks.py`)

Writes are recorded in the change feed as `tasks` (`kind_of` in `changes.py`); endpoints that
also write a note record `notes`. `TaskMissing` becomes 404, `TaskError` 400.

| Method | Path | What it does |
|---|---|---|
| GET | `/api/tasks` | Every task (not trashed), `refs` (task id → notes showing it) and the trash count. |
| GET | `/api/tasks/<id>` | One task with `refs` and its direct `subtasks`. |
| POST | `/api/tasks` | Create (`title`, `body`, `parent_id`, `due`, `priority`, `tags`, `status`, `before`/`after` a sibling; `note` and `after_line` to show it in a note). |
| PATCH | `/api/tasks/<id>` | Change fields; `status` with `with_subtasks`; move with `parent_id` (null: top level) and `before`/`after`. Returns the task and every task that `changed`. |
| POST | `/api/tasks/delete` | Trash `ids` (with their sub-tasks); returns the `batch`. |
| POST | `/api/tasks/restore` | Restore a `batch`, or one task (`id`). |
| GET | `/api/tasks/trash` | Trashed tasks. |
| POST | `/api/tasks/<id>/notes` | Show the task in a note (`path`, optional `after_line`). |
| POST | `/api/tasks/<id>/notes/remove` | Take it out of a note (`path`). |
| POST | `/api/tasks/convert` | Checkbox lines (`text`) to tasks; returns the replacement `text` and the `tasks`. |
| GET | `/api/tasks/<id>/history` | The task's recorded changes, newest first. |
| POST | `/api/tasks/undo` | Undo one recorded change (`history_id`). |

## Claude's task tools

`backend/mcp/tools/tasks.py` adds `list_tasks`, `get_task`, `create_task`, `update_task` and
`delete_task` (see [mcp](mcp.md)). `list_tasks` filters with the same syntax as the Tasks page
(`backend/tasks/query.py`). Changes are recorded as `ai`. `get_note` lists the tasks a note shows,
and `NoteChecker` refuses a note that shows an id that is not a task (Claude must create tasks
with `create_task` instead of inventing ids). The writing rules tell Claude that `- [ ]` is a
checkbox and `{{task:ID}}` a task.

## Search syntax

The Tasks page and `list_tasks` understand the same filters:

| Filter | Meaning |
|---|---|
| words, `"a phrase"`, `-word` | in the title or body; `-` excludes |
| `#tag` | has the tag |
| `is:open`, `is:done` | status |
| `due:today`, `due:overdue`, `due:week`, `due:none`, `due:<2026-11-01`, `due:>2026-10-25`, `due:2026-10-27` | due date (`week`: within seven days, overdue included) |
| `priority:high` (`p:high`), `p:none` | priority |
| `note:"Steady-state"` | shown in a note whose title or path contains the words |
| `has:body`, `has:subtasks` | has details, has sub-tasks |

## Tests

`tests/test_tasks.py` covers the store (validation, the tree and positions, statuses with
sub-tasks, the trash, purge, history and undo, the schema after `migrate`), the lines in notes
(placement, removal, Claude's edits in the note history), converting checkbox lists, the API, the
change feed and the attachment sweep. `tests/test_mcp.py` covers the tools, and the parser
fixture `tests/notes/fixtures/tasks.md` (with the parity test) what counts as a task line.
