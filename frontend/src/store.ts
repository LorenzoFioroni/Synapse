// Application state with a tiny pub/sub. Views subscribe to the slices they
// render; actions (in actions.ts) mutate state and emit events.
import type { Counts, Folder, Hits, Item, NoteKeys, PluginField, PluginSource, Schema, SearchMode, Settings, SettingsResponse, Sidebar, Stats } from './types.js';
import { storage, store as persist } from './util.js';

/**
 * Every event the views and actions exchange, with its payload (`void`: none). docs/frontend.md
 * lists who emits and who listens. An event is how a view tells others that something changed,
 * or reaches code it must not import (main.ts handles those).
 */
export interface AppEvents {
  'tasks-page': void;
  items: void;                      // the item list was (re)loaded or changed
  selection: void;
  'item-updated': string;           // one item changed in place (its id)
  sidebar: void;                    // folders, tags or smart folders
  view: void;
  search: void;
  loading: boolean;                 // a list request started or ended
  'tag-filter': void;
  'focus-field': { id: string; field: string };   // put the cursor in a field of the item pane
  columns: void;
  settings: void;
  indexer: void;                    // new full-text indexer status in state.indexer
  workspace: void;                  // Notes, Tasks or a plugin's page opened or closed
  tasks: void;                      // tasks were loaded or changed (plugins/core/tasks/taskData.ts)
  'tasks-count': { open: number; overdue: number } | null;   // for the sidebar; null: turned off
  'open-tasks': { task?: string | null; note?: string | null } | undefined;   // the Tasks page, a task selected or a note's tasks shown
  'notes-count': number | null;     // null: notes are turned off
  'focus-list': void;
  'open-note': { path: string; line?: number | null };
  'open-notes': void;
  'close-notes': void;
  'show-item': string;              // an item id
  'open-import': File | undefined;  // optionally a file to import
  'open-identifier': void;
  'clear-search': void;
  'set-search-mode': SearchMode;
  'open-page': string;              // a page a plugin added (its id)
  'open-view': string;              // a view of the library to show, leaving Notes, Tasks or a page
  'item-fields': void;               // plugins' fields (or their values) changed: the item pane redraws its section
  'export-formats': void;           // running export providers changed
  sources: void;                    // the lists plugins offer (state.sources) or their badges changed
}
export type AppEvent = keyof AppEvents;
/** The arguments after the event name: none for a `void` event, else the payload. */
type Payload<K extends AppEvent> = AppEvents[K] extends void ? [] : undefined extends AppEvents[K] ? [AppEvents[K]?] : [AppEvents[K]];

const listeners: { [K in AppEvent]?: ((payload: AppEvents[K]) => void)[] } = {};

/** Subscribe for the life of the page (views subscribe once, in their init… function). */
export function on<K extends AppEvent>(event: K, fn: (payload: AppEvents[K]) => void): void {
  ((listeners[event] ||= []) as ((payload: AppEvents[K]) => void)[]).push(fn);
}
/** Stop listening (for views that come and go, like a popup). */
export function off<K extends AppEvent>(event: K, fn: (payload: AppEvents[K]) => void): void {
  const list = listeners[event] as ((payload: AppEvents[K]) => void)[] | undefined;
  const i = list ? list.indexOf(fn) : -1;
  if (i >= 0) list!.splice(i, 1);
}
export function emit<K extends AppEvent>(event: K, ...payload: Payload<K>): void {
  for (const fn of (listeners[event] || []) as ((payload: AppEvents[K]) => void)[]) fn(payload[0] as AppEvents[K]);
}

/** A column of the items list: ``sortKey`` is the item field (or derived value) it sorts by. */
export interface ColumnDef {
  id: string;
  label: string;
  short?: string;
  width: number;
  min?: number;
  fixed?: boolean;
  sortKey: string;
}

/** A column as the user arranged it (saved in localStorage). */
export interface Column { id: string; width: number; visible: boolean }

// Column definitions for the items list.
export const COLUMNS: ColumnDef[] = [
  { id: 'attachments', label: 'Attachments', short: '', width: 34, fixed: true, sortKey: 'attachments' },
  { id: 'notes', label: 'Notes', short: '', width: 34, fixed: true, sortKey: 'notes' },
  { id: 'title', label: 'Title', width: 280, min: 160, sortKey: 'title' },
  { id: 'creator', label: 'Creator', width: 150, sortKey: 'authors' },
  { id: 'year', label: 'Year', width: 64, sortKey: 'year' },
  { id: 'item_type', label: 'Item Type', width: 120, sortKey: 'item_type' },
  { id: 'journal', label: 'Publication', width: 150, sortKey: 'journal' },
  { id: 'date_added', label: 'Date Added', width: 110, sortKey: 'date_added' },
  { id: 'date_modified', label: 'Date Modified', width: 110, sortKey: 'date_modified' },
  { id: 'citation_key', label: 'Citation Key', width: 150, sortKey: 'citation_key' },
  { id: 'doi', label: 'DOI', width: 170, sortKey: 'doi' },
  { id: 'publisher', label: 'Publisher', width: 130, sortKey: 'publisher' },
  { id: 'volume', label: 'Volume', width: 70, sortKey: 'volume' },
  { id: 'issue', label: 'Issue', width: 64, sortKey: 'issue' },
  { id: 'pages', label: 'Pages', width: 80, sortKey: 'pages' },
  { id: 'url', label: 'URL', width: 160, sortKey: 'url' },
  { id: 'isbn', label: 'ISBN', width: 120, sortKey: 'isbn' },
  { id: 'issn', label: 'ISSN', width: 100, sortKey: 'issn' },
  { id: 'short_title', label: 'Short Title', width: 140, sortKey: 'short_title' },
  { id: 'language', label: 'Language', width: 80, sortKey: 'language' },
  { id: 'keywords', label: 'Keywords', width: 160, sortKey: 'keywords' },
  { id: 'series', label: 'Series', width: 110, sortKey: 'series' },
  { id: 'edition', label: 'Edition', width: 70, sortKey: 'edition' },
  { id: 'place', label: 'Place', width: 100, sortKey: 'place' },
  { id: 'archive', label: 'Archive', width: 100, sortKey: 'archive' },
  { id: 'archive_id', label: 'Archive ID', width: 120, sortKey: 'archive_id' },
  { id: 'pmid', label: 'PMID', width: 90, sortKey: 'pmid' },
  { id: 'pmcid', label: 'PMCID', width: 100, sortKey: 'pmcid' },
  { id: 'extra', label: 'Extra', width: 140, sortKey: 'extra' },
  { id: 'tags', label: 'Tags', width: 160, sortKey: 'tags' },
  { id: 'folder', label: 'Folder', width: 140, sortKey: 'folder' },
];
const DEFAULT_VISIBLE = ['attachments', 'notes', 'title', 'creator', 'year', 'journal', 'date_added'];
/** Columns of plugins' fields have ids "f:<plugin>.<name>"; they exist while the plugin runs. */
export const FIELD_COL = 'f:';

function loadColumns(): Column[] {
  const saved = storage<Column[] | null>('zc.columns', null);
  const byId = Object.fromEntries(COLUMNS.map((c) => [c.id, c]));
  const cols: Column[] = [];
  if (Array.isArray(saved)) {
    for (const s of saved) {
      if (byId[s.id]) cols.push({ id: s.id, width: s.width || byId[s.id].width, visible: !!s.visible });
      else if (s.id?.startsWith(FIELD_COL)) cols.push({ id: s.id, width: s.width || 100, visible: !!s.visible });   // kept for when its plugin runs
    }
  }
  for (const c of COLUMNS) {
    if (cols.find((x) => x.id === c.id)) continue;
    const col: Column = { id: c.id, width: c.width, visible: DEFAULT_VISIBLE.includes(c.id) };
    const after = c.id === 'notes' ? cols.findIndex((x) => x.id === 'attachments') : -1;   // new marker column: next to the paperclip
    if (after >= 0) cols.splice(after + 1, 0, col); else cols.push(col);
  }
  if (!cols.some((c) => c.id === 'title' && c.visible)) cols.find((c) => c.id === 'title')!.visible = true;
  return cols;
}

/** The library app's state. Views read it; actions.ts changes the list, the view and the selection. */
export interface AppState {
  schema: Schema | null;
  settings: Settings;
  settingsMeta: Partial<SettingsResponse>;
  view: string;            // 'library' | 'trash' | 'duplicates' | 'unfiled' | 'folder:<id>' | 'smart:<id>' | 'notes' | 'graph' …
  query: string;
  mode: SearchMode;
  items: Item[];
  hits: Hits;
  counts: Counts;
  sidebar: Sidebar;
  tagFilter: Set<string>;
  openFolders: Set<number>;
  loading: boolean;
  selection: Set<string>;
  anchor: string | null;
  cursor: string | null;
  sort: { col: string; dir: 'asc' | 'desc' };
  /** The sort of a plugin's list (view "source:…"); null: the plugin's own order. */
  sourceSort: { col: string; dir: 'asc' | 'desc' } | null;
  /** The lists of papers running plugins offer (plugins/host.ts loads them). */
  sources: PluginSource[];
  /** The per-item fields running plugins declare (plugins/host.ts loads them). */
  fieldDefs: PluginField[];
  columns: Column[];
  expanded: Set<string>;
  indexer: Stats | null;     // last /api/stats answer (main.ts polls it)
  noteKeys: NoteKeys;
  order?: string[];        // the list's display order, set by itemlist.ts (the DOM holds only the rows on screen)
}

export const state: AppState = {
  schema: null,
  settings: {} as Settings,   // filled by main.ts before any view is built
  settingsMeta: {},
  view: 'library',          // 'library' | 'trash' | 'duplicates' | 'unfiled' | 'folder:<id>' | 'smart:<id>'
  query: '',
  mode: 'basic',            // search scope
  items: [],                // already filtered by the server
  hits: {},                 // id -> { fulltext: snippetHtml }
  counts: { library: 0, trash: 0, duplicates: 0 },
  sidebar: { folders: [], tags: [], smart: [] },
  tagFilter: new Set(),     // tag names; items must have all of them
  openFolders: new Set(storage<number[]>('zc.openFolders', [])),
  loading: false,
  selection: new Set(),
  anchor: null,             // for shift-click ranges
  cursor: null,             // keyboard focus row
  sort: storage<AppState['sort']>('zc.sort', { col: 'date_added', dir: 'desc' }),
  columns: loadColumns(),
  sourceSort: null,
  sources: [],
  fieldDefs: [],
  expanded: new Set(),
  indexer: null,
  noteKeys: {},             // citation key -> { paper: path of its paper note | null, citing: n notes }
};

export function saveColumns() { persist('zc.columns', state.columns); }

/** The definition of a column: one of COLUMNS, or a plugin's field while that plugin runs. */
export function colDef(id: string): ColumnDef | undefined {
  if (!id.startsWith(FIELD_COL)) return COLUMNS.find((c) => c.id === id);
  const f = state.fieldDefs.find((d) => d.id === id.slice(FIELD_COL.length));
  if (!f) return undefined;
  const narrow = ['int', 'float', 'bool', 'date'].includes(f.type);
  return { id, label: `${f.label} (${f.plugin_name})`, short: f.label, width: narrow ? 90 : 150, sortKey: id };
}

/** The fields' columns after the plugins changed: new ones join hidden at the end. Returns
    whether anything changed. */
export function syncFieldColumns(defs: PluginField[]): boolean {
  const before = JSON.stringify(state.fieldDefs);
  state.fieldDefs = defs;
  let added = false;
  for (const f of defs) {
    const id = FIELD_COL + f.id;
    if (!state.columns.some((c) => c.id === id)) { state.columns.push({ id, width: colDef(id)!.width, visible: false }); added = true; }
  }
  if (added) saveColumns();
  return added || before !== JSON.stringify(defs);
}

/** A field's value as the list shows it. */
export function fieldText(f: PluginField | undefined, v: unknown): string {
  if (v === undefined || v === null) return '';
  if (f?.type === 'bool') return v ? '✓' : '✗';
  if (f?.type === 'int' && typeof v === 'number') return v.toLocaleString();
  if (f?.type === 'float' && typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 10 ? 2 : 1);
  return String(v);
}
export function saveOpenFolders() { persist('zc.openFolders', [...state.openFolders]); }

/** Folder id of the current view, or null. */
export function currentFolderId(): number | null {
  return state.view.startsWith('folder:') ? Number(state.view.slice(7)) : null;
}
export const folderById = (id: number | string | null | undefined): Folder | undefined => state.sidebar.folders.find((f) => f.id === Number(id));
export function folderPath(id: number | string): string[] {
  const names: string[] = [];
  let f = folderById(id), guard = 0;
  while (f && guard++ < 100) { names.unshift(f.name); f = folderById(f.parent_id); }
  return names;
}
/** Folders in display order with their depth, for trees and pickers. */
export function folderTree(): (Folder & { depth: number; hasChildren: boolean })[] {
  const kids: Record<string, Folder[]> = {};
  for (const f of state.sidebar.folders) (kids[f.parent_id ?? 'root'] ||= []).push(f);
  const out: (Folder & { depth: number; hasChildren: boolean })[] = [];
  const walk = (parent: number | 'root', depth: number) => {
    for (const f of (kids[parent] || []).sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))) {
      const children = kids[f.id] || [];
      out.push({ ...f, depth, hasChildren: children.length > 0 });
      walk(f.id, depth + 1);
    }
  };
  walk('root', 0);
  return out;
}
/** Item count of a folder including all its subfolders. */
export function folderTotal(id: number | string): number {
  let n = 0;
  const walk = (fid: number) => {
    const f = folderById(fid);
    if (!f) return;
    n += f.count;
    state.sidebar.folders.filter((c) => c.parent_id === fid).forEach((c) => walk(c.id));
  };
  walk(Number(id));
  return n;
}
/** A folder id plus all ids below it. */
export function folderDescendants(id: number | string): number[] {
  const out = new Set([Number(id)]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const f of state.sidebar.folders) if (f.parent_id !== null && out.has(f.parent_id) && !out.has(f.id)) { out.add(f.id); grew = true; }
  }
  return [...out];
}
/** "Parent / Child" labels of every folder an item is in. */
export function itemFolderPaths(it: Item): string[] {
  return (it.folder_ids || []).filter((id) => folderById(id)).map((id) => folderPath(id).join(' / '))
    .sort((a, b) => a.localeCompare(b));
}
export const tagColor = (name: string): string | null => state.sidebar.tags.find((t) => t.name.toLowerCase() === String(name).toLowerCase())?.color || null;
export function saveSort() { persist('zc.sort', state.sort); }

/** The plugin list the view shows, or undefined. */
export const currentSource = (): PluginSource | undefined =>
  state.view.startsWith('source:') ? state.sources.find((s) => `source:${s.id}` === state.view) : undefined;
/** A paper from a plugin's list that is not in the library. */
export const isOutside = (it: Item | undefined): boolean => !!it?._outside;

/** The sort the list uses in this view: a plugin's list has its own, which starts in the
    plugin's order (null). */
export function viewSort(): { col: string; dir: 'asc' | 'desc' } | null {
  return state.view.startsWith('source:') ? state.sourceSort : state.sort;
}

/** A click on a column's head: sort by it, or turn the direction round. In a plugin's list a
    third click goes back to the plugin's order. */
export function sortBy(col: string): void {
  const first: 'asc' | 'desc' = ['date_added', 'date_modified', 'year'].includes(col) || col.startsWith(FIELD_COL) ? 'desc' : 'asc';
  if (state.view.startsWith('source:')) {
    const s = state.sourceSort;
    state.sourceSort = !s || s.col !== col ? { col, dir: first } : s.dir === first ? { col, dir: first === 'asc' ? 'desc' : 'asc' } : null;
    return;
  }
  if (state.sort.col === col) state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
  else state.sort = { col, dir: first };
  saveSort();
}

export const byId = (id: string | null | undefined): Item | undefined => state.items.find((i) => i.id === id);
export const selectedItems = (): Item[] => state.items.filter((i) => state.selection.has(i.id));

export function itemTypeLabel(key: string | null | undefined): string {
  const t = state.schema?.itemTypes.find((x) => x.key === key);
  return t ? t.label : (key || 'Journal Article');
}

export function sortedItems(): Item[] {
  const sort = viewSort();
  if (!sort) return [...state.items].sort((a, b) => (a._rank ?? 0) - (b._rank ?? 0));   // the plugin's order
  const { col, dir } = sort;
  const def = colDef(col) || COLUMNS.find((c) => c.id === 'title')!;
  const key = def.sortKey;
  const mul = dir === 'asc' ? 1 : -1;
  if (key.startsWith(FIELD_COL) && state.view !== 'duplicates') {
    // a plugin's field: numbers as numbers, items without a value always last
    const fid = key.slice(FIELD_COL.length);
    const fv = (it: Item) => it._f?.[fid];
    return [...state.items].sort((a, b) => {
      const va = fv(a), vb = fv(b);
      const ea = va === undefined || va === null, eb = vb === undefined || vb === null;
      if (ea || eb) return ea === eb ? (a.title || '').localeCompare(b.title || '') : ea ? 1 : -1;
      if (typeof va === 'number' && typeof vb === 'number') return va === vb ? (a.title || '').localeCompare(b.title || '') : (va - vb) * mul;
      return String(va).localeCompare(String(vb)) * mul || (a.title || '').localeCompare(b.title || '');
    });
  }
  const val = (it: Item): string | number => {
    if (key === 'attachments') return (it.attachments || []).length;
    if (key === 'notes') { const n = state.noteKeys[String(it.citation_key)]; return n ? (n.paper ? 1000 : 0) + n.citing : -1; }
    if (key === 'year') return parseInt(String(it.year), 10) || 0;
    if (key === 'item_type') return itemTypeLabel(it.item_type).toLowerCase();
    if (key === 'tags') return (it.tags || []).join(', ').toLowerCase();
    if (key === 'folder') return itemFolderPaths(it).join(', ').toLowerCase();
    const v = it[key];
    return v == null ? '' : String(v).toLowerCase();
  };
  if (state.view === 'duplicates') {
    // keep each group of duplicates together, oldest first
    return [...state.items].sort((a, b) => ((a.dup_group ?? 0) - (b.dup_group ?? 0)) || String(a.date_added).localeCompare(String(b.date_added)));
  }
  return [...state.items].sort((a, b) => {
    const va = val(a), vb = val(b);
    if (va === vb) return (a.title || '').localeCompare(b.title || '');
    if (va === '' || va === 0) return 1;      // empty values always last
    if (vb === '' || vb === 0) return -1;
    return va < vb ? -mul : mul;
  });
}
