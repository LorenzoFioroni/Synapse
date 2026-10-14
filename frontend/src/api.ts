// Thin client for the JSON API. Every call throws an Error with a readable
// message on failure, so callers can show it in a toast. Response types are in types.ts.
import type {
  Attachment, BackupStatus, ChangeFeed, CiteEntry, DevicesOverview, FindPdfResult, Folder, Format, GraphData, HistoryEntry, ImportEntry, ImportPreview, Item,
  ItemList, ItemNotes, ItemReferences, ItemsByIds, MetadataLookup, NoteAttachments, NoteFile, NoteHistory, NoteKeys, NoteLinks, NoteMoved, NoteSaved, NotesListing, Pairing,
  PdfImport, RenamePlan, Schema, Settings, SettingsResponse, SmartFolder, Stats, Tag,
} from './types.js';

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data?: unknown) { super(message); this.status = status; this.data = data; }
}

// Which open page this is, so the change feed does not report a page's own edits back to it.
export const CLIENT_ID = (globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`).replace(/-/g, '');

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

/** A JSON request; resolves to the parsed answer (typed by the caller as T). */
async function request<T = unknown>(method: Method, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-Synapse-Client': CLIENT_ID };
  const opts: RequestInit = { method, headers };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new ApiError('Cannot reach the server. Is it still running?', 0);
  }
  let data: any = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && data && data.paired === false) {
    // This device was signed out (Settings → Devices): the page itself now explains how to pair again.
    setTimeout(() => location.reload(), 1500);
  }
  if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status, data);
  return data as T;
}

const qs = (params: Record<string, unknown>) => new URLSearchParams(
  Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => [k, String(v)]),
).toString();

export const api = {
  schema: () => request<Schema>('GET', '/api/schema'),
  stats: () => request<Stats>('GET', '/api/stats'),

  // lean: no abstracts or empty fields in the list (the item pane fetches the whole item)
  listItems: ({ view, q, mode, tags }: { view: string; q?: string; mode?: string; tags?: string[] }) => request<ItemList>('GET', `/api/items?${qs({ view, q, mode, tags: tags && tags.join(','), lean: 1 })}`),
  itemsByIds: (ids: string[]) => request<ItemsByIds>('GET', `/api/items?ids=${encodeURIComponent(ids.join(','))}`),   // [] → just counts and sidebar
  getItem: (id: string) => request<Item>('GET', `/api/items/${id}`),
  createItem: (data: Partial<Item> = {}) => request<Item>('POST', '/api/items', data),
  updateItem: (id: string, data: Partial<Item>) => request<Item>('PATCH', `/api/items/${id}`, data),
  trash: (ids: string[]) => request('POST', '/api/items/trash', { ids }),
  restore: (ids: string[]) => request('POST', '/api/items/restore', { ids }),
  deleteForever: (ids: string[]) => request('POST', '/api/items/delete', { ids }),
  emptyTrash: () => request('POST', '/api/trash/empty'),
  regenerateCitekeys: (ids: string[]) => request<{ keys: Record<string, string> }>('POST', '/api/items/citekeys', { ids }),

  lookup: (id: string) => request<MetadataLookup>('POST', `/api/items/${id}/lookup`),
  fromIdentifier: (identifier: string, allowDuplicate = false, folderId: number | null = null, { fetchReferences }: { fetchReferences?: boolean } = {}) =>
    request<Item>('POST', '/api/items/from_identifier', { identifier, allow_duplicate: allowDuplicate, folder_id: folderId, fetch_references: fetchReferences }),
  fromPdfs: (files: Iterable<File>, folderId: number | null = null) => {
    const fd = new FormData(); [...files].forEach((f) => fd.append('file', f));
    if (folderId) fd.append('folder_id', String(folderId));
    return request<{ created: PdfImport[] }>('POST', '/api/items/from_pdf', fd);
  },
  findPdf: (id: string) => request<FindPdfResult>('POST', `/api/items/${id}/find_pdf`),

  uploadAttachments: (id: string, files: Iterable<File>) => { const fd = new FormData(); [...files].forEach((f) => fd.append('file', f)); return request('POST', `/api/items/${id}/attachments`, fd); },
  renameAttachment: (attId: number, name: string) => request<Attachment>('PATCH', `/api/attachments/${attId}`, { name }),
  deleteAttachment: (attId: number) => request('DELETE', `/api/attachments/${attId}`),

  getSettings: () => request<SettingsResponse>('GET', '/api/settings'),
  saveSettings: (changes: Partial<Settings>) => request<{ settings: Settings }>('PUT', '/api/settings', changes),
  resetSettings: () => request<{ settings: Settings }>('POST', '/api/settings/reset'),
  changes: (since?: number | null, epoch?: string | null) => request<ChangeFeed>('GET', `/api/changes?${qs({ since, epoch })}`),
  devices: () => request<DevicesOverview>('GET', '/api/devices'),
  newPairing: () => request<Pairing>('POST', '/api/devices/pairing'),
  renameDevice: (id: string, name: string) => request('PATCH', `/api/devices/${encodeURIComponent(id)}`, { name }),
  removeDevice: (id: string) => request('DELETE', `/api/devices/${encodeURIComponent(id)}`),
  backups: () => request<BackupStatus>('GET', '/api/backups'),
  backupNow: () => request('POST', '/api/backups'),
  restoreBackup: (name: string) => request<BackupStatus & { restored: string; safety_backup: string }>('POST', '/api/backups/restore', { name }),
  citekeyPreview: (pattern: string) => request<{ pattern: string; examples: string[] }>('GET', `/api/settings/citekey_preview?${qs({ pattern })}`),

  formats: () => request<{ formats: Format[] }>('GET', '/api/formats'),
  importPreview: (fileOrText: string | File, format?: string) => {
    if (typeof fileOrText === 'string') return request<ImportPreview>('POST', '/api/import/preview', { text: fileOrText, format });
    const fd = new FormData(); fd.append('file', fileOrText); if (format) fd.append('format', format);
    return request<ImportPreview>('POST', '/api/import/preview', fd);
  },
  importCommit: (entries: ImportEntry[], skipDuplicates: boolean, folderId: number | null = null, { keywordsAsTags }: { keywordsAsTags?: boolean } = {}) =>
    request<{ created: string[]; skipped: number }>('POST', '/api/import/commit', { entries, skip_duplicates: skipDuplicates, folder_id: folderId, keywords_as_tags: keywordsAsTags }),
  exportText: (format: string, ids?: string[] | null) => request<{ text: string; format: string; count: number }>('GET', `/api/export?${qs({ format, ids: ids && ids.join(','), download: 0 })}`),
  exportUrl: (format: string, ids?: string[] | null) => `/api/export?${qs({ format, ids: ids && ids.join(',') })}`,

  rebuildFulltext: () => request('POST', '/api/fulltext/rebuild'),

  // folders · tags · smart folders
  createFolder: (name: string, parentId: number | null = null) => request<Folder>('POST', '/api/folders', { name, parent_id: parentId }),
  updateFolder: (id: number, data: { name?: string; parent_id?: number | null; position?: number }) => request('PATCH', `/api/folders/${id}`, data),
  deleteFolder: (id: number) => request('DELETE', `/api/folders/${id}`),
  changeFolders: (ids: string[], add: number[] = [], remove: number[] = []) => request('POST', '/api/items/folders', { ids, add, remove }),
  tagItems: (ids: string[], add: string[] = [], remove: string[] = []) => request('POST', '/api/items/tags', { ids, add, remove }),
  updateTag: (id: number, data: { name?: string; color?: string | null }) => request<Tag>('PATCH', `/api/tags/${id}`, data),
  deleteTag: (id: number) => request('DELETE', `/api/tags/${id}`),
  createSmart: (name: string, query: string, mode: string) => request<SmartFolder>('POST', '/api/smart', { name, query, mode }),
  updateSmart: (id: number, data: { name?: string; query?: string; mode?: string; position?: number }) => request<SmartFolder>('PATCH', `/api/smart/${id}`, data),
  deleteSmart: (id: number) => request('DELETE', `/api/smart/${id}`),
  searchPreview: (q: string, mode: string) => request<{ count: number; sample: Pick<Item, 'id' | 'title' | 'year' | 'authors'>[] }>('GET', `/api/search/preview?${qs({ q, mode })}`),

  // notes (addressed by their path inside the notes folder)
  notes: () => request<NotesListing>('GET', '/api/notes'),
  getNote: (path: string) => request<NoteFile>('GET', `/api/notes/note?${qs({ path })}`),
  createNote: (data: Record<string, unknown>) => request<Omit<NoteFile, 'info'>>('POST', '/api/notes/note', data),
  noteHistory: (path: string) => request<NoteHistory>('GET', `/api/notes/history?${qs({ path })}`),
  noteVersion: (id: string) => request<HistoryEntry & { before: string }>('GET', `/api/notes/history/version?${qs({ id })}`),
  saveNote: (path: string, text: string, hash: string | null) => request<NoteSaved>('PUT', '/api/notes/note', { path, text, hash }),
  recreateNote: (path: string, text: string) => request<NoteSaved>('PUT', '/api/notes/note', { path, text, create: true }),
  moveNote: (path: string, to: string, hash?: string | null) => request<NoteMoved>('POST', '/api/notes/move', { path, to, hash }),
  newNoteFolder: (path: string) => request<{ path: string }>('POST', '/api/notes/folder', { path }),
  trashNotes: (paths: string[], folders: string[] = []) => request<{ trash_id: string; paths: string[] }>('POST', '/api/notes/trash', { paths, folders }),
  moveNoteFolder: (path: string, to: string) => request<NoteMoved>('POST', '/api/notes/folder/move', { path, to }),
  restoreNoteBatch: (trashId: string) => request<{ paths: string[]; skipped: string[] }>('POST', '/api/notes/restore', { trash_id: trashId }),
  noteKeys: () => request<{ keys: NoteKeys }>('GET', '/api/notes/keys'),
  // reference lists (citation graph)
  itemReferences: (id: string) => request<ItemReferences>('GET', `/api/items/${id}/references`),
  fetchReferences: (id: string) => request<ItemReferences>('POST', `/api/items/${id}/references`),
  queueReferences: (ids: string[]) => request('POST', '/api/references/queue', { ids }),
  citationGraph: () => request('GET', '/api/graph/citations'),
  graph: () => request<GraphData>('GET', '/api/graph'),
  noteTrash: () => request<{ trash: { trash_id: string; path: string; trashed_at?: string; deletes_at?: string }[]; retention: Settings['notes_trash_retention'] }>('GET', '/api/notes/trash'),
  noteAttachments: () => request<NoteAttachments>('GET', '/api/notes/attachments'),
  restoreNoteAttachment: (trashId: string, name: string) => request('POST', '/api/notes/attachments/restore', { trash_id: trashId, name }),
  deleteNoteAttachment: (trashId: string, name: string) => request('POST', '/api/notes/attachments/delete', { trash_id: trashId, name }),
  citeIndex: () => request<{ items: CiteEntry[] }>('GET', '/api/notes/cite_index'),
  renamePlan: (old: string, nu: string) => request<RenamePlan>('GET', `/api/notes/rename_plan?${qs({ old, new: nu })}`),
  itemNotes: (id: string) => request<ItemNotes>('GET', `/api/items/${id}/notes`),
  restoreNote: (trashId: string, path: string) => request('POST', '/api/notes/restore', { trash_id: trashId, path }),
  noteLinks: (path: string) => request<NoteLinks>('GET', `/api/notes/links?${qs({ path })}`),
  searchNotes: (q: string) => request<{ results: { path: string; title: string | null; snippet: string }[] }>('GET', `/api/notes/search?${qs({ q })}`),
  rescanNotes: () => request<{ added: number; changed: number; removed: number }>('POST', '/api/notes/rescan'),
  uploadNoteImage: (file: File) => { const fd = new FormData(); fd.append('file', file); return request<{ path: string; markdown: string }>('POST', '/api/notes/attachments', fd); },
};

export function fileUrl(att: Pick<Attachment, 'id' | 'url'>): string {
  return /^https?:/.test(att.url) ? `/api/attachments/${att.id}/remote` : `/pdfs/${encodeURIComponent(att.url)}`;
}

/** The viewer's URL for a PDF, optionally at a page and at a spot on it: `at` is {q} (words to
 *  find on the page) or {rect: "x,y,w,h"} (a region, as fractions of the page) — see citeTarget.ts. */
export function viewerUrl(item: Pick<Item, 'id' | 'title'>, att: Pick<Attachment, 'id' | 'url'>, page?: number | null, at: ViewerSpot = {}): string {
  const p = new URLSearchParams({ file: fileUrl(att), paper_id: item.id, title: item.title || 'PDF' });
  if (att.id) p.set('att', String(att.id));   // annotations belong to this attachment
  if (page) p.set('page', String(page));
  if (at.q) p.set('q', at.q);
  if (at.rect) p.set('rect', at.rect);
  return `/viewer.html?${p}`;
}

/** Where to go in a PDF: words to find on the page, or a region "x,y,w,h" (fractions of the page). */
export interface ViewerSpot { q?: string; rect?: string }

/** A viewer tab; viewer.html defines zcViewerGoto so other tabs can point it somewhere else. */
type ViewerWindow = Window & { zcViewerGoto?: (to: { page?: number | null; q?: string; rect?: string }) => void };

export interface PdfTab { win: Window | null; reuse: boolean }

/* One viewer tab per PDF: opening a PDF that already has a tab (opened from this app tab) scrolls
 * that tab to the new spot and brings it forward instead of opening another one. `newTab` (Cmd/
 * Ctrl+click on a citation) always opens a fresh tab, which then becomes the one reused. */
const pdfTabs = new Map<string, Window>();   // fileUrl → window

const tabName = (att: Pick<Attachment, 'id' | 'url'>) => `zc-pdf:${fileUrl(att)}`;

/** The live viewer tab already showing this PDF, if any. */
function viewerTab(att: Pick<Attachment, 'id' | 'url'>): ViewerWindow | null {
  const w = pdfTabs.get(fileUrl(att)) as ViewerWindow | undefined;
  try {
    if (w && !w.closed && typeof w.zcViewerGoto === 'function'
        && new URLSearchParams(w.location.search).get('file') === fileUrl(att)) return w;
  } catch { /* navigated elsewhere */ }
  pdfTabs.delete(fileUrl(att));
  return null;
}

/** Point an existing viewer tab at a spot and show it. */
function reuseTab(w: ViewerWindow, page: number | null | undefined, at: ViewerSpot): void {
  w.zcViewerGoto!({ page, q: at.q, rect: at.rect });
  w.focus();
}

/**
 * A tab for this PDF, taken synchronously inside the click (popup blockers want that) for callers
 * that only know where to go after an await; finish with `pointPdfTab`.
 */
export function pdfTabFor(att: Pick<Attachment, 'id' | 'url'>, { newTab = false } = {}): PdfTab {
  const w = !newTab && viewerTab(att);
  if (w) return { win: w, reuse: true };
  return { win: window.open('about:blank', newTab ? '_blank' : tabName(att)), reuse: false };
}

export function pointPdfTab(tab: PdfTab, item: Pick<Item, 'id' | 'title'>, att: Pick<Attachment, 'id' | 'url'>, page?: number | null, at: ViewerSpot = {}): void {
  if (tab.reuse) {
    const w = viewerTab(att);
    if (w && w === tab.win) return reuseTab(w, page, at);
  }
  const url = viewerUrl(item, att, page, at);
  if (!tab.win) { window.open(url, '_blank'); return; }
  tab.win.location.href = url;
  pdfTabs.set(fileUrl(att), tab.win);
}

/** Open a PDF in the viewer, reusing its tab when it has one (see above). */
export function openAttachment(item: Pick<Item, 'id' | 'title'>, att: Pick<Attachment, 'id' | 'url'>, page?: number | null, at: ViewerSpot = {}, { newTab = false } = {}): void {
  const w = !newTab && viewerTab(att);
  if (w) return reuseTab(w, page, at);
  const win = window.open(viewerUrl(item, att, page, at), newTab ? '_blank' : tabName(att));
  if (win) pdfTabs.set(fileUrl(att), win);
}
