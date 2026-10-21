// The data the backend sends, as the JSON API returns it. Keep in step with the backend:
// item fields come from backend/library/fields.py (GET /api/schema lists them at run time),
// folders, tags and smart folders from backend/library/organize.py, settings from
// backend/settings.py (DEFAULTS).

/** An attachment of an item: a file in data/pdfs (``url`` is its file name) or a web link. */
export interface Attachment {
  id: number;
  paper_id: string;
  name: string;
  url: string;
  type: string;            // 'pdf', 'link', …
  date_added?: string;
}

/** A library item. Lists are "lean": empty fields and the abstract are left out. */
export interface Item {
  id: string;
  item_type?: string;
  title?: string;
  authors?: string;        // "Family, Given; Family, Given"
  year?: number | string;
  journal?: string;
  volume?: string;
  issue?: string;
  pages?: string;
  publisher?: string;
  place?: string;
  edition?: string;
  series?: string;
  isbn?: string;
  issn?: string;
  doi?: string;
  url?: string;
  citation_key?: string;
  short_title?: string;
  language?: string;
  keywords?: string;
  abstract?: string;
  extra?: string;
  pmid?: string;
  pmcid?: string;
  archive?: string;
  archive_id?: string;
  library_catalog?: string;
  call_number?: string;
  license?: string;
  date_added?: string;
  date_modified?: string;
  deleted_at?: string | null;
  attachments?: Attachment[];
  tags?: string[];
  folder_ids?: number[];
  dup_group?: number;      // in the Duplicates view: items of one group share it
  _lean?: boolean;         // set by the frontend: a list entry without the abstract (the pane fetches it)
  [field: string]: unknown;
}

export interface Folder {
  id: number;
  name: string;
  parent_id: number | null;
  position: number;
  count: number;           // items directly in it (not in subfolders)
}

export interface Tag {
  id: number;
  name: string;
  color: string | null;
  count: number;
}

export type SearchMode = 'basic' | 'all' | 'fulltext';

export interface SmartFolder {
  id: number;
  name: string;
  query: string;
  mode: SearchMode;
  position: number;
}

export interface Sidebar {
  folders: Folder[];
  tags: Tag[];
  smart: SmartFolder[];
}

export interface Counts {
  library: number;
  trash: number;
  duplicates: number;
}

/** Search hits of an item: a highlighted full-text snippet and/or a note that matched. */
export type Hits = Record<string, { fulltext?: string; note?: string; [k: string]: unknown }>;

export interface ItemList {
  items: Item[];
  hits: Hits;
  counts: Counts;
  mode: SearchMode;
  query: string;
  sidebar: Sidebar;
}

export interface ItemsByIds {
  items: Item[];
  missing: string[];
  counts: Counts;
  sidebar: Sidebar;
}

export interface FieldDef {
  key: string;
  label: string;
  sql: string;
  multiline: boolean;
  editable: boolean;
  basic_search: boolean;
  searchable: boolean;
  rare: boolean;
}

export interface Schema {
  fields: FieldDef[];
  itemTypes: { key: string; label: string }[];
  typeFields: Record<string, string[]>;
  containerLabels: Record<string, string>;
  fieldLabelOverrides: Record<string, Record<string, string>>;
  commonFields: string[];
  rareFields: string[];
}

export type AskChoice = 'never' | 'always' | 'ask';

export interface Settings {
  citekey_pattern: string;
  citekey_auto: boolean;
  default_search_mode: SearchMode;
  fulltext_auto_index: boolean;
  theme: 'system' | 'light' | 'dark';
  density: 'comfortable' | 'compact';
  font_size: number;
  confirm_trash: boolean;
  open_pdf_on_double_click: boolean;
  export_format: 'bibtex' | 'ris' | 'endnote' | 'endnote_xml' | 'csljson';
  bibtex_unicode: boolean;
  export_abstract: boolean;
  import_skip_duplicates: boolean;
  import_tags: AskChoice;
  show_subfolder_items: boolean;
  contact_email: string;
  references_auto: boolean;
  add_fetch_metadata: AskChoice;
  add_find_pdf: AskChoice;
  add_fetch_references: AskChoice;
  openalex_api_key: string;
  semantic_scholar_api_key: string;
  notes_enabled: boolean;
  notes_autosave_ms: number;
  notes_trash_retention: 'never' | '1d' | '1w' | '2w' | '1m' | '6m';
  notes_width: 'readable' | 'full';
  notes_equation_numbers: 'labelled' | 'all';
  notes_math_physics: boolean;
  notes_math_macros: string;
  notes_ai_complete: boolean;
  notes_ai_model: string;
  notes_ai_url: string;
  notes_ai_length: 'line' | 'paragraph';
  annotation_color_highlight: string;
  annotation_color_underline: string;
  annotation_color_text: string;
  annotation_color_box: string;
  annotation_fill_box: string;
  annotation_color_draw: string;
  annotation_snap_words: boolean;
  pdf_open_in: 'tabs' | 'browser';
  backup_interval: 'off' | 'daily' | 'weekly';
  backup_keep: number;
  backup_dir: string;
}

/** Why autocompletion can or cannot make suggestions (GET /api/assist/status, backend/assist). */
export type AssistReason = '' | 'off' | 'unreachable' | 'no_model' | 'unsupported' | 'timeout' | 'error' | 'superseded';
export interface AssistStatus {
  enabled: boolean;
  ok: boolean;
  reason: AssistReason;
  message: string;
  url: string;
  model: string;
  models: string[];      // installed in Ollama
  version: string;       // Ollama's
  fim: boolean;          // the model fills in the middle (sees the text after the cursor)
  thinking: boolean;
}
/** POST /api/assist/complete: the suggestion, or why there is none. */
export interface AssistCompletion { text: string; reason: AssistReason; message?: string }

export interface SettingsResponse {
  settings: Settings;
  defaults: Settings;
  searchModes: Record<SearchMode, string>;
  citekeyPresets: { label: string; pattern: string }[];
}

/* ------------------------------------------------------------------ notes */

/** A note in the notes index (backend/notes/index.py). ``path`` is relative to data/notes. */
export interface NoteInfo {
  path: string;
  id: string | null;
  title: string | null;
  type: string | null;     // front-matter type: 'paper' for a paper note
  paper: string | null;    // the citation key a paper note is about
  author: string | null;
  created: string | null;
  updated: string | null;
  mtime: number;
  tags: string[];
}

/** A change to a note recorded by the server (backend/notes/history.py): Claude's edits through MCP. */
export interface HistoryEntry {
  id: string;
  path: string;
  at: string;
  by: string;              // 'ai'
  summary: string;
  before_hash: string;
  after_hash: string;
}

export interface NoteHistory { path: string; hash: string | null; entries: HistoryEntry[] }

/** GET /api/notes/attachments: images in the notes folder, and the attachment trash. */
export interface NoteAttachments {
  attachments: { name: string; path: string; size: number; modified: string; used_by: string[] }[];
  trash: { trash_id: string; name: string; trashed_at: string; size: number; deletes_at?: string }[];
  retention: Settings['notes_trash_retention'];
}

/** GET /api/notes/note: a note's text, its hash (for hash-checked saves) and its index entry. */
export interface NoteFile { path: string; text: string; hash: string; info: NoteDetails | null }

/** PUT /api/notes/note: the saved note's new hash and index entry. */
export interface NoteSaved { path: string; hash: string; info: NoteDetails | null }

/** A note's index entry with its tags and headings (GET /api/notes/note: ``info``). */
export interface NoteDetails extends NoteInfo {
  headings: { level: number; text: string; line: number }[];
}

/** A link in a note, as the notes index stores it (backend/notes/index.py, table links). */
export interface NoteLink {
  path: string;            // the note containing the link
  kind: 'note' | 'embed' | 'cite' | 'image';
  target: string;
  heading: string | null;
  alias: string | null;
  locator: string | null;
  page: number | null;
  prefix: string | null;
  suppress_author: boolean;
  line: number;
  context: string;
  resolved?: string | null;   // note / embed: the path it goes to
  item?: Omit<CiteEntry, 'key'> | null;   // cite: the library item
}

/** GET /api/notes/links: what a note links to and cites, what does not resolve, and what links to it. */
export interface NoteLinks {
  outgoing: NoteLink[];
  cited: { key: string; item: Omit<CiteEntry, 'key'> | null; count: number; lines: number[] }[];
  unresolved: NoteLink[];
  backlinks: (NoteLink & { source_title: string | null })[];
}

/** A moved note or folder (POST /api/notes/move, /api/notes/folder/move): links that no longer resolve. */
export interface NoteMoved { path: string; broken_links: { path: string; line: number }[] }

/** GET /api/items/<id>/notes: the item's paper notes and the notes that cite it (empty without a citation key). */
export interface ItemNotes {
  key: string;
  paper_notes: { path: string; title: string | null; type: string | null; author: string | null; updated: string | null }[];
  citing: { path: string; line: number; context: string; locator: string; page: number | null;
    source_title: string | null; type: string | null; author: string | null }[];
  suggested_path?: string;
}

/** GET /api/notes/keys: per citation key, its paper note (path) and how many notes cite it. */
export type NoteKeys = Record<string, { paper: string | null; citing: number }>;

export interface NotesListing {
  notes: NoteInfo[];
  folders: string[];
  /** Notes folders given a colour (backend/notes/folder_colors.py): folder path → #rrggbb. */
  folder_colors?: Record<string, string>;
  stats: { notes: number; links: number };
}

/** A PDF of an item, as citations name it ([@key, p. N, att:ID]). */
export interface CitePdf { id: number; url: string; name: string }

/** An item that notes can cite, by citation key (GET /api/notes/cite_index; includes trashed items). */
export interface CiteEntry {
  key: string;
  id: string;
  title: string;
  authors: string;
  year: number | string | null;
  journal: string;
  trashed: boolean;
  has_pdf: boolean;
  pdf: CitePdf | null;     // the first PDF: what a citation without att: opens
  pdfs: CitePdf[];
  note: string | null;     // path of its paper note
}

/* ------------------------------------------------------------------ graph */

/** A node of GET /api/graph (backend/references/graph.py). */
export interface GraphNode {
  id: string;
  kind: 'paper' | 'work' | 'note';
  title: string;
  authors?: string;
  year?: number | string | null;
  doi?: string | null;
  arxiv_id?: string | null;
  item_id?: string;        // papers
  citation_key?: string;   // papers
  item_type?: string;
  folder_ids?: number[];
  tags?: string[];
  has_pdf?: boolean;
  path?: string;           // notes
  note_type?: string | null;
  paper?: string | null;
}

/** cites: paper → paper/work (reference lists); cite: note → paper; link: note → note; about: paper note → paper. */
export type EdgeKind = 'cites' | 'cite' | 'link' | 'about';

export interface GraphEdge { source: string; target: string; kind: EdgeKind }

export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[] }

/* ------------------------------------------------------------------ import / export */

/** An import/export format (backend/formats). */
export interface Format { key: string; label: string; extension: string }

/** The body of a 409 from POST /api/items/from_identifier: the item is already in the library. */
export interface DuplicateFound { error: string; duplicate_of: string; metadata: Partial<Item> }

/** An entry read from an import file (item fields), and the library item it duplicates, if any. */
export type ImportEntry = Partial<Item> & { duplicate_of?: string | null };

export interface ImportPreview { format: string; label: string; entries: ImportEntry[] }

/* ------------------------------------------------------------------ references */

/** A work an item cites (backend/references), matched to a library item when it is one. */
export interface Reference {
  item_id: string;
  position: number;
  doi: string | null;
  arxiv_id: string | null;
  openalex_id: string | null;
  s2_id: string | null;
  title: string | null;
  authors: string | null;
  year: number | null;
  venue: string | null;
  raw: string | null;
  library_id: string | null;
}

export interface ReferenceFetch {
  item_id: string;
  source: 'semanticscholar' | 'openalex' | 'crossref' | 'none';
  fetched_at: string;
  doi: string | null;
  arxiv_id: string | null;
  count: number;
  error: string | null;
}

/** GET/POST /api/items/<id>/references. */
export interface ItemReferences {
  fetch: ReferenceFetch | null;
  references: Reference[];
  cited_by: { id: string; title: string | null; authors: string | null; year: number | string | null }[];
  can_fetch: boolean;
  queued: boolean;
}

/** POST /api/items/<id>/lookup: the item as it is, and what the metadata services say. */
export interface MetadataLookup { current: Item; fetched: Partial<Item> }

/** POST /api/items/<id>/find_pdf. */
export interface FindPdfResult { status: 'success' | 'not_found' | 'error'; message?: string; attachment?: Attachment; source?: string }

/** POST /api/items/from_pdf: one entry per PDF; ``note`` says why no metadata was found. */
export interface PdfImport { item: Item; identifier: { doi?: string; arxiv?: string } | null; note?: string | null }

/* ------------------------------------------------------------------ misc */

/** GET /api/stats: library totals and the full-text indexer's progress. */
export interface Stats {
  counts: Counts;
  attachments: number;
  fulltext: { documents: number; chars: number; pages: number; bytes: number; fts5: boolean };
  indexer: { running: boolean; total: number; done: number; current: string; last_run: string | null; errors: number };
  data_dir: string;
}

/** A backup archive (backend/backup.py). ``items`` and ``notes`` come from its manifest. */
export interface Backup {
  name: string;
  size: number;
  created: string;
  reason: string | null;   // 'manual', 'scheduled', or what it was made before ('import', …)
  items: number | null;
  notes: number | null;
  damaged?: boolean;       // the manifest cannot be read
}

/** GET /api/backups. */
export interface BackupStatus {
  dir: string;
  backups: Backup[];
  last: Backup | null;
  error: string | null;
  interval: Settings['backup_interval'];
  keep: number;
}

/** A setting a plugin declares in its manifest (backend/plugins/manifest.py). */
export interface PluginSetting {
  key: string;
  type: 'bool' | 'int' | 'float' | 'text' | 'choice' | 'multi';
  label: string;
  default: unknown;
  description?: string;
  options?: [string, string][];
  min?: number;
  max?: number;
}

/** A plugin as Settings → Plugins shows it (backend/plugins/host.py, info). */
export interface PluginInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  source: 'bundled' | 'user' | 'dev';
  folder: string;
  replaces_bundled: boolean;
  enabled: boolean;
  running: boolean;
  status: 'on' | 'off' | 'failed' | 'incompatible' | 'invalid' | 'safe-mode';
  problem: string | null;
  failed_at: string | null;
  remote_mcp: boolean;
  api?: string;
  permissions?: { network: string[]; library: string[]; mcp: boolean; python?: string[] };
  parts?: string[];
  settings?: PluginSetting[];
  values?: Record<string, unknown>;
  tools?: string[];
  routes?: { rule: string; methods: string[] }[];
  events?: string[];
  /** Its scheduled work (while running) and the jobs waiting. */
  schedules?: { name: string; rule: string }[];
  jobs?: number;
  /** Paths in the plugin's folder (from the manifest). */
  frontend?: string | null;
  styles?: string | null;
  /** When it started (while running): the app loads its frontend again when this changes. */
  started?: string;
}

/** GET /api/plugin-host. */
export interface PluginsOverview {
  plugins: PluginInfo[];
  safe_mode: boolean;
  dir: string;
  api: string;
}

/** A paired device (backend/access/devices.py). */
export interface Device {
  id: string;
  name: string;
  user_agent: string | null;
  created: string;
  last_seen: string | null;
  last_addr: string | null;
}

/** GET /api/devices: the paired devices, and whether and where other devices can reach this server. */
export interface DevicesOverview {
  lan: boolean;
  this_computer: boolean;
  current: string | null;  // the device asking (null on this computer)
  devices: Device[];
  addresses: { label: string; url: string }[];
}

/** POST /api/devices/pairing: a one-time code, as a link (and QR code SVG) per address. */
export interface Pairing {
  expires: number;         // Unix time
  links: { label: string; url: string; qr: string | null }[];
}

/** A passkey that signs devices in without pairing (backend/access/passkeys.py). */
export interface Passkey {
  id: string;
  name: string;
  rp_id: string;           // the host name it works on
  created: string;
  last_used: string | null;
  last_addr: string | null;
}

/** GET /api/passkeys: the passkeys, and why this page cannot add one (``unavailable``), if it cannot. */
export interface PasskeysOverview {
  this_computer: boolean;
  rp_id: string | null;
  unavailable: string | null;
  passkeys: Passkey[];
}

/** An access token for an AI app using the MCP endpoint (backend/access/tokens.py). */
export interface AccessToken {
  id: string;
  name: string;
  created: string;
  last_used: string | null;
  last_addr: string | null;
}

/** GET /api/mcp/tokens: the tokens, and the MCP endpoint's URLs. */
export interface AccessTokensOverview {
  this_computer: boolean;
  lan: boolean;
  endpoints: { label: string; url: string }[];
  tokens: AccessToken[];
}

/** GET /api/changes: what changed since ``seq`` (backend/changes.py). */
export interface ChangeFeed {
  epoch: string;
  seq: number;
  changes: ('library' | 'annotations' | 'notes')[];
  reset: boolean;          // the server restarted: everything counts as changed
}

/** GET /api/notes/rename_plan: what renaming a citation key would change in the notes. */
export interface RenamePlan {
  old: string;
  new: string;
  notes: { path: string; title: string; changes: { line: number; before: string; after: string }[] }[];
  count: number;
  moves: { from: string; to: string }[];
}

/* ------------------------------------------------------------------ tasks (docs/tasks.md) */
export type TaskStatus = 'open' | 'done';

/** A task as /api/tasks returns it (a record in library.db). */
export interface Task {
  id: string;
  parent_id: string | null;
  position: number;
  title: string;            // one line of inline Markdown
  body: string;             // Markdown
  status: TaskStatus;
  due: string | null;       // YYYY-MM-DD
  created_at: string;
  updated_at: string;
  done_at: string | null;
  created_by: 'human' | 'ai';
  updated_by: 'human' | 'ai';
  deleted_at: string | null;
  deleted_batch: string | null;
}

/** A note that shows a task with a {{task:id}} line. */
export interface TaskRef { path: string; title: string | null; line: number; heading: string | null }

export interface TasksOverview { tasks: Task[]; refs: Record<string, TaskRef[]>; trash: number }

export interface TaskHistoryEntry {
  id: number;
  task_id: string;
  at: string;
  by: 'human' | 'ai';
  action: 'create' | 'update' | 'move' | 'delete' | 'restore';
  before: Partial<Task> | null;
  after: Partial<Task> | null;
  undone: boolean;
}

/** What may be sent to create or change a task. */
export interface TaskChanges {
  title?: string;
  body?: string;
  status?: TaskStatus;
  due?: string | null;
  parent_id?: string | null;
  before?: string | null;
  after?: string | null;
  with_subtasks?: boolean;
  note?: string;
  after_line?: number | null;
}
