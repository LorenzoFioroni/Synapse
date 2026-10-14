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
