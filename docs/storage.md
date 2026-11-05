# Storage

Everything the user owns lives in one data folder, so it can be backed up or moved as a whole.
This page describes what is in that folder, which component owns each file, the schema of the
library database and how it is migrated, and what backups contain. For where each package sits,
see [architecture](architecture.md).

## The data folder

`backend/config.py` (`Config`) knows every path. The folder is, in order of precedence, the one
passed on the command line (`--data-dir` for `server.py`, `--data` for `mcp_server.py`), the
`SYNAPSE_DATA_DIR` environment variable (`ZCLONE_DATA_DIR` is also accepted), or `data/` in the
repository. It is git-ignored.

```
data/
├── library.db          items, attachments, folders, tags, smart folders, PDF annotations, tasks
├── fulltext.db         text extracted from the PDFs (derived)
├── pdfs/               the attached files
├── notes/              Markdown notes, the source of truth for notes
│   ├── papers/         paper notes (one per citation key)
│   ├── ai/             notes written by the MCP server
│   ├── attachments/    images used by notes
│   ├── .trash/         deleted notes, in <timestamp>/ folders
│   ├── .trash-attachments/   images no note uses any more (and the .since marker)
│   ├── .history/       AI changes kept before the Git history (and while Git is missing)
│   └── .folders.json   colours given to notes folders (folder path → #rrggbb)
├── notes-history.git/  every version of the notes' text (Git; its work tree is notes/)
├── .notes-history.git.lock   taken by the app and the MCP server in turn while recording
├── notes_index.db      links, tags, headings and search index of the notes (derived)
├── citations.db        reference lists fetched online (a cache)
├── backups/            library-*.tar.gz and notes-*.tar.gz
├── settings.json       preferences
├── auth.db             paired devices (hashed tokens), passkeys (public keys), access tokens (hashed)
├── plugins/            plugins the user installed, one folder each (code; see plugins.md)
├── plugin-data/        what each plugin stores, in <id>/ (and, not in backups: its log .plugin.log,
│                       its Python packages .python/, its built frontend .build/, .jobs.sqlite, .schedules.json)
├── plugins.json        which plugins are on, their settings, why one was turned off
└── fields.db           values plugins attach to papers (per-item fields)
```

`Config.ensure_dirs` creates the folder and `pdfs/`; `Vault.ensure` (`backend/notes/vault.py`)
creates `notes/` and `notes/attachments/`. Every database file is created by its owner the first
time it is opened. `mcp_server.py` refuses to start when `library.db` is missing, so pointing it
at the wrong folder does not create an empty library.

## Who owns what

Each file has one owner. Other code goes through that owner, with the few exceptions listed.

| File | Owner | Schema code | Kind |
|---|---|---|---|
| `library.db` | `Database` in `backend/library/db.py`, used by `PaperRepository`, `AnnotationStore`, the repositories in `library/organize.py` and `TaskStore` (`backend/tasks/store.py`) | `Database.migrate`, plus `SCHEMA` in `backend/library/annotations.py` and in `backend/tasks/store.py` | source of truth |
| `pdfs/` | `PaperRepository` (`attachment_path`, `_remove_file`) and the upload code in `backend/api/attachments.py` | none | source of truth |
| `notes/` | `Vault` in `backend/notes/vault.py` | none (see [notes](notes.md)) | source of truth |
| `settings.json` | `SettingsStore` in `backend/settings.py` | `DEFAULTS`, `_migrate` | source of truth |
| `auth.db` | `Devices`, `Passkeys` and `AccessTokens` in `backend/access/` (`devices.py`, `passkeys.py`, `tokens.py`), one table each, on `AuthTable` (`authdb.py`) | `SCHEMA` in each | source of truth, not in backups |
| `fulltext.db` | `FulltextIndex` in `backend/pdf/fulltext.py` | `FulltextIndex._init` | derived from `pdfs/` |
| `notes_index.db` | `NotesIndex` in `backend/notes/index.py` | `SCHEMA`, `SCHEMA_VERSION` | derived from `notes/` |
| `citations.db` | `CitationStore` in `backend/references/store.py` | `SCHEMA` there | cache of online data |
| `plugins.json` | `StateFile` in `backend/plugins/state.py` | none (unknown keys are ignored) | source of truth |
| `fields.db` | `FieldStore` in `backend/plugins/fields.py` | `SCHEMA` there (`CREATE TABLE IF NOT EXISTS`) | source of truth for what plugins set; in backups |
| `plugin-data/<id>/` | each plugin, through `plugin.storage` (`backend/plugins/storage.py`) | the plugin's own `migrations`, in `PRAGMA user_version` | source of truth |
| `plugins/` | the user (installing a plugin); read by `PluginHost.scan` | `manifest.json`, `backend/plugins/manifest.py` | code, not in backups |
| `backups/` | `Backups` in `backend/backup.py`; `Rekeyer.backup` in `backend/notes/rekey.py` | none | copies |

The exceptions: `backend/backup.py` copies `library.db` with raw `sqlite3` (the online backup
API), `backend/changes.py` watches the modification time of `library.db` and `notes_index.db` to
notice writes from another process, and `_page_count` in `backend/mcp/tools/common.py` reads page
counts from the `documents` table of `fulltext.db`.

The web app and the MCP server are separate processes that open the same files (both build a
`Services`). SQLite's own locking keeps them consistent; connections wait up to 15 seconds
(30 for `fulltext.db`) for a lock. See [sync and devices](sync-and-devices.md) for how each side
notices the other's writes.

### Source of truth and what can be rebuilt

| File | If it is lost |
|---|---|
| `library.db`, `notes/`, `settings.json` | Restore a backup. |
| `pdfs/` | Not in backups. The user must back it up separately. |
| `auth.db` | Paired devices must pair again, passkeys be added again and AI apps get new tokens. Left out of backups on purpose, so a restore never brings back a device, passkey or token that was removed. |
| `fulltext.db` | Rebuilt from the PDFs: by the indexer at startup when `fulltext_auto_index` is on, or with Settings → Rebuild Index. See [full-text search](fulltext-search.md). |
| `notes_index.db` | Rebuilt at startup: `Services.__init__` calls `NotesIndex.sync()`, which indexes every note it does not know. |
| `citations.db` | The reference lists are fetched again. See [references](references.md). |
| `plugins.json`, `plugin-data/`, `fields.db` | Restore a backup (most field values can also be fetched again by their plugin). |
| `plugins/` | Not in backups: install the plugins again. |

## Creating items through the HTTP API

`POST /api/items` accepts the editable metadata keys returned by `GET /api/schema`
(`editable: true`), plus `tags`, `folder_id` and `folder_ids`. An empty object creates an
untitled item. Unsupported keys, including read-only fields such as `id`, `date_added` and
`date_modified`, reject the entire request before any item, tag or folder membership is saved.
The response is HTTP 400 with a JSON `error` naming every unsupported key in alphabetical order.
For example, `{"title": "A paper", "publication": "Nature"}` returns
`{"error": "Unknown item fields: publication"}`; use `journal` for the publication field.
This validation applies to this creation endpoint; imports and repository callers retain their
own input handling.

## The library database

`Database` (`backend/library/db.py`) opens a new connection per unit of work. `session()`
commits on success, rolls back on error and always closes. Every connection turns on
`PRAGMA foreign_keys`. After a commit that changed rows, `session()` bumps `Database.version`
(used as a cache key by `backend/references/service.py`) and calls `on_write`, which `Services`
points at the change feed. A failure in `on_write` is swallowed, because telling other windows
must never undo a saved change.

All SQL for items lives in the `library/` package. HTTP endpoints never build queries.

### Tables

| Table | One row per | Notes |
|---|---|---|
| `papers` | item | Columns come from `backend/library/fields.py`: one per `Field` in `FIELDS`, plus `INTERNAL_COLUMNS`. |
| `attachments` | attached file | `paper_id` (cascades on delete), `name` (shown to the user), `url`, `type`, `date_added`. |
| `folders` | folder | A tree: `parent_id` points at the parent; `position` orders siblings. |
| `paper_folders` | item in a folder | Primary key `(paper_id, folder_id)`. An item can be in any number of folders. |
| `tags` | tag | `name` is unique, case-insensitive (`COLLATE NOCASE`). `color` is optional. |
| `paper_tags` | tag on an item | Primary key `(paper_id, tag_id)`. |
| `smart_folders` | saved search | `query` and `mode` (`basic`, `all` or `fulltext`, default `all`). |
| `annotations` | PDF annotation | Tied to an attachment and a page; see `backend/library/annotations.py`. |
| `annotation_migration` | converted legacy item | Marks items whose old annotation blob was converted. |
| `tasks` | task | A tree through `parent_id`, ordered by `position`; title, Markdown body, `open`/`done`, due date; `deleted_at`/`deleted_batch` while in the trash; `updated_by` (`human`/`ai`) and `updated_agent` (the AI app, added later as a new column). See [tasks](tasks.md). |
| `task_history` | change to a task | Who (`by`: `human`/`ai`; `agent`: the AI app, added later as a new column), what (`create`, `update`, `move`, `delete`, `restore`), the values before and after (JSON). |

Details worth knowing:

- **`papers.id`** is a random hex UUID (`uuid.uuid4().hex`), made in
  `PaperRepository.create_many`.
- **Field columns** are `TEXT`, except `year` (`INTEGER`). `authors` holds
  `Family, Given; Family, Given` (lists are joined with `"; "` by `_clean_value`).
  `date_added` and `date_modified` are UTC strings like `2026-01-31T12:00:00Z` (`utcnow`).
- **`INTERNAL_COLUMNS`** are `id`, `deleted_at` (set when an item is moved to the Trash, `NULL`
  while it is in the library), and three columns kept only so old data stays readable:
  `folder_id` (single folder, replaced by `paper_folders`), `annotations` (the old per-item SVG
  blob, replaced by the `annotations` table) and `pdfUrl` (no backend code reads it).
  `PaperRepository.list` and `get` drop `annotations` and `folder_id` from what they return.
- **`attachments.url`** is either a file name in `pdfs/` or an `http(s)` address.
  `attachment_path` only ever uses the base name of `url`, so a stored value cannot point outside
  `pdfs/`, and it returns `None` for web addresses.
- **`paper_tags` and `paper_folders` have no foreign keys.** The code removes their rows itself
  (`delete_permanently`, `FolderRepository.delete`, `TagRepository.delete`), and `migrate()`
  deletes orphans on every start.
- **`annotations.attachment_id`** cascades from `attachments`, so deleting an attachment row
  deletes its annotations. `rects` and `path` are JSON text. `fill` (a box's fill colour) was
  added later; `annotations.upgrade`, run by `migrate()`, adds the column to older files.
- **Unused tags** are deleted when no item has them, unless the user gave them a colour
  (`TagRepository._prune`, and the same statement in `delete_permanently`).
- **Indexes:** `idx_title`, `idx_papers_deleted`, `idx_papers_doi`, `idx_att_paper`,
  `idx_paper_folders_folder`, `idx_paper_tags_tag` and `idx_annotations_attachment`.

### Citation-key integrity

`PaperRepository.create_many` and `update` validate nonempty citation keys (`citekeys.validate`).
A key uses ASCII letters, digits and `_ - : . / +`, starts with a letter, digit or `_`, and ends
with a letter, digit, `_` or `+`: the shape the notes' citation parser reads as one key (it treats
a trailing `. : / -` as punctuation). A key already held by another item, in the library or the
Trash, is refused, also when it differs only in case: two such items' paper notes would share one
file on macOS and Windows. Empty keys remain valid for incomplete items. Generated keys are passed
through `citekeys.sanitize`, so they always validate, and get the usual `a, b, c` suffixes.

Key selection and validation run under a SQLite write transaction (`BEGIN IMMEDIATE`), so two
writers cannot claim the same key. Explicit keys in a batch are reserved before others are
generated. For `create_many` (and the single `create`), a bad or taken key rejects the whole
batch; HTTP writes return 400 with the reason, and duplicate errors name the item holding the key.

Imported files often carry keys made elsewhere, so `import_many` (used by `POST
/api/import/commit`) repairs instead of refusing: a key is sanitised (accents folded, other
characters dropped, ends trimmed), and if that is taken, in the library or earlier in the file, it
gets a suffix. When nothing of the key survives, one is generated from the pattern. The response
lists each replaced key in `renamed_keys` (`id`, `title`, `from`, `to`), and the import dialog
offers to show them.

Existing records are not rewritten: metadata edits and resubmitting an unchanged legacy key still
work. A changed key must pass validation before the existing note/task rekey listeners run.

### How migrations work

There is no schema version number. `Database.migrate()` brings any older file up to date, and it
is safe to run on a current one. It runs every time a `Services` is built (the web app and the
MCP server) and again after a backup is restored (`Backups._restore_db`), so an old backup gets
the columns this version needs.

In one transaction, `migrate()`:

1. Creates `papers` from `INTERNAL_COLUMNS` and `FIELDS` if it does not exist, then adds every
   missing column with `ALTER TABLE papers ADD COLUMN` (found by comparing with
   `PRAGMA table_info`). Adding a field to `FIELDS` is enough to add its column.
2. Creates the other tables with `CREATE TABLE IF NOT EXISTS`, and adds `attachments.date_added`
   when an old table lacks it.
3. Copies memberships from the old `papers.folder_id` into `paper_folders`, then clears
   `folder_id`.
4. Creates the indexes and runs `annotations.SCHEMA`.
5. Backfills values older versions never stored: `date_added`, `date_modified`, `item_type`
   and `attachments.date_added`.
6. Deletes orphaned rows in `attachments`, `paper_tags` and `paper_folders`.
7. Runs the schemas other packages registered with `Database.add_schema` (the tasks tables), so
   a restored backup from before they existed gets them.

One conversion is too large and too risky for `migrate()`: turning the old SVG blobs in
`papers.annotations` into rows of `annotations`. `migrate_legacy` in
`backend/library/annotations_legacy.py` does it after the web app starts (`create_app`). It makes
a backup first, records each item in `annotation_migration` whether or not it succeeded (so it
runs once per item), and leaves the old column untouched. It needs `pypdfium2`; without it, it
waits for a later start.

### The other databases

`fulltext.db` and `citations.db` use `CREATE TABLE IF NOT EXISTS`. An `auth.db` from before
passkeys gains the `passkeys` and `access_tokens` tables. `AccessTokens._conn` additively migrates
`access_tokens` under a serialized SQLite transaction, adding `scope TEXT NOT NULL DEFAULT 'mcp'`.
Existing token hashes, names and IDs are preserved; legacy tokens stay MCP-only. New connector
tokens use `scope='connector'`; scope is checked before accepting a request. Both scopes remain
valid until revoked. No database is rewritten. `notes_index.db` stores `SCHEMA_VERSION` in its `meta` table; when
the stored number differs, `NotesIndex._init_schema` drops every table and the next sync fills
them again. That is fine because the index is derived data.

## Attachments on disk

Attached files sit flat in `pdfs/`, named with a random hex prefix so names never clash: uploads
are `<hex>_<original name>` (made safe by `secure_filename`), PDFs found online
`<hex>_downloaded.pdf`, and PDFs from the browser extension `<hex>_connector.pdf`. They are served
at `/pdfs/<file name>`.

Items have a Trash (`papers.deleted_at`), but attached files do not. Moving an item to the Trash
keeps its files. Deleting an attachment, deleting items for good or emptying the Trash removes
the files at once (`PaperRepository._remove_file`) and their rows in `fulltext.db`; deleting
items also removes their reference lists from `citations.db`. Because backups leave out
`pdfs/`, a restored library can name files that no longer exist; `attachment_path` returns
`None` for those.

The **attachment trash**, `notes/.trash-attachments/`, is for images used by notes, not for
item files. `Attachments` in `backend/notes/attachments.py` moves an image there once no note
shows it (never within an hour of it being added), brings it back if a note uses it again, and
deletes entries of both notes trashes after the `notes_trash_retention` setting. `create_app`
starts this maintenance (`Attachments.start`, hourly). See [notes](notes.md).

## Settings

`SettingsStore` (`backend/settings.py`) keeps the preferences in `settings.json`. `DEFAULTS`
lists every key with its default value, and its type decides how `update()` coerces a value.
`CHOICES` lists the allowed values of the enumerated keys; a few keys have range or format checks
in `update()` (for example `backup_dir` must be an absolute folder that can be written to, and
the `annotation_color_…` and `annotation_fill_…` keys must be colours like `#ffcc00`, stored in
lower case).

- Reading: the file is merged over `DEFAULTS`; unknown keys are ignored, and a file that cannot
  be read or parsed gives the defaults.
- Writing: `update()` writes every key, through a temporary file and a rename, so a crash never
  leaves half a file.
- Old files: `_migrate()` renames keys and values written by older versions. Because every key is
  written back on save, an old `True` cannot be told from an old default, so only a deliberate
  `False` is carried over.
- `reload()` reads the file again; the restore of a backup uses it.

`backend/config.py` holds paths only, no preferences.

## Backups

`Backups` in `backend/backup.py` writes one archive per backup, `library-YYYYMMDD-HHMMSS.tar.gz`
(with `-1`, `-2` … for several in one second), into `backup_dir` or `data/backups/`. Each holds:

| Entry | How |
|---|---|
| `manifest.json` | Written first, so listing reads only the start of each archive: `created`, `reason`, `items`, `notes` and `format`. |
| `library.db` | Copied with SQLite's online backup API, so the copy is consistent while the app writes. |
| `settings.json` | As it is on disk, if it exists. |
| `notes/` | Every file in the notes folder, including `attachments/`, both trashes, `.history/` and `.folders.json`. |
| `notes-history.bundle` | The notes history (`git bundle --all`), when Git is installed. Restoring keeps the live history and adds the restored notes to it; the bundle rebuilds a history that was missing or empty (see [notes history](notes-history.md#backups)). |
| `plugins.json` | As it is on disk, if it exists. |
| `fields.db` | Copied with the backup API, if it exists. |
| `plugin-data/` | What plugins store; SQLite files (`.sqlite`, `.db`) copied with the backup API, `-wal`/`-shm` files and dot files (the plugins' logs) left out. |

Left out on purpose: `pdfs/` (large), `fulltext.db` and `notes_index.db` (rebuilt),
`citations.db` (fetched again) and `auth.db`.

The archive is written as `<name>.part` and renamed when complete, so a half-written archive
never looks like a backup. After each backup, `prune()` keeps the newest `backup_keep` archives.

**When.** `Backups.start()` (called by `create_app`, never by the MCP server) checks at once and
then every 15 minutes (`CHECK_EVERY_S`), and backs up when the newest archive is older than
`backup_interval` (`daily` or `weekly`; `off` disables backups). `before(operation)` makes one
before risky operations: emptying the Trash, deleting items for good, imports and regenerating
more than ten citation keys. It skips the backup if one was made in the last ten minutes
(`BEFORE_EVERY`), and a failure never stops the operation. Any backup counts as the newest, so a
backup made before an operation also postpones the next scheduled one. `migrate_legacy` calls
`create()` directly.

The `notes-*.tar.gz` archives are different: `Rekeyer.backup` writes them into the same folder
(`backup_dir` or `data/backups/`, read at each backup) before rewriting notes for a citation-key
change, keeps the newest 20 (`BACKUPS_KEPT`), and
`Backups.prune` never touches them. See [notes](notes.md).

**Restore.** `Backups.restore(name)` works in this order, so nothing live is touched until the
archive is known to be good:

1. Extract into a temporary folder inside the data folder. `_extract` refuses absolute paths,
   `..`, links and any top-level entry other than `manifest.json`, `library.db`, `settings.json`,
   `notes`, `plugins.json`, `plugin-data` and `fields.db`.
2. Check the database (`PRAGMA integrity_check` and a query on `papers`).
3. Make a safety backup of the current state (reason `before restoring <name>`). If it fails,
   the restore stops.
4. Copy `library.db` into the live file through the backup API, so open connections stay valid,
   then run `migrate()`.
5. Copy `settings.json`, but keep the current `backup_interval`, `backup_keep` and `backup_dir`,
   so the backup folder does not move away from the backups just listed.
6. Swap `notes/`: the live folder is renamed aside, the restored one moved in (a rename, because
   the temporary folder is on the same disk), and the old one deleted only on success.
   The plugins are stopped first; `plugins.json`, `fields.db` and `plugin-data/` are put back the same way
   (today's plugin logs are kept; a backup without plugin data leaves today's alone), and the
   plugins are started again afterwards, also when the restore fails.
7. Rebuild the notes index, schedule a sweep of note attachments and, when
   `fulltext_auto_index` is on, sync the full-text index.

The endpoints are in `backend/api/backups.py`.

## Changing the schema

`library.db` is the user's data, so its schema is changed by migrating, never by rewriting.

1. **New item field:** add a `Field` to `FIELDS` in `backend/library/fields.py`. `migrate()`
   adds the column. SQLite's `ADD COLUMN` cannot add a `PRIMARY KEY` or `UNIQUE` column, or a
   `NOT NULL` one without a default, so new columns must allow `NULL`.
2. **New table or index:** add a `CREATE TABLE IF NOT EXISTS` (or `CREATE INDEX IF NOT EXISTS`)
   to `migrate()`, or to a `SCHEMA` string in the owning module that `migrate()` runs, as
   `annotations.SCHEMA` does.
3. **New column on another table:** check `PRAGMA table_info` and `ALTER TABLE ... ADD COLUMN`,
   as for `attachments.date_added`.
4. **Backfill** with `UPDATE ... WHERE <column> IS NULL`. Every step runs on every start and
   after every restore, so it must be idempotent and cheap.
5. **Never drop or rename** a column or table, and never change what an existing column means.
   To restructure, copy the data into the new shape and leave the old column in place (as
   `folder_id` and `annotations` are). A conversion that can fail or takes long goes outside
   `migrate()`, after a backup, and records its progress per row, like `migrate_legacy`.
6. **Test the upgrade** from an old file, like `test_old_schema_is_upgraded` in
   `tests/test_api.py`. Remember that a backup of any older version can be restored.
7. **Update this page**, and `frontend/src/types.ts` if the API returns the new data.

For the derived files: bump `SCHEMA_VERSION` in `backend/notes/index.py` when the notes index
changes. `fulltext.db` has no version, so a change to its layout needs one added (or the index
cleared) so old files are rebuilt. For `settings.json`, add the key to `DEFAULTS` (and `CHOICES`
if it has fixed values), and put renames in `_migrate()`.

### Concurrent plugin storage

`backend/plugins/files.py` serializes JSON changes and SQLite migrations between the web app and
standalone MCP process. Locks live beside their data as hidden `.lock` files, are reentrant
within one thread, and are released by the operating system when a process exits. JSON is written
to a unique temporary file and atomically replaced. The `plugins.json` and plugin store formats
are unchanged. Lock files are coordination, not data, and backups exclude them.
