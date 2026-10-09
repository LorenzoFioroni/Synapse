// Settings page (full-screen view). Changes save immediately.
import * as A from '../actions.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { emit, state } from '../store.js';
import { confirmDialog } from '../ui/dialog.js';
import { toast, toastError } from '../ui/toast.js';
import { debounce, escapeHtml, formatBytes, formatDate, modLabel, plural, store } from '../util.js';

const SECTIONS = [
  ['general', 'General', 'palette'],
  ['citekeys', 'Citation Keys', 'key'],
  ['search', 'Search & Full Text', 'search'],
  ['io', 'Import & Export', 'export'],
  ['online', 'Online Services', 'globe'],
  ['library', 'Library & Shortcuts', 'database'],
];

const TOKENS = ['{auth}', '{Auth}', '{AUTH}', '{authEtAl}', '{AuthEtAl}', '{authors2}', '{year}', '{yy}', '{title}', '{Title}', '{shorttitle}', '{ShortTitle}', '{journal}', '{doi}'];

let view, statsTimer;

export function applyAppearance(s = state.settings) {
  const root = document.documentElement;
  const dark = s.theme === 'dark' || (s.theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  root.dataset.theme = dark ? 'dark' : 'light';
  store('zc.themeHint', root.dataset.theme);
  root.dataset.density = s.density || 'comfortable';
  root.style.setProperty('--fs', `${s.font_size || 13}px`);
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyAppearance());

async function save(changes, el) {
  try {
    const res = await api.saveSettings(changes);
    state.settings = res.settings;
    applyAppearance();
    emit('settings');
    const pill = (el?.closest('.setting') || view)?.querySelector('.saved-pill');
    if (pill) { pill.classList.add('show'); setTimeout(() => pill.classList.remove('show'), 1200); }
    return true;
  } catch (e) { toastError(e); return false; }
}

const saved = '<span class="saved-pill">' + icon('check', 'icon-sm') + ' Saved</span>';

function switchRow(key, title, desc) {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div><div class="s-desc">${desc}</div></div>
    <div class="s-control">${saved}<label class="switch"><input type="checkbox" data-key="${key}" ${state.settings[key] ? 'checked' : ''}><span></span></label></div></div>`;
}

function selectRow(key, title, desc, options) {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div><div class="s-desc">${desc}</div></div>
    <div class="s-control">${saved}<select class="select" data-key="${key}">${options.map(([v, l]) => `<option value="${v}" ${String(state.settings[key]) === String(v) ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}</select></div></div>`;
}

function segmentedRow(key, title, desc, options) {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div><div class="s-desc">${desc}</div></div>
    <div class="s-control">${saved}<div class="segmented" data-seg="${key}">${options.map(([v, l]) => `<button data-v="${v}" class="${state.settings[key] === v ? 'active' : ''}">${l}</button>`).join('')}</div></div></div>`;
}

export function openSettings(sectionId = null) {
  closeSettings();
  const s = state.settings;
  const meta = state.settingsMeta;
  const presetMatch = meta.citekeyPresets.find((p) => p.pattern === s.citekey_pattern);
  view = document.createElement('div');
  view.id = 'settings-view';
  view.innerHTML = `
    <div class="settings-top"><button class="btn btn-ghost" data-close>${icon('arrowLeft')} Back to Library</button>
      <h1>Settings</h1><span class="spacer"></span><button class="btn btn-ghost btn-sm" data-reset>Reset to defaults</button></div>
    <div class="settings-main">
      <nav class="settings-nav">${SECTIONS.map(([id, label, ic]) => `<button class="side-item" data-nav="${id}">${icon(ic)}<span>${label}</span></button>`).join('')}</nav>
      <div class="settings-content"><div class="settings-inner">

      <section class="settings-section" id="set-general"><h2>General</h2><p class="lead">How the library looks and behaves.</p>
        <div class="card">
          ${segmentedRow('theme', 'Theme', 'Follow your system, or force light or dark.', [['system', 'System'], ['light', 'Light'], ['dark', 'Dark']])}
          ${segmentedRow('density', 'Row density', 'Compact fits more items on screen.', [['comfortable', 'Comfortable'], ['compact', 'Compact']])}
          <div class="setting"><div class="s-text"><div class="s-title">Font size</div><div class="s-desc">Base text size of the interface.</div></div>
            <div class="s-control">${saved}<input type="range" min="11" max="18" step="1" value="${s.font_size}" data-key="font_size" style="width:140px">
            <span class="mono" data-fs-label style="width:36px">${s.font_size}px</span></div></div>
          ${switchRow('open_pdf_on_double_click', 'Double-click opens the PDF', 'When off, double-clicking an item starts editing its title.')}
          ${switchRow('confirm_trash', 'Ask before moving to the Trash', 'Items in the Trash can always be restored, and an Undo button appears after deleting.')}
          ${switchRow('show_subfolder_items', 'Show items from subfolders', 'A folder also lists the items of the folders inside it.')}
        </div></section>

      <section class="settings-section" id="set-citekeys"><h2>Citation Keys</h2>
        <p class="lead">Keys are used by BibTeX/LaTeX (<span class="mono">\\cite{key}</span>). Duplicates get a, b, c… suffixes automatically.</p>
        <div class="card">
          ${selectRow('_preset', 'Style', 'Pick a common style, or write your own pattern below.', [...meta.citekeyPresets.map((p) => [p.pattern, p.label]), ['custom', 'Custom pattern…']])}
          <div class="setting stack"><div class="s-text"><div class="s-title">Pattern</div>
            <div class="s-desc">Tokens in braces are replaced; the case of the token name sets the case of the output.</div></div>
            <div class="pattern-row"><input class="input" data-pattern value="${escapeHtml(s.citekey_pattern)}" spellcheck="false"></div>
            <div class="token-list">${TOKENS.map((t) => `<button class="token" data-token="${t}">${t}</button>`).join('')}</div>
            <div class="preview-box" data-preview></div><div class="form-error" data-pattern-error hidden></div></div>
          ${switchRow('citekey_auto', 'Generate keys for new items', 'Applies to items added manually, by identifier or from PDFs, and to imported entries without a key.')}
          <div class="setting"><div class="s-text"><div class="s-title">Regenerate all keys</div><div class="s-desc">Rewrite the citation key of every item with the current pattern.</div></div>
            <div class="s-control"><button class="btn" data-regen-all>${icon('refresh')} Regenerate…</button></div></div>
        </div></section>

      <section class="settings-section" id="set-search"><h2>Search &amp; Full Text</h2>
        <p class="lead">The full-text index is stored separately from your library (<span class="mono">data/fulltext.db</span>) and can be rebuilt at any time.</p>
        <div class="card">
          ${selectRow('default_search_mode', 'Default search scope', 'Used when the app opens. You can switch scope from the search box at any time.', Object.entries(meta.searchModes))}
          ${switchRow('fulltext_auto_index', 'Index PDFs automatically', 'Extract text from new attachments in the background so they can be searched.')}
          <div class="setting stack"><div style="display:flex;align-items:center;gap:12px"><div class="s-text"><div class="s-title">Full-text index</div>
            <div class="s-desc" data-index-desc>Loading…</div></div>
            <div class="s-control"><button class="btn" data-rebuild>${icon('refresh')} Rebuild Index</button></div></div>
            <div class="progress" data-index-progress hidden><div style="width:0"></div></div></div>
        </div></section>

      <section class="settings-section" id="set-io"><h2>Import &amp; Export</h2><p class="lead">BibTeX, RIS, EndNote (tagged .enw and XML) and CSL JSON are supported in both directions.</p>
        <div class="card">
          ${selectRow('export_format', 'Default export format', 'Pre-selected in the Export dialog.', [['bibtex', 'BibTeX'], ['ris', 'RIS'], ['endnote', 'EndNote (.enw)'], ['endnote_xml', 'EndNote XML'], ['csljson', 'CSL JSON']])}
          ${switchRow('bibtex_unicode', 'Keep Unicode in BibTeX', 'On: write “Bañuls” as is (biber/BibLaTeX). Off: escape as “Ba{\\~{n}}uls” for classic BibTeX.')}
          ${switchRow('export_abstract', 'Include abstracts when exporting', 'Turn off for smaller .bib files.')}
          ${switchRow('import_skip_duplicates', 'Untick duplicates when importing', 'Entries whose DOI or title+year already exist start unticked in the import preview.')}
          ${switchRow('import_keywords_as_tags', 'Turn imported keywords into tags', 'Keywords in BibTeX/RIS/EndNote files become tags. Exports always write tags back as keywords.')}
          <div class="setting"><div class="s-text"><div class="s-title">Transfer</div><div class="s-desc">Bring references in or take your whole library elsewhere.</div></div>
            <div class="s-control"><button class="btn" data-import>${icon('import')} Import…</button><button class="btn" data-export>${icon('export')} Export Library…</button></div></div>
        </div></section>

      <section class="settings-section" id="set-online"><h2>Online Services</h2><p class="lead">Used for Retrieve Metadata, Add by Identifier and Find Available PDF (Crossref, OpenAlex, arXiv, Open Library, Unpaywall).</p>
        <div class="card">
          <div class="setting stack"><div class="s-text"><div class="s-title">Contact e-mail ${saved}</div>
            <div class="s-desc">Crossref and Unpaywall ask clients to identify themselves; with an e-mail you get faster, more reliable responses. It is only sent to those services.</div></div>
            <input class="input" type="email" data-email placeholder="you@university.edu" value="${escapeHtml(s.contact_email)}"></div>
        </div></section>

      <section class="settings-section" id="set-library"><h2>Library &amp; Shortcuts</h2><p class="lead" data-data-dir></p>
        <div class="card"><div class="stat-grid" data-stats></div></div>
        <h2 style="margin-top:22px;font-size:15px">Browser button</h2>
        <div class="card"><div class="setting"><div class="s-text"><div class="s-title">Save to Library (Chrome)</div>
          <div class="s-desc">Save the article you're reading with one click. In Chrome open <span class="mono">chrome://extensions</span>, turn on Developer mode,
          choose “Load unpacked” and select the <span class="mono">clone_app/browser_extension</span> folder.</div></div></div></div>
        <h2 style="margin-top:22px;font-size:15px">Keyboard shortcuts</h2>
        <div class="card"><dl class="shortcuts">
          <dt><kbd>${modLabel}F</kbd></dt><dd>Search</dd>
          <dt><kbd>${modLabel}N</kbd></dt><dd>New item</dd>
          <dt><kbd>${modLabel}⇧N</kbd></dt><dd>Add item by identifier</dd>
          <dt><kbd>${modLabel}I</kbd></dt><dd>Import…</dd>
          <dt><kbd>${modLabel}E</kbd></dt><dd>Export selected items (or the library)…</dd>
          <dt><kbd>${modLabel},</kbd></dt><dd>Settings</dd>
          <dt><kbd>↑</kbd> <kbd>↓</kbd></dt><dd>Move selection (with <kbd>⇧</kbd> to extend)</dd>
          <dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>Collapse / expand attachments</dd>
          <dt><kbd>↵</kbd></dt><dd>Open PDF</dd>
          <dt><kbd>⌫</kbd></dt><dd>Move to Trash</dd>
          <dt><kbd>${modLabel}C</kbd></dt><dd>Copy BibTeX of the selected items</dd>
          <dt><kbd>${modLabel}⇧C</kbd></dt><dd>Copy citation keys</dd>
          <dt><kbd>${modLabel}T</kbd></dt><dd>Edit tags of the selected items</dd>
          <dt><kbd>${modLabel}A</kbd></dt><dd>Select all</dd>
          <dt><kbd>Esc</kbd></dt><dd>Clear search / close dialogs</dd>
        </dl></div></section>
      </div></div>
    </div>`;
  document.body.appendChild(view);
  bind();
  refreshPreview(s.citekey_pattern);
  refreshStats();
  statsTimer = setInterval(refreshStats, 2000);
  const sel = view.querySelector('[data-key="_preset"]');
  sel.value = presetMatch ? presetMatch.pattern : 'custom';
  setNav(sectionId || 'general', !!sectionId);
}

export function closeSettings() {
  if (!view) return;
  clearInterval(statsTimer);
  view.remove();
  view = null;
  emit('focus-list');
}
export const settingsOpen = () => !!view;

function setNav(id, scroll = true) {
  view.querySelectorAll('[data-nav]').forEach((b) => b.classList.toggle('active', b.dataset.nav === id));
  if (scroll) view.querySelector(`#set-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

const refreshPreview = debounce(async (pattern) => {
  if (!view) return;
  const box = view.querySelector('[data-preview]');
  try {
    const res = await api.citekeyPreview(pattern);
    const names = ['Example (Cui, Cirac & Bañuls 2015)', ...state.items.slice(0, 3).map((i) => i.title)];
    box.innerHTML = res.examples.map((k, i) => `<div class="ex"><code>${escapeHtml(k)}</code><span>${escapeHtml(names[i] || '')}</span></div>`).join('');
  } catch (e) { box.textContent = e.message; }
}, 200);

async function refreshStats() {
  if (!view) return;
  try {
    const st = await api.stats();
    view.querySelector('[data-data-dir]').innerHTML = `Your library lives in <span class="mono">${escapeHtml(st.data_dir)}</span> — back up this folder to keep everything (database, PDFs, settings).`;
    view.querySelector('[data-stats]').innerHTML = [
      [st.counts.library, 'items'], [st.attachments, 'attachments'], [st.counts.trash, 'in Trash'],
      [st.fulltext.documents, 'PDFs indexed'], [formatBytes(st.fulltext.bytes), 'index size'],
      [st.fulltext.pages.toLocaleString(), 'pages indexed'],
    ].map(([v, l]) => `<div class="stat"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');
    const ix = st.indexer;
    const desc = view.querySelector('[data-index-desc]');
    const prog = view.querySelector('[data-index-progress]');
    if (ix.running) {
      desc.textContent = `Indexing ${ix.done + 1} of ${ix.total}: ${ix.current}`;
      prog.hidden = false; prog.firstElementChild.style.width = `${(ix.done / Math.max(1, ix.total)) * 100}%`;
    } else {
      prog.hidden = true;
      desc.textContent = `${plural(st.fulltext.documents, 'PDF')} of ${plural(st.attachments, 'attachment')} indexed · ${formatBytes(st.fulltext.bytes)}`
        + `${st.fulltext.fts5 ? '' : ' · FTS5 unavailable (slower fallback)'}${ix.last_run ? ` · last run ${formatDate(ix.last_run, true)}` : ''}${ix.errors ? ` · ${ix.errors} failed` : ''}`;
    }
  } catch { /* server gone; ignore */ }
}

function bind() {
  view.querySelector('[data-close]').onclick = closeSettings;
  view.querySelector('.settings-nav').addEventListener('click', (e) => { const b = e.target.closest('[data-nav]'); if (b) setNav(b.dataset.nav); });
  const content = view.querySelector('.settings-content');
  content.addEventListener('scroll', debounce(() => {
    const secs = [...view.querySelectorAll('.settings-section')];
    const cur = secs.find((s) => s.getBoundingClientRect().bottom > 140) || secs[0];
    setNav(cur.id.replace('set-', ''), false);
  }, 60));

  view.addEventListener('change', async (e) => {
    const el = e.target;
    const key = el.dataset.key;
    if (!key) return;
    if (key === '_preset') {
      if (el.value === 'custom') { view.querySelector('[data-pattern]').focus(); return; }
      view.querySelector('[data-pattern]').value = el.value;
      refreshPreview(el.value);
      await save({ citekey_pattern: el.value }, el);
      return;
    }
    const value = el.type === 'checkbox' ? el.checked : (el.type === 'range' ? Number(el.value) : el.value);
    await save({ [key]: value }, el);
    if (key === 'default_search_mode') emit('set-search-mode', value);
    if (key === 'fulltext_auto_index' && value) api.stats();
  });
  const fs = view.querySelector('[data-key="font_size"]');
  fs.addEventListener('input', () => {
    view.querySelector('[data-fs-label]').textContent = `${fs.value}px`;
    document.documentElement.style.setProperty('--fs', `${fs.value}px`);
  });
  view.querySelectorAll('[data-seg]').forEach((seg) => seg.addEventListener('click', async (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    seg.querySelectorAll('button').forEach((x) => x.classList.toggle('active', x === b));
    await save({ [seg.dataset.seg]: b.dataset.v }, seg);
  }));

  // citation key pattern
  const pat = view.querySelector('[data-pattern]');
  const err = view.querySelector('[data-pattern-error]');
  const savePattern = debounce(async () => {
    const v = pat.value.trim();
    err.hidden = true;
    if (!v) { err.textContent = 'The pattern cannot be empty.'; err.hidden = false; return; }
    const unknown = (v.match(/\{[^}]*\}/g) || []).filter((t) => !TOKENS.map((x) => x.toLowerCase()).includes(t.toLowerCase()) && !/^\{(authors2|authetal)\}$/i.test(t));
    if (unknown.length) { err.textContent = `Unknown token${unknown.length > 1 ? 's' : ''}: ${unknown.join(', ')}`; err.hidden = false; }
    const preset = state.settingsMeta.citekeyPresets.find((p) => p.pattern === v);
    view.querySelector('[data-key="_preset"]').value = preset ? preset.pattern : 'custom';
    await save({ citekey_pattern: v }, pat);
  }, 500);
  pat.addEventListener('input', () => { refreshPreview(pat.value); savePattern(); });
  view.querySelector('.token-list').addEventListener('click', (e) => {
    const t = e.target.closest('[data-token]');
    if (!t) return;
    const s = pat.selectionStart ?? pat.value.length, en = pat.selectionEnd ?? s;
    pat.value = pat.value.slice(0, s) + t.dataset.token + pat.value.slice(en);
    pat.focus(); pat.selectionStart = pat.selectionEnd = s + t.dataset.token.length;
    pat.dispatchEvent(new Event('input'));
  });
  view.querySelector('[data-regen-all]').onclick = async () => {
    const n = state.counts.library;
    if (!(await confirmDialog(`Regenerate citation keys for all ${plural(n, 'item')}?`,
      { title: 'Regenerate all keys', confirm: 'Regenerate', detail: `Existing keys are replaced using the pattern ${state.settings.citekey_pattern}. Documents that cite the old keys will need updating.` }))) return;
    const all = await api.listItems({ view: 'library' });
    await A.regenerateCitekeys(all.items.map((i) => i.id));
    refreshPreview(pat.value);
  };

  const email = view.querySelector('[data-email]');
  email.addEventListener('change', () => save({ contact_email: email.value.trim() }, email));

  view.querySelector('[data-rebuild]').onclick = async () => {
    await api.rebuildFulltext();
    toast('Rebuilding the full-text index…');
    setTimeout(refreshStats, 300);
  };
  view.querySelector('[data-import]').onclick = () => emit('open-import');
  view.querySelector('[data-export]').onclick = async () => (await import('./exportDialog.js')).openExport(null);
  view.querySelector('[data-reset]').onclick = async () => {
    if (!(await confirmDialog('Reset all settings to their defaults?', { confirm: 'Reset' }))) return;
    const res = await api.resetSettings();
    state.settings = res.settings;
    applyAppearance();
    emit('settings');
    openSettings();
    toast('Settings reset', { type: 'success' });
  };
}
