// Application state with a tiny pub/sub. Views subscribe to the slices they
// render; actions (in actions.ts) mutate state and emit events.
import type { Counts, Folder, Hits, Item, NoteKeys, Schema, SearchMode, Settings, SettingsResponse, Sidebar, Stats } from './types.js';
import { storage, store as persist } from './util.js';

type Listener = (payload: any) => void;
const listeners: Record<string, Listener[]> = {};

export function on(event: string, fn: Listener): void { (listeners[event] ||= []).push(fn); }
export function emit(event: string, payload?: unknown): void { (listeners[event] || []).forEach((fn) => fn(payload)); }

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

function loadColumns(): Column[] {
  const saved = storage<Column[] | null>('zc.columns', null);
  const byId = Object.fromEntries(COLUMNS.map((c) => [c.id, c]));
  const cols: Column[] = [];
  if (Array.isArray(saved)) {
    for (const s of saved) if (byId[s.id]) cols.push({ id: s.id, width: s.width || byId[s.id].width, visible: !!s.visible });
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

/** The citation keys notes use: whether the item has a paper note, and how many notes cite it. */
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
  view: 'library',          // 'library' | 'trash'
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
  expanded: new Set(),
  indexer: null,
  noteKeys: {},             // citation key -> { paper: path of its paper note | null, citing: n notes }
};

export function saveColumns() { persist('zc.columns', state.columns); }
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

export const byId = (id: string | null | undefined): Item | undefined => state.items.find((i) => i.id === id);
export const selectedItems = (): Item[] => state.items.filter((i) => state.selection.has(i.id));

export function itemTypeLabel(key: string | null | undefined): string {
  const t = state.schema?.itemTypes.find((x) => x.key === key);
  return t ? t.label : (key || 'Journal Article');
}

export function sortedItems(): Item[] {
  const { col, dir } = state.sort;
  const def = COLUMNS.find((c) => c.id === col) || COLUMNS[1];
  const key = def.sortKey;
  const mul = dir === 'asc' ? 1 : -1;
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
