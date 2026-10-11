// Popup of the "Save to Library" button.
// 1. read citation metadata from the active tab (extract.js)
// 2. check the local library is running and load its folders/tags
// 3. let the user adjust title / folder / tags, then save
/* global extractCitation, fetchPdfInPage */

const DEFAULT_SERVER = 'http://127.0.0.1:8000';
const MAX_PDF_BYTES = 60 * 1024 * 1024;
const view = document.getElementById('view');
const conn = document.getElementById('conn');
let server = DEFAULT_SERVER;
let page = null, lib = null, tab = null;
let saveState = { tags: [], folder: '' };   // remembered for "Save Anyway"

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const shortAuthors = (a) => (!a.length ? '' : a.length === 1 ? a[0] : a.length === 2 ? `${a[0]} and ${a[1]}` : `${a[0]} et al.`);
const TYPE_LABELS = { journalArticle: 'Journal Article', preprint: 'Preprint', conferencePaper: 'Conference Paper', book: 'Book',
  bookSection: 'Book Section', thesis: 'Thesis', report: 'Report', webpage: 'Web Page', dataset: 'Dataset' };

document.getElementById('settings').onclick = () => chrome.runtime.openOptionsPage();

async function api(path, opts = {}) {
  const res = await fetch(server + path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  return { ok: res.ok, status: res.status, data };
}

async function init() {
  server = ((await chrome.storage.sync.get('server')).server || DEFAULT_SERVER).replace(/\/+$/, '');
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const forced = new URLSearchParams(location.search).get('tabId');   // used by the automated tests
  if (forced) tab = await chrome.tabs.get(Number(forced));
  if (!tab || !/^(https?|file):/.test(tab.url || '')) {
    return showMessage('🛈', 'Nothing to save here', 'Open an article page, an arXiv abstract or a PDF, then click the button again.', 'warn');
  }
  const [extracted, ping] = await Promise.allSettled([
    chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractCitation }),
    api('/api/connector/ping'),
  ]);
  if (ping.status !== 'fulfilled' || !ping.value.ok || !['synapse', 'zotero-clone'].includes(ping.value.data?.app)) {
    conn.className = 'conn off';
    return showOffline();
  }
  conn.className = 'conn ok';
  conn.title = `Connected to ${server}`;
  lib = ping.value.data;
  page = extracted.status === 'fulfilled' ? extracted.value?.[0]?.result : null;
  if (!page) page = { title: tab.title, page_url: tab.url, item_type: 'webpage', authors: [] };
  renderForm();
}

function showOffline() {
  view.innerHTML = `<div class="result">
      <div class="big err">!</div><h2>Library not running</h2>
      <p>Couldn't reach <code>${esc(server)}</code>.</p>
      <p>Start it with <code>python server.py</code> in the app folder, then retry.</p>
      <div class="actions"><button class="btn" id="opts">Change address…</button><button class="btn primary" id="retry">Retry</button></div></div>`;
  document.getElementById('retry').onclick = () => { view.innerHTML = '<div class="center"><span class="spinner"></span> Connecting…</div>'; init(); };
  document.getElementById('opts').onclick = () => chrome.runtime.openOptionsPage();
}

function showMessage(icon, title, text, kind = 'ok', actions = '') {
  view.innerHTML = `<div class="result"><div class="big ${kind}">${icon}</div><h2>${esc(title)}</h2><p>${text}</p>${actions}</div>`;
}

/* ------------------------------------------------------------------ form */
function folderOptions(selected) {
  const kids = {};
  for (const f of lib.folders) (kids[f.parent_id ?? 'root'] ||= []).push(f);
  const out = ['<option value="">My Library (no folder)</option>'];
  const walk = (p, depth) => (kids[p] || []).sort((a, b) => a.name.localeCompare(b.name)).forEach((f) => {
    out.push(`<option value="${f.id}" ${String(f.id) === String(selected) ? 'selected' : ''}>${' '.repeat(depth)}${esc(f.name)}</option>`);
    walk(f.id, depth + 1);
  });
  walk('root', 0);
  return out.join('');
}

async function renderForm() {
  // the library's Settings → Adding Items ("never" / "always" / "ask"; older libraries send nothing)
  const onAdd = lib.on_add || {};
  const prefs = await chrome.storage.local.get(['lastFolder']);
  const folderOk = lib.folders.some((f) => String(f.id) === String(prefs.lastFolder));
  const ident = page.doi ? `<span class="badge accent" title="${esc(page.doi)}">DOI ${esc(page.doi)}</span>`
    : page.arxiv ? `<span class="badge accent">arXiv ${esc(page.arxiv)}</span>` : '';
  const pdfBadge = page.is_pdf ? '<span class="badge ok">Metadata read from the PDF</span>' : page.pdf_url ? '<span class="badge ok">PDF available</span>' : '<span class="badge">No PDF link</span>';
  const meta = [shortAuthors(page.authors || []), (page.year || '').slice(0, 4), page.journal].filter(Boolean).join(' · ');
  view.innerHTML = `
    <div class="badges"><span class="badge">${esc(page.is_pdf ? 'PDF document' : (TYPE_LABELS[page.item_type] || 'Web Page'))}</span>${ident}${pdfBadge}</div>
    <textarea class="title" id="title" rows="2" spellcheck="false">${esc(page.title || '')}</textarea>
    <div class="sub">${esc(meta) || `<span style="color:var(--text3)">${esc(page.site || '')}</span>`}</div>
    <label class="f" for="folder">Folder</label>
    <select id="folder">${folderOptions(folderOk ? prefs.lastFolder : '')}</select>
    <label class="f">Tags</label>
    <div class="tags" id="tags"><input id="tagIn" list="tagList" placeholder="Add tags…" autocomplete="off"></div>
    <datalist id="tagList">${lib.tags.map((t) => `<option value="${esc(t)}">`).join('')}</datalist>
    <div class="checks">
      ${page.pdf_url ? '<label><input type="checkbox" id="attach" checked> Attach the PDF</label>' : ''}
      ${page.doi || page.arxiv ? '<label><input type="checkbox" id="lookup" checked> Complete metadata online (Crossref / arXiv)</label>' : ''}
      ${onAdd.find_pdf === 'ask' && !page.is_pdf ? `<label><input type="checkbox" id="findPdf" checked> ${page.pdf_url ? 'If the PDF can’t be attached, look' : 'Look'} for an open-access PDF</label>` : ''}
      ${onAdd.fetch_references === 'ask' && (page.doi || page.arxiv) ? '<label><input type="checkbox" id="refs" checked> Fetch the reference list</label>' : ''}
    </div>
    <div class="actions"><button class="btn primary" id="save">Save to Library</button></div>
    <div class="hint"><kbd>${navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}</kbd>+<kbd>Enter</kbd> saves${page.has_citation_meta || page.doi || page.arxiv || page.is_pdf ? '' : ' · no citation data found, saving as a web page'}</div>`;

  const tags = [];
  const box = document.getElementById('tags');
  const input = document.getElementById('tagIn');
  const paintTags = () => {
    box.querySelectorAll('.chip').forEach((c) => c.remove());
    tags.forEach((t, i) => {
      const c = document.createElement('span');
      c.className = 'chip';
      c.innerHTML = `${esc(t)}<button title="Remove" data-i="${i}">×</button>`;
      box.insertBefore(c, input);
    });
  };
  const addTag = (v) => {
    v = v.trim().replace(/,$/, '').trim();
    if (v && !tags.some((t) => t.toLowerCase() === v.toLowerCase())) tags.push(lib.tags.find((t) => t.toLowerCase() === v.toLowerCase()) || v);
    input.value = '';
    paintTags();
  };
  input.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' && !(e.metaKey || e.ctrlKey)) || e.key === ',') { e.preventDefault(); addTag(input.value); }
    if (e.key === 'Backspace' && !input.value && tags.length) { tags.pop(); paintTags(); }
  });
  input.addEventListener('change', () => { if (lib.tags.includes(input.value)) addTag(input.value); });   // picked from the datalist
  box.addEventListener('click', (e) => {
    if (e.target.dataset.i !== undefined) { tags.splice(Number(e.target.dataset.i), 1); paintTags(); }
    input.focus();
  });

  const save = () => doSave({ tags: [...tags, ...(input.value.trim() ? [input.value.trim()] : [])] });
  document.getElementById('save').onclick = save;
  document.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) save(); });
  const title = document.getElementById('title');
  title.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !(e.metaKey || e.ctrlKey)) e.preventDefault(); });
  document.getElementById('save').focus();
}

/* ------------------------------------------------------------------ save */
async function doSave({ tags, allowDuplicate = false }) {
  const btn = document.getElementById('save');
  const folder = document.getElementById('folder')?.value ?? '';
  const attach = page.pdf_url && (document.getElementById('attach')?.checked ?? true);
  const lookup = document.getElementById('lookup')?.checked ?? true;
  // only asked when the setting is "ask"; "Save Anyway" (no form on screen) keeps the earlier answer
  const findPdf = document.getElementById('findPdf')?.checked ?? saveState.findPdf ?? false;
  const fetchRefs = document.getElementById('refs')?.checked ?? saveState.fetchRefs ?? false;
  const title = document.getElementById('title')?.value.trim() || page.title;
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Saving…'; }
  await chrome.storage.local.set({ lastFolder: folder });
  saveState = { tags, folder, findPdf, fetchRefs };

  const meta = { ...page, title, folder_id: folder || null, tags, lookup, attach_pdf: attach, allow_duplicate: allowDuplicate,
    find_pdf: findPdf, fetch_references: fetchRefs };
  const fd = new FormData();
  // Fetch the PDF from inside the page so the user's own session (institutional access) is used.
  if (attach) {
    if (btn) btn.innerHTML = '<span class="spinner"></span> Getting the PDF…';
    try {
      const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: fetchPdfInPage, args: [page.pdf_url, MAX_PDF_BYTES] });
      if (result?.b64) {
        const bytes = Uint8Array.from(atob(result.b64), (c) => c.charCodeAt(0));
        const name = decodeURIComponent(new URL(page.pdf_url).pathname.split('/').pop() || 'paper.pdf');
        fd.append('file', new Blob([bytes], { type: 'application/pdf' }), /\.pdf$/i.test(name) ? name : `${name}.pdf`);
        meta.pdf_name = 'Full Text PDF';
        meta.attach_pdf = false;   // already attached as upload; don't download again
      }
      // otherwise the library will try to download pdf_url itself
    } catch { /* cross-origin PDF: let the server try */ }
  }
  fd.append('meta', JSON.stringify(meta));
  if (btn) btn.innerHTML = '<span class="spinner"></span> Saving…';
  let res;
  try {
    res = await api('/api/connector/save', { method: 'POST', body: fd });
  } catch {
    conn.className = 'conn off';
    return showOffline();
  }
  if (res.status === 409) return showDuplicate(res.data);
  if (!res.ok) {
    showMessage('!', 'Could not save', esc(res.data?.error || `Error ${res.status}`), 'err',
      '<div class="actions"><button class="btn" id="back">Back</button></div>');
    document.getElementById('back').onclick = () => renderForm();
    return;
  }
  showSaved(res.data);
}


function folderName(id) {
  return lib.folders.find((f) => String(f.id) === String(id))?.name;
}

function showSaved({ item, pdf, source }) {
  const where = item.folder_id ? `in <b>${esc(folderName(item.folder_id) || 'folder')}</b>` : 'in <b>My Library</b>';
  const pdfLine = { attached: 'PDF attached ✓', downloaded: 'PDF downloaded ✓', failed: 'The PDF could not be downloaded (try “Find PDF” in the app).',
    'not-a-pdf': 'The PDF link returned a web page (login needed?) — not attached.',
    searching: 'Looking for an open-access PDF in the background…', none: '' }[pdf] || '';
  showMessage('✓', 'Saved', `${where}${source === 'online' ? ' · metadata completed online' : ''}`, 'ok',
    `<div class="t">${esc(item.title)}</div>${pdfLine ? `<p>${esc(pdfLine)}</p>` : ''}
     <div class="actions"><button class="btn" id="close">Close</button><button class="btn primary" id="open">Open in Library</button></div>`);
  document.getElementById('close').onclick = () => window.close();
  document.getElementById('open').onclick = () => openInLibrary(item.id);
  setTimeout(() => document.getElementById('open')?.focus(), 30);
}

function showDuplicate(d) {
  const inFolders = (d.folder_ids || []).map(folderName).filter(Boolean);
  const target = saveState.folder && !(d.folder_ids || []).map(String).includes(String(saveState.folder)) ? folderName(saveState.folder) : null;
  showMessage('=', 'Already in your library', `<span class="t">${esc(d.title || '')}</span>${inFolders.length ? `in <b>${inFolders.map(esc).join('</b>, <b>')}</b>` : ''}`, 'warn',
    `<div class="actions">${target
      ? `<button class="btn primary" id="file">Add to “${esc(target)}”</button>`
      : '<button class="btn" id="anyway">Save Anyway</button>'}
      <button class="btn ${target ? '' : 'primary'}" id="open">Open in Library</button></div>
     ${target ? '<div class="hint"><a href="#" id="anyway">Save a separate copy instead</a></div>' : ''}`);
  document.getElementById('open').onclick = () => openInLibrary(d.duplicate_of);
  document.getElementById('anyway').onclick = (e) => {
    e.preventDefault();
    view.innerHTML = '<div class="center"><span class="spinner"></span> Saving…</div>';
    doSave({ tags: saveState.tags, allowDuplicate: true });
  };
  document.getElementById('file')?.addEventListener('click', async () => {
    const res = await api('/api/connector/file', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: d.duplicate_of, folder_id: saveState.folder, tags: saveState.tags }) });
    if (!res.ok) return showMessage('!', 'Could not update', esc(res.data?.error || `Error ${res.status}`), 'err');
    showMessage('✓', 'Added to folder', `“${esc(res.data.item.title)}” is now also in <b>${esc(target)}</b> — it is still stored only once.`, 'ok',
      '<div class="actions"><button class="btn" id="close">Close</button><button class="btn primary" id="open">Open in Library</button></div>');
    document.getElementById('close').onclick = () => window.close();
    document.getElementById('open').onclick = () => openInLibrary(d.duplicate_of);
  });
}

async function openInLibrary(id) {
  const url = `${server}/#item=${encodeURIComponent(id)}`;
  // reuse an open library tab if there is one
  const tabs = await chrome.tabs.query({ url: `${server}/*` }).catch(() => []);
  if (tabs.length) {
    await chrome.tabs.update(tabs[0].id, { active: true, url });
    await chrome.windows.update(tabs[0].windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url });
  }
  window.close();
}

init();
