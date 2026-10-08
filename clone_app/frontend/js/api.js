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

  listItems: ({ view, q, mode }) => request('GET', `/api/items?${qs({ view, q, mode })}`),
  getItem: (id) => request('GET', `/api/items/${id}`),
  createItem: (data = {}) => request('POST', '/api/items', data),
  updateItem: (id, data) => request('PATCH', `/api/items/${id}`, data),
  trash: (ids) => request('POST', '/api/items/trash', { ids }),
  restore: (ids) => request('POST', '/api/items/restore', { ids }),
  deleteForever: (ids) => request('POST', '/api/items/delete', { ids }),
  emptyTrash: () => request('POST', '/api/trash/empty'),
  regenerateCitekeys: (ids) => request('POST', '/api/items/citekeys', { ids }),

  lookup: (id) => request('POST', `/api/items/${id}/lookup`),
  fromIdentifier: (identifier, allowDuplicate = false) =>
    request('POST', '/api/items/from_identifier', { identifier, allow_duplicate: allowDuplicate }),
  fromPdfs: (files) => { const fd = new FormData(); [...files].forEach((f) => fd.append('file', f)); return request('POST', '/api/items/from_pdf', fd); },
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
  importCommit: (entries, skipDuplicates) => request('POST', '/api/import/commit', { entries, skip_duplicates: skipDuplicates }),
  exportText: (format, ids) => request('GET', `/api/export?${qs({ format, ids: ids && ids.join(','), download: 0 })}`),
  exportUrl: (format, ids) => `/api/export?${qs({ format, ids: ids && ids.join(',') })}`,

  rebuildFulltext: () => request('POST', '/api/fulltext/rebuild'),
};

export function fileUrl(att) {
  return /^https?:/.test(att.url) ? `/api/proxy?url=${encodeURIComponent(att.url)}` : `/pdfs/${encodeURIComponent(att.url)}`;
}

export function openAttachment(item, att, page) {
  const p = new URLSearchParams({ file: fileUrl(att), paper_id: item.id, title: item.title || 'PDF' });
  if (page) p.set('page', page);
  window.open(`/viewer.html?${p}`, '_blank');
}
