// Thin client for the JSON API. Every call throws an Error with a readable
// message on failure, so callers can show it in a toast. Response types are in types.ts.
import type {
  AccessToken, AccessTokensOverview, Attachment, BackupStatus, ChangeFeed, CiteEntry, DevicesOverview, FindPdfResult, Folder, Format, HistoryDiff, HistoryLog, HistoryStatus, HistoryVersion, ImportEntry, ImportPreview, Item, RenamedKey,
  ItemList, ItemNotes, ItemReferences, ItemsByIds, MetadataLookup, Milestone, NoteAttachments, NoteFile, NoteHistory, NoteKeys, NoteLinks, NoteMoved, NoteSaved, NotesListing, Pairing,
  ItemFieldValue, Passkey, PasskeysOverview, PdfImport, PluginField, PluginInfo, PluginSource, PluginsOverview, RenamePlan, Schema, Settings, SettingsResponse, SmartFolder, Stats, Tag, Task,
  TaskChanges, TaskHistoryEntry, TaskRef, TasksOverview, UpdaterState, UpdateStatus, RemoteBackupSetup, RemoteBackupConfiguration, Backup,
} from './types.js';
import { openNewWindow, openPageInNewWindow } from './windows.js';

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data?: unknown) { super(message); this.status = status; this.data = data; }
}

// Which open page this is, so the change feed does not report a page's own edits back to it.
export const CLIENT_ID = (globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`).replace(/-/g, '');

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

/** A JSON request; resolves to the parsed answer (typed by the caller as T). */
async function request<T = unknown>(method: Method, url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { 'X-Synapse-Client': CLIENT_ID };
  const opts: RequestInit = { method, headers, signal };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    if (signal?.aborted) throw e;   // the caller cancelled it
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
  updatePluginItem: (pid: string, id: string, changes: Record<string, unknown>) => request<Item>('PATCH', `/api/plugin-host/${encodeURIComponent(pid)}/items/${encodeURIComponent(id)}`, changes),
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
  cancelPairing: () => request('DELETE', '/api/devices/pairing'),
  renameDevice: (id: string, name: string) => request('PATCH', `/api/devices/${encodeURIComponent(id)}`, { name }),
  removeDevice: (id: string) => request('DELETE', `/api/devices/${encodeURIComponent(id)}`),

  passkeys: () => request<PasskeysOverview>('GET', '/api/passkeys'),
  passkeyOptions: (name: string) => request<Record<string, any>>('POST', '/api/passkeys/register/options', { name }),
  addPasskey: (credential: Record<string, unknown>) => request<{ passkey: Passkey }>('POST', '/api/passkeys/register', { credential }),
  removePasskey: (id: string) => request('DELETE', `/api/passkeys/${encodeURIComponent(id)}`),
  confirmOptions: () => request<Record<string, any>>('POST', '/api/passkeys/confirm/options'),
  confirm: (credential: Record<string, unknown>) => request<{ until: number }>('POST', '/api/passkeys/confirm', { credential }),

  accessTokens: () => request<AccessTokensOverview>('GET', '/api/mcp/tokens'),
  newAccessToken: (name: string, scope: 'mcp' | 'connector' = 'mcp') => request<{ token: string; entry: AccessToken }>('POST', '/api/mcp/tokens', { name, scope }),
  removeAccessToken: (id: string) => request('DELETE', `/api/mcp/tokens/${encodeURIComponent(id)}`),
  plugins: () => request<PluginsOverview>('GET', '/api/plugin-host'),
  pluginFields: () => request<{ fields: PluginField[] }>('GET', '/api/plugin-fields'),
  pluginSources: () => request<{ sources: PluginSource[] }>('GET', '/api/plugin-sources'),
  /** Add a paper from a plugin's list to the library (201; 200 with duplicate: true when it is there already). */
  addOutside: (paper: Item, folderId: number | null = null) =>
    request<{ item: Item; duplicate: boolean }>('POST', '/api/outside/add', { paper, folder_id: folderId }),
  itemFields: (id: string) => request<{ fields: ItemFieldValue[] }>('GET', `/api/items/${encodeURIComponent(id)}/fields`),
  /** A plugin's own route (/api/plugins/<id><path>), for its frontend (plugins/host.ts). */
  pluginRoute: <T = unknown>(id: string, method: Method, path: string, body?: unknown, query?: Record<string, unknown>, signal?: AbortSignal) => {
    const q = query ? qs(query) : '';
    return request<T>(method, `/api/plugins/${encodeURIComponent(id)}${path.startsWith('/') ? path : `/${path}`}${q ? `?${q}` : ''}`, body, signal);
  },
  enablePlugin: (id: string) => request<{ plugin: PluginInfo }>('POST', `/api/plugin-host/${encodeURIComponent(id)}/enable`),
  disablePlugin: (id: string) => request<{ plugin: PluginInfo }>('POST', `/api/plugin-host/${encodeURIComponent(id)}/disable`),
  restartPlugin: (id: string) => request<{ plugin: PluginInfo }>('POST', `/api/plugin-host/${encodeURIComponent(id)}/restart`),
  setPluginSettings: (id: string, values: Record<string, unknown>) => request<{ values: Record<string, unknown> }>('PUT', `/api/plugin-host/${encodeURIComponent(id)}/settings`, values),
  setPluginRemoteMcp: (id: string, on: boolean) => request<{ plugin: PluginInfo }>('PUT', `/api/plugin-host/${encodeURIComponent(id)}/remote-mcp`, { on }),
  pluginLog: (id: string) => request<{ lines: string[] }>('GET', `/api/plugin-host/${encodeURIComponent(id)}/log`),
  backups: () => request<BackupStatus>('GET', '/api/backups'),
  backupNow: () => request('POST', '/api/backups'),
  restoreBackup: (name: string, source: 'local' | 'remote' = 'local') => request<BackupStatus & { restored: string; safety_backup: string }>('POST', '/api/backups/restore', { name, source }),
  remoteBackupSetup: () => request<RemoteBackupSetup>('GET', '/api/backups/remote'),
  saveRemoteBackup: (data: { provider: string; folder: string; namespace?: string; options: Record<string, string>; password_obscured?: boolean }) => request<{ configuration: RemoteBackupConfiguration }>('PUT', '/api/backups/remote', data),
  remoteBackupSets: () => request<{ sets: string[] }>('GET', '/api/backups/remote/sets'),
  testRemoteBackup: () => request('POST', '/api/backups/remote/test'),
  authorizeRemoteBackup: () => request<{ url: string }>('POST', '/api/backups/remote/authorize'),
  remoteBackups: () => request<{ backups: Backup[] }>('GET', '/api/backups/remote/archives'),
  retryRemoteBackups: () => request('POST', '/api/backups/remote/retry'),
  // updates of Synapse itself (Settings → Updates)
  updates: (refresh = false) => request<UpdateStatus>('GET', refresh ? '/api/updates?refresh=1' : '/api/updates'),
  installUpdate: () => request<UpdateStatus & { started: UpdaterState }>('POST', '/api/updates/install'),
  // autocompletion in notes (a model in Ollama on this computer)
  citekeyPreview: (pattern: string) => request<{ pattern: string; examples: string[] }>('GET', `/api/settings/citekey_preview?${qs({ pattern })}`),

  formats: () => request<{ formats: Format[]; exports: Format[] }>('GET', '/api/formats'),
  importPreview: (fileOrText: string | File, format?: string) => {
    if (typeof fileOrText === 'string') return request<ImportPreview>('POST', '/api/import/preview', { text: fileOrText, format });
    const fd = new FormData(); fd.append('file', fileOrText); if (format) fd.append('format', format);
    return request<ImportPreview>('POST', '/api/import/preview', fd);
  },
  importCommit: (entries: ImportEntry[], skipDuplicates: boolean, folderId: number | null = null, { keywordsAsTags }: { keywordsAsTags?: boolean } = {}) =>
    request<{ created: string[]; skipped: number; renamed_keys?: RenamedKey[] }>('POST', '/api/import/commit', { entries, skip_duplicates: skipDuplicates, folder_id: folderId, keywords_as_tags: keywordsAsTags }),
  clipboardBibtex: (ids: string[]) => request<{ text: string; count: number }>('POST', '/api/clipboard/bibtex', { ids }),
  exportText: (format: string, ids?: string[] | null) => request<{ text: string; format: string; count: number }>('GET', `/api/export?${qs({ format, ids: ids && ids.join(','), download: 0 })}`),
  exportUrl: (format: string, ids?: string[] | null) => `/api/export?${qs({ format, ids: ids && ids.join(',') })}`,

  rebuildFulltext: () => request('POST', '/api/fulltext/rebuild'),
  syncFulltext: () => request('POST', '/api/fulltext/sync'),

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
  /** The note, or only its hash when it still has ``hash`` (cheap enough to ask every few seconds). */
  checkNote: (path: string, hash: string) =>
    request<NoteFile | { path: string; hash: string; unchanged: true }>('GET', `/api/notes/note?${qs({ path, unless: hash })}`),
  createNote: (data: Record<string, unknown>) => request<Omit<NoteFile, 'info'>>('POST', '/api/notes/note', data),
  noteHistory: (path: string, limit?: number) => request<NoteHistory>('GET', `/api/notes/history?${qs({ path, limit })}`),
  noteVersion: (id: string, path?: string, from?: string) => request<HistoryVersion>('GET', `/api/notes/history/version?${qs({ id, path, from })}`),
  historyStatus: () => request<HistoryStatus>('GET', '/api/notes/history/status'),
  historyLog: (limit = 100, skip = 0) => request<HistoryLog>('GET', `/api/notes/history/log?${qs({ limit, skip })}`),
  historyDiff: (from: string, to: string) => request<HistoryDiff>('GET', `/api/notes/history/diff?${qs({ from, to })}`),
  historyCheckpoint: (path?: string, restoredFrom?: string) =>
    request<{ commits: string[] }>('POST', '/api/notes/history/checkpoint', { path, restored_from: restoredFrom }),
  addMilestone: (label: string) => request<Milestone>('POST', '/api/notes/history/milestones', { label }),
  renameMilestone: (id: string, label: string) => request<Milestone>('PUT', `/api/notes/history/milestones/${encodeURIComponent(id)}`, { label }),
  deleteMilestone: (id: string) => request('DELETE', `/api/notes/history/milestones/${encodeURIComponent(id)}`),
  restoreMilestone: (id: string) => request<{ restored: string[]; trashed: string[]; trash_id: string | null }>('POST', `/api/notes/history/milestones/${encodeURIComponent(id)}/restore`),
  saveNote: (path: string, text: string, hash: string | null) => request<NoteSaved>('PUT', '/api/notes/note', { path, text, hash }),
  recreateNote: (path: string, text: string) => request<NoteSaved>('PUT', '/api/notes/note', { path, text, create: true }),
  moveNote: (path: string, to: string, hash?: string | null) => request<NoteMoved>('POST', '/api/notes/move', { path, to, hash }),
  newNoteFolder: (path: string) => request<{ path: string }>('POST', '/api/notes/folder', { path }),
  trashNotes: (paths: string[], folders: string[] = []) => request<{ trash_id: string; paths: string[] }>('POST', '/api/notes/trash', { paths, folders }),
  moveNoteFolder: (path: string, to: string) => request<NoteMoved>('POST', '/api/notes/folder/move', { path, to }),
  setNoteFolderColor: (path: string, color: string | null) => request<{ folder_colors: Record<string, string> }>('PUT', '/api/notes/folder/color', { path, color }),
  restoreNoteBatch: (trashId: string) => request<{ paths: string[]; skipped: string[] }>('POST', '/api/notes/restore', { trash_id: trashId }),
  noteKeys: () => request<{ keys: NoteKeys }>('GET', '/api/notes/keys'),
  // reference lists (citation graph)
  itemReferences: (id: string) => request<ItemReferences>('GET', `/api/items/${id}/references`),
  fetchReferences: (id: string) => request<ItemReferences>('POST', `/api/items/${id}/references`),
  queueReferences: (ids: string[]) => request('POST', '/api/references/queue', { ids }),
  citationGraph: () => request('GET', '/api/graph/citations'),
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
  // tasks (records in library.db; a note shows one with a {{task:id}} line)
  tasks: () => request<TasksOverview>('GET', '/api/tasks'),
  getTask: (id: string) => request<{ task: Task & { refs: TaskRef[]; subtasks: Task[] } }>('GET', `/api/tasks/${id}`),
  createTask: (data: TaskChanges) => request<{ task: Task; ref?: { path: string; line: number } }>('POST', '/api/tasks', data),
  updateTask: (id: string, data: TaskChanges) => request<{ task: Task; changed: Task[] }>('PATCH', `/api/tasks/${id}`, data),
  deleteTasks: (ids: string[]) => request<{ batch: string; ids: string[] }>('POST', '/api/tasks/delete', { ids }),
  restoreTasks: (batch: string) => request<{ ids: string[] }>('POST', '/api/tasks/restore', { batch }),
  restoreTask: (id: string) => request<{ ids: string[] }>('POST', '/api/tasks/restore', { id }),
  taskTrash: () => request<{ tasks: Task[] }>('GET', '/api/tasks/trash'),
  addTaskToNote: (id: string, path: string, afterLine: number | null = null) => request<{ ref: { path: string; line: number } }>('POST', `/api/tasks/${id}/notes`, { path, after_line: afterLine }),
  removeTaskFromNote: (id: string, path: string) => request<{ path: string; removed: number }>('POST', `/api/tasks/${id}/notes/remove`, { path }),
  convertToTasks: (text: string) => request<{ text: string; tasks: Task[] }>('POST', '/api/tasks/convert', { text }),
  taskHistory: (id: string) => request<{ history: TaskHistoryEntry[] }>('GET', `/api/tasks/${id}/history`),
  undoTaskChange: (historyId: number) => request<{ task: Task }>('POST', '/api/tasks/undo', { history_id: historyId }),
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

/** A PDF to open in one of the app's own tabs (tabs/tabs.ts). */
export interface InAppPdf {
  key: string;
  url: string;
  title: string;
  spot: { page?: number | null; q?: string; rect?: string };
  newTab: boolean;
}

/** Opens PDFs in the app's own tabs; `open` answers false when PDFs open in browser tabs instead
 *  (Settings → PDF Viewer). Set by tabs/tabs.ts, so this module does not import the tabs. */
export interface PdfTabOpener { enabled(): boolean; open(pdf: InAppPdf): void }
let appTabs: PdfTabOpener | null = null;
export function setPdfTabOpener(o: PdfTabOpener | null): void { appTabs = o; }

function openInApp(item: Pick<Item, 'id' | 'title'>, att: Pick<Attachment, 'id' | 'url'>, page: number | null | undefined, at: ViewerSpot, newTab: boolean): boolean {
  if (!appTabs?.enabled()) return false;
  appTabs.open({ key: fileUrl(att), url: viewerUrl(item, att, page, at), title: item.title || 'PDF', spot: { page, q: at.q, rect: at.rect }, newTab });
  return true;
}

/** Where a PDF will be opened: a browser tab (`win`), or one of the app's own tabs (`inApp`). */
export interface PdfTab { win: Window | null; reuse: boolean; inApp?: boolean; newTab?: boolean }

/* One viewer tab per PDF: opening a PDF that already has a tab (opened from this app tab) scrolls
 * that tab to the new spot and brings it forward instead of opening another one. `newTab` (Cmd/
 * Ctrl+click on a citation) always opens a fresh tab, which then becomes the one reused. The same
 * holds for the app's own tabs, which tabs/tabs.ts keeps. */
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
  if (appTabs?.enabled()) return { win: null, reuse: false, inApp: true, newTab };   // no popup to take
  const w = !newTab && viewerTab(att);
  if (w) return { win: w, reuse: true };
  return { win: window.open('about:blank', newTab ? '_blank' : tabName(att)), reuse: false };
}

export function pointPdfTab(tab: PdfTab, item: Pick<Item, 'id' | 'title'>, att: Pick<Attachment, 'id' | 'url'>, page?: number | null, at: ViewerSpot = {}): void {
  if (tab.inApp && openInApp(item, att, page, at, !!tab.newTab)) return;
  if (tab.reuse) {
    const w = viewerTab(att);
    if (w && w === tab.win) return reuseTab(w, page, at);
  }
  const url = viewerUrl(item, att, page, at);
  if (!tab.win) { window.open(url, '_blank'); return; }
  tab.win.location.href = url;
  pdfTabs.set(fileUrl(att), tab.win);
}

/** Open a PDF in a new window: in a tab of a new app window, or in a viewer window of its own
 *  when PDFs open in browser tabs. */
export function openPdfInNewWindow(item: Pick<Item, 'id' | 'title'>, att: Pick<Attachment, 'id' | 'url'>, page?: number | null): void {
  const url = viewerUrl(item, att, page);
  if (appTabs?.enabled()) openNewWindow({ pdf: url });
  else openPageInNewWindow(url);
}

/** Open a PDF in the viewer, reusing its tab when it has one (see above). */
export function openAttachment(item: Pick<Item, 'id' | 'title'>, att: Pick<Attachment, 'id' | 'url'>, page?: number | null, at: ViewerSpot = {}, { newTab = false } = {}): void {
  if (openInApp(item, att, page, at, newTab)) return;
  const w = !newTab && viewerTab(att);
  if (w) return reuseTab(w, page, at);
  const win = window.open(viewerUrl(item, att, page, at), newTab ? '_blank' : tabName(att));
  if (win) pdfTabs.set(fileUrl(att), win);
}
