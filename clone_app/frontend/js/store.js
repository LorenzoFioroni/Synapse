// Application state with a tiny pub/sub. Views subscribe to the slices they
// render; actions (in actions.js) mutate state and emit events.
import { storage, store as persist } from './util.js';

const listeners = {};

export function on(event, fn) { (listeners[event] ||= []).push(fn); }
export function emit(event, payload) { (listeners[event] || []).forEach((fn) => fn(payload)); }

// Column definitions for the items list. `key` is the item field it shows.
export const COLUMNS = [
  { id: 'attachments', label: 'Attachments', short: '', width: 34, fixed: true, sortKey: 'attachments' },
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
];
const DEFAULT_VISIBLE = ['attachments', 'title', 'creator', 'year', 'journal', 'date_added'];

function loadColumns() {
  const saved = storage('zc.columns', null);
  const byId = Object.fromEntries(COLUMNS.map((c) => [c.id, c]));
  let cols = [];
  if (Array.isArray(saved)) {
    for (const s of saved) if (byId[s.id]) cols.push({ id: s.id, width: s.width || byId[s.id].width, visible: !!s.visible });
  }
  for (const c of COLUMNS) if (!cols.find((x) => x.id === c.id)) cols.push({ id: c.id, width: c.width, visible: DEFAULT_VISIBLE.includes(c.id) });
  if (!cols.some((c) => c.id === 'title' && c.visible)) cols.find((c) => c.id === 'title').visible = true;
  return cols;
}

export const state = {
  schema: null,
  settings: {},
  settingsMeta: {},
  view: 'library',          // 'library' | 'trash'
  query: '',
  mode: 'basic',            // search scope
  items: [],                // already filtered by the server
  hits: {},                 // id -> { fulltext: snippetHtml }
  counts: { library: 0, trash: 0, duplicates: 0 },
  loading: false,
  selection: new Set(),
  anchor: null,             // for shift-click ranges
  cursor: null,             // keyboard focus row
  sort: storage('zc.sort', { col: 'date_added', dir: 'desc' }),
  columns: loadColumns(),
  expanded: new Set(),
  indexer: null,
};

export function saveColumns() { persist('zc.columns', state.columns); }
export function saveSort() { persist('zc.sort', state.sort); }

export const byId = (id) => state.items.find((i) => i.id === id);
export const selectedItems = () => state.items.filter((i) => state.selection.has(i.id));

export function itemTypeLabel(key) {
  const t = state.schema?.itemTypes.find((x) => x.key === key);
  return t ? t.label : (key || 'Journal Article');
}

export function sortedItems() {
  const { col, dir } = state.sort;
  const def = COLUMNS.find((c) => c.id === col) || COLUMNS[1];
  const key = def.sortKey;
  const mul = dir === 'asc' ? 1 : -1;
  const val = (it) => {
    if (key === 'attachments') return (it.attachments || []).length;
    if (key === 'year') return parseInt(it.year, 10) || 0;
    if (key === 'item_type') return itemTypeLabel(it.item_type).toLowerCase();
    const v = it[key];
    return v == null ? '' : String(v).toLowerCase();
  };
  if (state.view === 'duplicates') {
    // keep each group of duplicates together, oldest first
    return [...state.items].sort((a, b) => (a.dup_group - b.dup_group) || String(a.date_added).localeCompare(String(b.date_added)));
  }
  return [...state.items].sort((a, b) => {
    const va = val(a), vb = val(b);
    if (va === vb) return (a.title || '').localeCompare(b.title || '');
    if (va === '' || va === 0) return 1;      // empty values always last
    if (vb === '' || vb === 0) return -1;
    return va < vb ? -mul : mul;
  });
}
