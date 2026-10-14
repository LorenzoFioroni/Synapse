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
  backup_interval: 'off' | 'daily' | 'weekly';
  backup_keep: number;
  backup_dir: string;
}

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
export interface NoteFile { path: string; text: string; hash: string; info: NoteInfo | null }

export interface NotesListing {
  notes: NoteInfo[];
  folders: string[];
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

/** POST /api/items/<id>/find_pdf. */
export interface FindPdfResult { status: 'success' | 'not_found' | 'error'; message?: string; attachment?: Attachment; source?: string }

/** POST /api/items/from_pdf: one entry per PDF; ``note`` says why no metadata was found. */
export interface PdfImport { item: Item; identifier: { doi?: string; arxiv?: string } | null; note?: string | null }

/* ------------------------------------------------------------------ misc */

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
