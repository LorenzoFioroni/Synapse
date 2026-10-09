// Thin client for the JSON API. Every call throws an Error with a readable
// message on failure, so callers can show it in a toast.

export class ApiError extends Error {
  constructor(message, status, data) { super(message); this.status = status; this.data = data; }
}

async function request(method, url, body, { raw = false } = {}) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new ApiError('Cannot reach the server. Is it still running?', 0);
  }
  if (raw) {
    if (!res.ok) throw new ApiError(`Request failed (${res.status})`, res.status);
    return res;
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status, data);
  return data;
}

const qs = (params) => new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString();

export const api = {
  schema: () => request('GET', '/api/schema'),
  stats: () => request('GET', '/api/stats'),

  listItems: ({ view, q, mode, tags }) => request('GET', `/api/items?${qs({ view, q, mode, tags: tags && tags.join(',') })}`),
  getItem: (id) => request('GET', `/api/items/${id}`),
  createItem: (data = {}) => request('POST', '/api/items', data),
  updateItem: (id, data) => request('PATCH', `/api/items/${id}`, data),
  trash: (ids) => request('POST', '/api/items/trash', { ids }),
  restore: (ids) => request('POST', '/api/items/restore', { ids }),
  deleteForever: (ids) => request('POST', '/api/items/delete', { ids }),
  emptyTrash: () => request('POST', '/api/trash/empty'),
  regenerateCitekeys: (ids) => request('POST', '/api/items/citekeys', { ids }),

  lookup: (id) => request('POST', `/api/items/${id}/lookup`),
  fromIdentifier: (identifier, allowDuplicate = false, folderId = null) =>
    request('POST', '/api/items/from_identifier', { identifier, allow_duplicate: allowDuplicate, folder_id: folderId }),
  fromPdfs: (files, folderId = null) => {
    const fd = new FormData(); [...files].forEach((f) => fd.append('file', f));
    if (folderId) fd.append('folder_id', folderId);
    return request('POST', '/api/items/from_pdf', fd);
  },
  findPdf: (id) => request('POST', `/api/items/${id}/find_pdf`),

  uploadAttachments: (id, files) => { const fd = new FormData(); [...files].forEach((f) => fd.append('file', f)); return request('POST', `/api/items/${id}/attachments`, fd); },
  renameAttachment: (attId, name) => request('PATCH', `/api/attachments/${attId}`, { name }),
  deleteAttachment: (attId) => request('DELETE', `/api/attachments/${attId}`),

  getSettings: () => request('GET', '/api/settings'),
  saveSettings: (changes) => request('PUT', '/api/settings', changes),
  resetSettings: () => request('POST', '/api/settings/reset'),
  citekeyPreview: (pattern) => request('GET', `/api/settings/citekey_preview?${qs({ pattern })}`),

  formats: () => request('GET', '/api/formats'),
  importPreview: (fileOrText, format) => {
    if (typeof fileOrText === 'string') return request('POST', '/api/import/preview', { text: fileOrText, format });
    const fd = new FormData(); fd.append('file', fileOrText); if (format) fd.append('format', format);
    return request('POST', '/api/import/preview', fd);
  },
  importCommit: (entries, skipDuplicates, folderId = null) => request('POST', '/api/import/commit', { entries, skip_duplicates: skipDuplicates, folder_id: folderId }),
  exportText: (format, ids) => request('GET', `/api/export?${qs({ format, ids: ids && ids.join(','), download: 0 })}`),
  exportUrl: (format, ids) => `/api/export?${qs({ format, ids: ids && ids.join(',') })}`,

  rebuildFulltext: () => request('POST', '/api/fulltext/rebuild'),

  // folders · tags · smart folders
  createFolder: (name, parentId = null) => request('POST', '/api/folders', { name, parent_id: parentId }),
  updateFolder: (id, data) => request('PATCH', `/api/folders/${id}`, data),
  deleteFolder: (id) => request('DELETE', `/api/folders/${id}`),
  changeFolders: (ids, add = [], remove = []) => request('POST', '/api/items/folders', { ids, add, remove }),
  tagItems: (ids, add = [], remove = []) => request('POST', '/api/items/tags', { ids, add, remove }),
  updateTag: (id, data) => request('PATCH', `/api/tags/${id}`, data),
  deleteTag: (id) => request('DELETE', `/api/tags/${id}`),
  createSmart: (name, query, mode) => request('POST', '/api/smart', { name, query, mode }),
  updateSmart: (id, data) => request('PATCH', `/api/smart/${id}`, data),
  deleteSmart: (id) => request('DELETE', `/api/smart/${id}`),
  searchPreview: (q, mode) => request('GET', `/api/search/preview?${qs({ q, mode })}`),

  // notes (addressed by their path inside the notes folder)
  notes: () => request('GET', '/api/notes'),
  getNote: (path) => request('GET', `/api/notes/note?${qs({ path })}`),
  createNote: (data) => request('POST', '/api/notes/note', data),
  saveNote: (path, text, hash) => request('PUT', '/api/notes/note', { path, text, hash }),
  recreateNote: (path, text) => request('PUT', '/api/notes/note', { path, text, create: true }),
  moveNote: (path, to, hash) => request('POST', '/api/notes/move', { path, to, hash }),
  newNoteFolder: (path) => request('POST', '/api/notes/folder', { path }),
  trashNote: (path) => request('POST', '/api/notes/trash', { path }),
  noteTrash: () => request('GET', '/api/notes/trash'),
  citeIndex: () => request('GET', '/api/notes/cite_index'),
  renamePlan: (old, nu) => request('GET', `/api/notes/rename_plan?${qs({ old, new: nu })}`),
  itemNotes: (id) => request('GET', `/api/items/${id}/notes`),
  restoreNote: (trashId, path) => request('POST', '/api/notes/restore', { trash_id: trashId, path }),
  noteLinks: (path) => request('GET', `/api/notes/links?${qs({ path })}`),
  searchNotes: (q) => request('GET', `/api/notes/search?${qs({ q })}`),
  rescanNotes: () => request('POST', '/api/notes/rescan'),
  uploadNoteImage: (file) => { const fd = new FormData(); fd.append('file', file); return request('POST', '/api/notes/attachments', fd); },
};

export function fileUrl(att) {
  return /^https?:/.test(att.url) ? `/api/proxy?url=${encodeURIComponent(att.url)}` : `/pdfs/${encodeURIComponent(att.url)}`;
}

export function openAttachment(item, att, page) {
  const p = new URLSearchParams({ file: fileUrl(att), paper_id: item.id, title: item.title || 'PDF' });
  if (page) p.set('page', page);
  window.open(`/viewer.html?${p}`, '_blank');
}
