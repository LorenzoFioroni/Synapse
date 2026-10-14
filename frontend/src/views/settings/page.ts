// The Settings page's markup: its sections and the rows that make them up. The page reads the
// settings when it is built; settingsView.ts binds the controls and saves each change.
import { icon } from '../../icons.js';
import { state } from '../../store.js';
import type { Settings, SettingsResponse } from '../../types.js';
import { escapeHtml, modLabel } from '../../util.js';

const PHYSICS_SHOWN = ['\\vb', '\\dv', '\\pdv', '\\qty', '\\abs', '\\norm', '\\expval', '\\mel', '\\comm', '\\dd', '\\grad', '\\mqty']
  .map((c) => `<span class="mono">${c}</span>`).join(', ');

const SECTIONS: [string, string, string][] = [
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

export const TOKENS = ['{auth}', '{Auth}', '{AUTH}', '{authEtAl}', '{AuthEtAl}', '{authors2}', '{year}', '{yy}', '{title}', '{Title}', '{shorttitle}', '{ShortTitle}', '{journal}', '{doi}'];

/** The "Saved" mark in a row, shown for a moment after a change is saved. */
export function flashSaved(scope: Element | null | undefined): void {
  const pill = scope?.querySelector('.saved-pill');
  if (pill) { pill.classList.add('show'); setTimeout(() => pill.classList.remove('show'), 1200); }
}

const ASK: [string, string][] = [['never', 'Never'], ['ask', 'Ask'], ['always', 'Always']];

const saved = `<span class="saved-pill">${icon('check', 'icon-sm')} Saved</span>`;

type Key = keyof Settings | '_preset';
type Options = [string | number, string][];
const setting = (key: Key): unknown => (key === '_preset' ? undefined : state.settings[key]);

function switchRow(key: Key, title: string, desc: string): string {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div><div class="s-desc">${desc}</div></div>
    <div class="s-control">${saved}<label class="switch"><input type="checkbox" data-key="${key}" ${setting(key) ? 'checked' : ''}><span></span></label></div></div>`;
}

function selectRow(key: Key, title: string, desc: string, options: Options): string {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div><div class="s-desc">${desc}</div></div>
    <div class="s-control">${saved}<select class="select" data-key="${key}">${options.map(([v, l]) => `<option value="${v}" ${String(setting(key)) === String(v) ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}</select></div></div>`;
}

function segmentedRow(key: Key, title: string, desc: string, options: Options): string {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div><div class="s-desc">${desc}</div></div>
    <div class="s-control">${saved}<div class="segmented" data-seg="${key}">${options.map(([v, l]) => `<button data-v="${v}" class="${setting(key) === v ? 'active' : ''}">${l}</button>`).join('')}</div></div></div>`;
}

/** The whole page for settings ``s``. */
export function settingsHtml(s: Settings, meta: Partial<SettingsResponse>): string {
  return `
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
          ${selectRow('_preset', 'Style', 'Pick a common style, or write your own pattern below.', [...(meta.citekeyPresets || []).map((p): [string, string] => [p.pattern, p.label]), ['custom', 'Custom pattern…']])}
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
          ${selectRow('default_search_mode', 'Default search scope', 'Used when the app opens. You can switch scope from the search box at any time.', Object.entries(meta.searchModes || {}))}
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
          ${segmentedRow('add_fetch_metadata', 'Retrieve metadata for imported items', 'Look up imported entries online (Crossref, arXiv) in the background, then review the proposed changes. Items added by identifier or from PDFs are always looked up. The browser button asks with its own “Complete metadata online” checkbox, unticked when this is Never.', ASK)}
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
            <div class="s-desc">Crossref, OpenAlex and Unpaywall ask clients to identify themselves; with an e-mail you get faster, more reliable responses. It is only sent to the scholarly services Synapse looks things up in (Crossref, OpenAlex, Unpaywall, Semantic Scholar).</div></div>
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
}
