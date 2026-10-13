// Settings page (full-screen view). Changes save immediately.
import * as A from '../actions.js';
import { api } from '../api.js';
import { icon } from '../icons.js';
import { emit, state } from '../store.js';
import { confirmDialog, promptDialog } from '../ui/dialog.js';
import { toast, toastError } from '../ui/toast.js';
import { debounce, escapeHtml, formatBytes, formatDate, modLabel, plural, store } from '../util.js';
import { baseMacros } from './notes/macros.js';

const PHYSICS_SHOWN = ['\\vb', '\\dv', '\\pdv', '\\qty', '\\abs', '\\norm', '\\expval', '\\mel', '\\comm', '\\dd', '\\grad', '\\mqty']
  .map((c) => `<span class="mono">${c}</span>`).join(', ');

const SECTIONS = [
  ['general', 'General', 'palette'],
  ['citekeys', 'Citation Keys', 'key'],
  ['search', 'Search & Full Text', 'search'],
  ['adding', 'Adding Items', 'plus'],
  ['io', 'Import & Export', 'export'],
  ['online', 'Online Services', 'globe'],
  ['backups', 'Backups', 'restore'],
  ['devices', 'Devices', 'tablet'],
  ['library', 'Library & Shortcuts', 'database'],
];

const TOKENS = ['{auth}', '{Auth}', '{AUTH}', '{authEtAl}', '{AuthEtAl}', '{authors2}', '{year}', '{yy}', '{title}', '{Title}', '{shorttitle}', '{ShortTitle}', '{journal}', '{doi}'];

let view, statsTimer, pairing = null;   // pairing: { links, expires, timer, known }

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

const ASK = [['never', 'Never'], ['ask', 'Ask'], ['always', 'Always']];

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
          ${switchRow('notes_enabled', 'Notes', 'Markdown notes for papers and ideas, stored as files in your data folder. Turn off to hide Notes in the sidebar and the item pane.')}
          ${selectRow('notes_trash_retention', 'Empty the notes trash', 'Deleted notes, and images no note uses any more, are deleted for good after this long.',
    [['never', 'Never'], ['1d', 'After 1 day'], ['1w', 'After 1 week'], ['2w', 'After 2 weeks'], ['1m', 'After 1 month'], ['6m', 'After 6 months']])}
          ${segmentedRow('notes_width', 'Note width', 'A readable column, or the whole width of the note pane (more room when the notes list is hidden).', [['readable', 'Readable'], ['full', 'Full']])}
          ${selectRow('notes_equation_numbers', 'Equation numbers', 'A $$ equation gets a number when it has a \\label{name} (refer to it with \\eqref{name}); equation and align environments are numbered as in LaTeX, and \\tag{…} sets a number. Or number every display equation.',
    [['labelled', 'Labelled equations'], ['all', 'Every display equation']])}
          ${switchRow('notes_math_physics', 'Physics package commands', `In formulas: ${PHYSICS_SHOWN}… as in the LaTeX physics package.`)}
          <div class="setting stack"><div class="s-text"><div class="s-title">Math macros ${saved}</div>
            <div class="s-desc">Your own commands for every note, one per line, e.g. <span class="mono">\\newcommand{\\R}{\\mathbb{R}}</span> or <span class="mono">\\DeclareMathOperator{\\Var}{Var}</span>. A \\newcommand written in a note's formula works in all formulas of that note.</div></div>
            <textarea class="input mono" data-key="notes_math_macros" rows="4" spellcheck="false" placeholder="\\newcommand{\\R}{\\mathbb{R}}">${escapeHtml(s.notes_math_macros || '')}</textarea>
            <div class="s-desc" data-macro-check></div></div>
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

      <section class="settings-section" id="set-adding"><h2>Adding Items</h2>
        <p class="lead">What happens right after items are added — by identifier, by dropping PDFs, by importing a file or with the browser button.
          <b>Ask</b> shows a checkbox, ticked, where you add the items, so you decide each time. You can always run these later from the item menu.</p>
        <div class="card">
          ${segmentedRow('add_fetch_metadata', 'Retrieve metadata for imported items', 'Look up imported entries online (Crossref, arXiv) in the background, then review the proposed changes. Items added by identifier, from PDFs or with the browser button are always looked up.', ASK)}
          ${segmentedRow('add_find_pdf', 'Find an available PDF', 'Look for an open-access PDF of new items that have none, in the background.', ASK)}
          ${segmentedRow('add_fetch_references', 'Fetch reference lists', 'Fetch the list of papers a new item cites, in the background. They appear in the item pane under References and build the citation graph. Needs a DOI or arXiv ID.', ASK)}
          ${segmentedRow('import_tags', 'Turn imported keywords into tags', 'Keywords in BibTeX/RIS/EndNote files become tags. When not, they stay in the Keywords field. Exports always write tags back as keywords.', ASK)}
        </div></section>

      <section class="settings-section" id="set-io"><h2>Import &amp; Export</h2><p class="lead">BibTeX, RIS, EndNote (tagged .enw and XML) and CSL JSON are supported in both directions.</p>
        <div class="card">
          ${selectRow('export_format', 'Default export format', 'Pre-selected in the Export dialog.', [['bibtex', 'BibTeX'], ['ris', 'RIS'], ['endnote', 'EndNote (.enw)'], ['endnote_xml', 'EndNote XML'], ['csljson', 'CSL JSON']])}
          ${switchRow('bibtex_unicode', 'Keep Unicode in BibTeX', 'On: write “Bañuls” as is (biber/BibLaTeX). Off: escape as “Ba{\\~{n}}uls” for classic BibTeX.')}
          ${switchRow('export_abstract', 'Include abstracts when exporting', 'Turn off for smaller .bib files.')}
          ${switchRow('import_skip_duplicates', 'Untick duplicates when importing', 'Entries whose DOI or title+year already exist start unticked in the import preview.')}
          <div class="setting"><div class="s-text"><div class="s-title">Transfer</div><div class="s-desc">Bring references in or take your whole library elsewhere.</div></div>
            <div class="s-control"><button class="btn" data-import>${icon('import')} Import…</button><button class="btn" data-export>${icon('export')} Export Library…</button></div></div>
        </div></section>

      <section class="settings-section" id="set-online"><h2>Online Services</h2><p class="lead">Used for Retrieve Metadata, Add by Identifier, Find Available PDF and reference lists (Crossref, OpenAlex, arXiv, Open Library, Unpaywall, Semantic Scholar).</p>
        <div class="card">
          <div class="setting stack"><div class="s-text"><div class="s-title">Contact e-mail ${saved}</div>
            <div class="s-desc">Crossref and Unpaywall ask clients to identify themselves; with an e-mail you get faster, more reliable responses. It is only sent to those services.</div></div>
            <input class="input" type="email" data-email placeholder="you@university.edu" value="${escapeHtml(s.contact_email)}"></div>
          ${switchRow('references_auto', 'Fetch reference lists with Retrieve Metadata', 'When metadata of existing items is retrieved, also fetch the list of papers they cite, in the background. For new items see Adding Items. You can always fetch them from the item menu.')}
          <div class="setting stack"><div class="s-text"><div class="s-title">API keys for reference lists (optional) ${saved}</div>
            <div class="s-desc">Reference lists come from Semantic Scholar, then OpenAlex, then Crossref. Without a key, OpenAlex allows only a few lookups a day and Semantic Scholar shares a rate limit with everyone. Free keys: openalex.org/settings/api and semanticscholar.org/product/api. Each key is only sent to its own service.</div></div>
            <input class="input" type="text" data-apikey="openalex_api_key" placeholder="OpenAlex API key" spellcheck="false" autocomplete="off" value="${escapeHtml(s.openalex_api_key || '')}">
            <input class="input" type="text" data-apikey="semantic_scholar_api_key" placeholder="Semantic Scholar API key" spellcheck="false" autocomplete="off" value="${escapeHtml(s.semantic_scholar_api_key || '')}" style="margin-top:6px"></div>
        </div></section>

      <section class="settings-section" id="set-backups"><h2>Backups</h2>
        <p class="lead">A backup holds the library database (items, folders, tags, annotations), your settings and all notes with their images.
          The PDFs of your items are not included — back up <span class="mono">data/pdfs</span> separately.</p>
        <div class="card">
          ${segmentedRow('backup_interval', 'Back up automatically', 'When the app is running. Also before emptying the Trash, deleting items for good, importing and regenerating citation keys.', [['off', 'Off'], ['daily', 'Daily'], ['weekly', 'Weekly']])}
          ${selectRow('backup_keep', 'Keep', 'Older backups are deleted.', [[7, 'Last 7 backups'], [14, 'Last 14 backups'], [30, 'Last 30 backups'], [90, 'Last 90 backups']])}
          <div class="setting stack"><div class="s-text"><div class="s-title">Backup folder ${saved}</div>
            <div class="s-desc">Leave empty to use the backups folder inside your data folder. A folder synced by iCloud Drive or Dropbox keeps a copy off this computer.</div></div>
            <input class="input mono" type="text" data-backup-dir spellcheck="false" autocomplete="off" placeholder="Default: data/backups" value="${escapeHtml(s.backup_dir || '')}">
            <div class="form-error" data-backup-dir-error hidden></div></div>
          <div class="setting stack"><div style="display:flex;align-items:center;gap:12px"><div class="s-text"><div class="s-title">Backups</div>
            <div class="s-desc" data-backup-desc>Loading…</div></div>
            <div class="s-control"><button class="btn" data-backup-now>${icon('download')} Back Up Now</button></div></div>
            <div class="backup-list" data-backup-list></div></div>
        </div></section>

      <section class="settings-section" id="set-devices"><h2>Devices</h2>
        <p class="lead">Use the library from an iPad or a phone on the same network. Each device is paired once and stays paired until you remove it here.
          This computer never needs pairing.</p>
        <div class="card">
          <div class="setting stack" data-device-add><div class="s-desc">Loading…</div></div>
          <div class="setting stack"><div class="s-text"><div class="s-title">Paired devices</div>
            <div class="s-desc" data-devices-desc></div></div>
            <div class="backup-list" data-device-list></div></div>
        </div></section>

      <section class="settings-section" id="set-library"><h2>Library &amp; Shortcuts</h2><p class="lead" data-data-dir></p>
        <div class="card"><div class="stat-grid" data-stats></div></div>
        <h2 style="margin-top:22px;font-size:15px">Browser button</h2>
        <div class="card"><div class="setting"><div class="s-text"><div class="s-title">Save to Library (Chrome)</div>
          <div class="s-desc">Save the article you're reading with one click. In Chrome open <span class="mono">chrome://extensions</span>, turn on Developer mode,
          choose “Load unpacked” and select the <span class="mono">browser_extension</span> folder.</div></div></div></div>
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
          <dt><kbd>⌫</kbd></dt><dd>Move to Trash — inside a folder: remove from that folder (<kbd>${modLabel}⌫</kbd> moves to Trash)</dd>
          <dt><kbd>${modLabel}C</kbd></dt><dd>Copy BibTeX of the selected items</dd>
          <dt><kbd>${modLabel}⇧C</kbd></dt><dd>Copy citation keys</dd>
          <dt><kbd>${modLabel}T</kbd></dt><dd>Edit tags of the selected items</dd>
          <dt><kbd>${modLabel}A</kbd></dt><dd>Select all</dd>
          <dt><kbd>Esc</kbd></dt><dd>Clear search / close dialogs</dd>
          <dt><kbd>${modLabel}⇧L</kbd></dt><dd>Switch between Library and Notes</dd>
          <dt>Back / Forward</dt><dd>Return to the previous note or library view (mouse buttons, <kbd>${modLabel}[</kbd> / <kbd>Alt←</kbd>)</dd>
        </dl></div>
        <h2 style="margin-top:22px;font-size:15px">In the notes list</h2>
        <div class="card"><dl class="shortcuts">
          <dt>${modLabel}-click, ⇧-click</dt><dd>Select several notes and folders (or <kbd>⇧↑</kbd> <kbd>⇧↓</kbd>)</dd>
          <dt><kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd></dt><dd>Move; fold and unfold folders</dd>
          <dt><kbd>↵</kbd></dt><dd>Open the note</dd>
          <dt><kbd>F2</kbd></dt><dd>Rename the note or folder</dd>
          <dt><kbd>⌫</kbd></dt><dd>Move the selection to the notes trash (with Undo)</dd>
          <dt><kbd>${modLabel}N</kbd></dt><dd>New note</dd>
          <dt><kbd>${modLabel}E</kbd></dt><dd>Read / edit the open note</dd>
        </dl></div></section>
      </div></div>
    </div>`;
  document.body.appendChild(view);
  bind();
  refreshPreview(s.citekey_pattern);
  refreshStats();
  refreshBackups();
  refreshDevices();
  statsTimer = setInterval(refreshStats, 2000);
  const sel = view.querySelector('[data-key="_preset"]');
  sel.value = presetMatch ? presetMatch.pattern : 'custom';
  setNav(sectionId || 'general', !!sectionId);
}

export function closeSettings() {
  if (!view) return;
  clearInterval(statsTimer);
  stopPairing();
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

async function refreshBackups() {
  if (!view) return;
  const desc = view.querySelector('[data-backup-desc]');
  const list = view.querySelector('[data-backup-list]');
  try {
    const st = await api.backups();
    if (!view) return;
    const last = st.last ? `Last backup ${formatDate(st.last.created, true)}` : 'No backup yet';
    desc.innerHTML = `${escapeHtml(last)} · in <span class="mono">${escapeHtml(st.dir)}</span>`
      + (st.error ? `<div class="form-error">${escapeHtml(st.error)}</div>` : '');
    list.innerHTML = st.backups.length ? st.backups.map((b) => `<div class="backup-row" data-backup="${escapeHtml(b.name)}">
        <div class="b-main"><div class="b-when">${escapeHtml(formatDate(b.created, true))}${b.damaged ? ' · <span class="form-error">cannot be read</span>' : ''}</div>
          <div class="b-what">${b.items != null ? `${plural(b.items, 'item')} · ${plural(b.notes ?? 0, 'note')} · ` : ''}${formatBytes(b.size)}${b.reason && b.reason !== 'manual' && b.reason !== 'scheduled' ? ` · ${escapeHtml(b.reason)}` : ''}</div></div>
        <button class="btn btn-sm" data-restore ${b.damaged ? 'disabled' : ''}>${icon('restore')} Restore…</button></div>`).join('')
      : '';
  } catch (e) { desc.textContent = e.message; }
}

// -- Devices (issue #34) -------------------------------------------------------------------------
function stopPairing() {
  if (pairing) clearInterval(pairing.timer);
  pairing = null;
}

function relTime(iso) {
  if (!iso) return 'never';
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return formatDate(iso, true);
}

function renderPairing(box) {
  const now = Date.now() / 1000;
  const left = Math.max(0, Math.round(pairing.expires - now));
  if (left <= 0) {
    stopPairing();
    box.innerHTML = `<div class="s-desc">The pairing code expired.</div>
      <div><button class="btn" data-pair-new>${icon('plus')} Add a Device</button></div>`;
    return;
  }
  const link = pairing.links[pairing.index] || pairing.links[0];
  const timeLeft = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
  const choices = pairing.links.length > 1 ? `<div class="pair-addrs">Address: ${pairing.links.map((l, i) =>
    `<button class="token${i === pairing.index ? ' active' : ''}" data-pair-addr="${i}">${escapeHtml(l.label)}</button>`).join(' ')}</div>` : '';
  box.innerHTML = `<div class="pair-box">
      ${link.qr ? `<div class="pair-qr">${link.qr}</div>`
        : `<div class="pair-qr pair-qr-missing">No QR code: the <span class="mono">segno</span> package is missing.
          Run <span class="mono">pip install segno</span> and restart Synapse — or use the link.</div>`}
      <div class="pair-text">
        <div class="s-title">Scan with the other device's camera</div>
        <div class="s-desc">Or open this link on it: <span class="mono pair-url">${escapeHtml(link.url.split('#')[0])}…</span>
          <button class="btn btn-sm" data-pair-copy>${icon('copy')} Copy Link</button></div>
        ${choices}
        <div class="s-desc">If the other device can't open the page, try another address. Both devices must be on the same network.</div>
        <div class="s-desc">The code works once and expires in <b data-pair-left>${timeLeft}</b>.</div>
        <div><button class="btn btn-sm" data-pair-cancel>Cancel</button></div>
      </div></div>`;
}

async function refreshDevices() {
  if (!view) return;
  const add = view.querySelector('[data-device-add]');
  const list = view.querySelector('[data-device-list]');
  const desc = view.querySelector('[data-devices-desc]');
  let st;
  try { st = await api.devices(); } catch (e) { desc.textContent = e.message; return; }
  if (!view) return;
  if (pairing) {
    const fresh = st.devices.filter((d) => !pairing.known.has(d.id));
    if (fresh.length) {
      stopPairing();
      toast(`${fresh.map((d) => d.name).join(', ')} paired`, { type: 'success' });
    }
  }
  if (!pairing) {
    if (!st.lan) {
      add.innerHTML = `<div class="s-title">Not reachable from other devices</div>
        <div class="s-desc">Synapse only answers this computer. To use it from an iPad or a phone, stop it and start it again with
          <span class="mono">python server.py --lan</span>, then come back here.</div>`;
    } else if (!st.this_computer) {
      add.innerHTML = `<div class="s-desc">New devices are added on the computer running Synapse (Settings → Devices → Add a Device).</div>`;
    } else {
      const where = st.addresses.length ? ` Other devices reach it at <span class="mono">${escapeHtml(st.addresses[0].url)}</span>.` : '';
      add.innerHTML = `<div style="display:flex;align-items:center;gap:12px"><div class="s-text"><div class="s-title">Add a device</div>
          <div class="s-desc">Shows a QR code to scan with the iPad or phone.${where}</div></div>
          <div class="s-control"><button class="btn" data-pair-new>${icon('plus')} Add a Device</button></div></div>`;
    }
  }
  desc.textContent = st.devices.length ? '' : 'No devices yet.';
  list.innerHTML = st.devices.map((d) => `<div class="backup-row" data-device="${escapeHtml(d.id)}">
      <div class="b-main"><div class="b-when">${escapeHtml(d.name)}${d.id === st.current ? ' <span class="device-this">This device</span>' : ''}</div>
        <div class="b-what">Paired ${escapeHtml(formatDate(d.created, true))} · last used ${escapeHtml(relTime(d.last_seen))}${d.last_addr ? ` from ${escapeHtml(d.last_addr)}` : ''}</div></div>
      <button class="btn btn-sm" data-device-rename>${icon('edit')} Rename…</button>
      <button class="btn btn-sm" data-device-remove>${d.id === st.current ? 'Sign Out' : 'Remove…'}</button></div>`).join('');
  view.__devices = st;
}

async function startPairing() {
  const box = view.querySelector('[data-device-add]');
  try {
    const res = await api.newPairing();
    const known = new Set((view.__devices?.devices || []).map((d) => d.id));
    stopPairing();
    pairing = { links: res.links, expires: res.expires, index: 0, known, tick: 0 };
    renderPairing(box);
    pairing.timer = setInterval(() => {
      if (!view || !pairing) return;
      const left = Math.max(0, Math.round(pairing.expires - Date.now() / 1000));
      const el = box.querySelector('[data-pair-left]');
      if (left <= 0 || !el) { renderPairing(box); return; }
      el.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
      if (++pairing.tick % 3 === 0) refreshDevices();   // notice when the device has paired
    }, 1000);
  } catch (e) { toastError(e); }
}

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
  // Math macros: say what the definitions define, and which lines are not definitions
  const mt = view.querySelector('[data-key="notes_math_macros"]');
  const mcheck = () => {
    const r = baseMacros({ physics: false, text: mt.value });
    const out = view.querySelector('[data-macro-check]');
    out.innerHTML = (r.names.length ? `Defines ${r.names.map((n) => `<span class="mono">${escapeHtml(n)}</span>`).join(', ')}.` : '')
      + (r.problems.length ? ` <span class="warn">Not a definition: <span class="mono">${escapeHtml(r.problems[0].slice(0, 60))}</span>${r.problems.length > 1 ? ` (and ${r.problems.length - 1} more)` : ''}</span>` : '');
  };
  mt.addEventListener('input', mcheck);
  mcheck();
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
      { title: 'Regenerate all keys', confirm: 'Regenerate', detail: `Existing keys are replaced using the pattern ${state.settings.citekey_pattern}. Your notes are updated to the new keys automatically (after a backup); LaTeX documents that cite the old keys will need updating.` }))) return;
    const all = await api.listItems({ view: 'library' });
    await A.regenerateCitekeys(all.items.map((i) => i.id));
    refreshPreview(pat.value);
  };

  const email = view.querySelector('[data-email]');
  email.addEventListener('change', () => save({ contact_email: email.value.trim() }, email));
  view.querySelectorAll('[data-apikey]').forEach((inp) => inp.addEventListener('change', () => save({ [inp.dataset.apikey]: inp.value.trim() }, inp)));

  const devAdd = view.querySelector('[data-device-add]');
  devAdd.addEventListener('click', async (e) => {
    if (e.target.closest('[data-pair-new]')) startPairing();
    else if (e.target.closest('[data-pair-cancel]')) { stopPairing(); refreshDevices(); }
    else if (e.target.closest('[data-pair-addr]') && pairing) {
      pairing.index = Number(e.target.closest('[data-pair-addr]').dataset.pairAddr);
      renderPairing(devAdd);
    } else if (e.target.closest('[data-pair-copy]') && pairing) {
      const link = pairing.links[pairing.index] || pairing.links[0];
      try { await navigator.clipboard.writeText(link.url); toast('Link copied — it works once', { type: 'success' }); } catch (err) { toastError(err); }
    }
  });
  view.querySelector('[data-device-list]').addEventListener('click', async (e) => {
    const row = e.target.closest('[data-device]');
    if (!row) return;
    const id = row.dataset.device;
    const dev = (view.__devices?.devices || []).find((d) => d.id === id);
    if (!dev) return;
    const self = id === view.__devices.current;
    if (e.target.closest('[data-device-rename]')) {
      const name = await promptDialog('Name', { title: 'Rename device', value: dev.name, confirm: 'Rename' });
      if (!name || !name.trim()) return;
      try { await api.renameDevice(id, name.trim()); } catch (err) { toastError(err); }
      refreshDevices();
    } else if (e.target.closest('[data-device-remove]')) {
      const ok = await confirmDialog(self ? 'Sign out this device?' : `Remove ${dev.name}?`, {
        title: self ? 'Sign out' : 'Remove device', confirm: self ? 'Sign Out' : 'Remove', danger: true,
        detail: self ? 'To use the library here again, pair this device again from the computer running Synapse.'
          : 'It can no longer open the library until it is paired again.' });
      if (!ok) return;
      try { await api.removeDevice(id); } catch (err) { toastError(err); return; }
      if (self) { location.reload(); return; }
      refreshDevices();
    }
  });

  const bdir = view.querySelector('[data-backup-dir]');
  bdir.addEventListener('change', async () => {
    const errEl = view.querySelector('[data-backup-dir-error]');
    errEl.hidden = true;
    try {
      const res = await api.saveSettings({ backup_dir: bdir.value.trim() });
      state.settings = res.settings;
      const pill = bdir.closest('.setting').querySelector('.saved-pill');
      pill.classList.add('show'); setTimeout(() => pill.classList.remove('show'), 1200);
      refreshBackups();
    } catch (e) { errEl.textContent = e.message; errEl.hidden = false; }
  });
  view.querySelector('[data-backup-now]').onclick = async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    try { await api.backupNow(); toast('Backup made', { type: 'success' }); } catch (err) { toastError(err); }
    b.disabled = false;
    refreshBackups();
  };
  view.querySelector('[data-backup-list]').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-restore]');
    if (!btn) return;
    const row = btn.closest('[data-backup]');
    const when = row.querySelector('.b-when').textContent;
    if (!(await confirmDialog(`Restore the backup of ${when}?`, {
      title: 'Restore backup', confirm: 'Restore', danger: true,
      detail: 'Your library, settings and notes go back to how they were then. The current state is backed up first, so this can be undone by restoring that backup. PDFs are not changed.',
    }))) return;
    btn.disabled = true;
    try {
      const res = await api.restoreBackup(row.dataset.backup);
      toast(`Backup restored. The state before it is saved as ${res.safety_backup}.`, { type: 'success' });
      setTimeout(() => location.reload(), 800);
    } catch (err) { toastError(err); btn.disabled = false; }
  });

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
