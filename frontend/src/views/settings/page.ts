// The Settings page's markup: the grouped navigation, and one page (section) per entry, of which
// one is shown at a time. The page reads the settings when it is built; settingsView.ts binds the
// controls, saves each change and switches pages.
import { icon } from '../../icons.js';
import { state } from '../../store.js';
import type { Settings, SettingsResponse } from '../../types.js';
import { escapeHtml, isMac, modLabel } from '../../util.js';

const tabMod = isMac ? '⌃' : 'Alt+';   // the tab keys (tabs/tabs.ts)

const PHYSICS_SHOWN = ['\\vb', '\\dv', '\\pdv', '\\qty', '\\abs', '\\norm', '\\expval', '\\mel', '\\comm', '\\dd', '\\grad', '\\mqty']
  .map((c) => `<span class="mono">${c}</span>`).join(', ');

/** A navigation entry: id (its section is ``#set-<id>``), label, icon, and the tile colour on phones. */
type NavEntry = [string, string, string, string];

/** The navigation, in groups. Plugins' own pages are reached from Plugins, not listed here. */
export const NAV_GROUPS: [string, NavEntry[]][] = [
  ['Preferences', [
    ['general', 'General', 'palette', '#5b6576'],
    ['notes', 'Notes', 'edit', '#d4880f'],
    ['viewer', 'PDF Viewer', 'pdf', '#e0402a'],
    ['notifications', 'Notifications', 'bell', '#c2410c'],
  ]],
  ['Library', [
    ['adding', 'Adding Items', 'plus', '#2f6fed'],
    ['citekeys', 'Citation Keys', 'key', '#7a5af5'],
    ['search', 'Search & Full Text', 'search', '#0f8b8d'],
    ['io', 'Import & Export', 'export', '#2563a8'],
    ['online', 'Online Services', 'globe', '#0e7490'],
  ]],
  ['Data & access', [
    ['backups', 'Backups', 'restore', '#1f9d55'],
    ['devices', 'Devices', 'tablet', '#3a4352'],
    ['ai', 'AI Access', 'sparkle', '#b3202c'],
  ]],
  ['Extend', [
    ['plugins', 'Plugins', 'plugin', '#4a5363'],
  ]],
  ['About', [
    ['library', 'Library', 'database', '#6b5b45'],
    ['updates', 'Updates', 'refresh', '#2f6fed'],
    ['shortcuts', 'Keyboard Shortcuts', 'keyboard', '#5b6576'],
  ]],
];

/** The group a section belongs to, shown above its title. */
const GROUP_OF = new Map(NAV_GROUPS.flatMap(([group, entries]) => entries.map(([id]) => [id, group] as [string, string])));

/** One navigation entry. Its value (phones) and badge are filled in later (settingsView.ts). */
export function navItemHtml(id: string, label: string, ic: string, color = '#5b6576', extra = ''): string {
  return `<button class="side-item" data-nav="${escapeHtml(id)}"${extra}><span class="nav-ico" style="--tile:${escapeHtml(color)}">${icon(ic)}</span>`
    + `<span class="nav-label">${escapeHtml(label)}</span><span class="nav-value" data-nav-value></span>`
    + `<span class="nav-badge" data-nav-badge hidden></span><span class="nav-count" data-nav-count hidden></span>`
    + `<span class="nav-chevron">${icon('chevron')}</span></button>`;
}

/** Show ``text`` as the badge of a navigation entry (empty: no badge). */
export function setNavBadge(view: ParentNode, id: string, text: string): void {
  const badge = view.querySelector<HTMLElement>(`.settings-nav [data-nav="${CSS.escape(id)}"] [data-nav-badge]`);
  if (!badge) return;
  badge.textContent = text;
  badge.hidden = !text;
}

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
const desc = (text: string) => (text ? `<div class="s-desc">${text}</div>` : '');

function switchRow(key: Key, title: string, text = ''): string {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div>${desc(text)}</div>
    <div class="s-control">${saved}<label class="switch"><input type="checkbox" data-key="${key}" aria-label="${escapeHtml(title)}" ${setting(key) ? 'checked' : ''}><span></span></label></div></div>`;
}

function selectRow(key: Key, title: string, text: string, options: Options): string {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div>${desc(text)}</div>
    <div class="s-control">${saved}<select class="select" data-key="${key}" aria-label="${escapeHtml(title)}">${options.map(([v, l]) => `<option value="${v}" ${String(setting(key)) === String(v) ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}</select></div></div>`;
}

function segmentedRow(key: Key, title: string, text: string, options: Options): string {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div>${desc(text)}</div>
    <div class="s-control">${saved}<div class="segmented" data-seg="${key}" role="group" aria-label="${escapeHtml(title)}">${options.map(([v, l]) => `<button data-v="${v}" class="${setting(key) === v ? 'active' : ''}">${l}</button>`).join('')}</div></div></div>`;
}

function textRow(key: Key, title: string, text: string, placeholder = ''): string {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div>${desc(text)}</div>
    <div class="s-control">${saved}<input class="input" type="text" data-key="${key}" aria-label="${escapeHtml(title)}" maxlength="200" autocomplete="name" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(String(setting(key) || ''))}"></div></div>`;
}

function colorRow(key: Key, title: string, text: string): string {
  return `<div class="setting"><div class="s-text"><div class="s-title">${title}</div>${desc(text)}</div>
    <div class="s-control">${saved}<input type="color" class="color-input" data-key="${key}" aria-label="${escapeHtml(title)} colour" value="${escapeHtml(String(setting(key) || '#000000'))}"></div></div>`;
}

/** A section's top: its group, title and one-line lead. */
function head(id: string, title: string, lead = '', leadAttrs = ''): string {
  return `<div class="page-crumb">${escapeHtml(GROUP_OF.get(id) || '')}</div><h2>${title}</h2>${lead || leadAttrs ? `<p class="lead"${leadAttrs}>${lead}</p>` : ''}`;
}

const sub = (title: string) => `<h3 class="sec-h">${title}</h3>`;

/** Reset this page: the settings with a control on it go back to their defaults. */
const resetFoot = `<div class="page-foot"><button class="link-btn" data-reset-page>Reset this page</button></div>`;

/** The theme as three pictures to pick from (saved like any segmented control). */
function themeTiles(): string {
  const tile = (v: string, label: string, art: string) => `<button data-v="${v}" class="theme-tile ${state.settings.theme === v ? 'active' : ''}" aria-label="${label} theme">
      <span class="theme-art theme-art-${v}" aria-hidden="true">${art}</span><span class="theme-name">${label}</span></button>`;
  const lines = '<i></i><i></i><i class="hl"></i>';
  return `<div class="setting stack"><div class="s-text"><div class="s-title">Theme ${saved}</div></div>
    <div class="theme-tiles" data-seg="theme" role="group" aria-label="Theme">
      ${tile('system', 'System', `<span class="half light">${lines}</span><span class="half dark">${lines}</span>`)}
      ${tile('light', 'Light', `<span class="side"></span><span class="body">${lines}</span>`)}
      ${tile('dark', 'Dark', `<span class="side"></span><span class="body">${lines}</span>`)}
    </div></div>`;
}

function shortcuts(rows: [string, string][]): string {
  return `<div class="card"><dl class="shortcuts">${rows.map(([k, d]) => `<dt>${k}</dt><dd>${d}</dd>`).join('')}</dl></div>`;
}

/** The whole page for settings ``s``. */
export function settingsHtml(s: Settings, meta: Partial<SettingsResponse>): string {
  return `
    <div class="settings-top"><button class="btn btn-ghost" data-close>${icon('arrowLeft')} Library</button>
      <span class="settings-top-sep"></span><h1>Settings</h1><span class="spacer"></span>
      <span class="settings-autosave">${icon('check', 'icon-sm')} Changes save as you make them</span></div>
    <div class="settings-main">
      <nav class="settings-nav" aria-label="Settings sections">
        <div class="settings-search-row" role="search">
          <div class="settings-search-field">${icon('search')}
            <input type="search" data-settings-search placeholder="Search settings" aria-label="Search settings" autocomplete="off" spellcheck="false">
            <button class="icon-btn" data-settings-search-clear title="Clear settings search" aria-label="Clear settings search" hidden>${icon('close', 'icon-sm')}</button>
          </div>
          <span class="settings-search-summary" data-settings-search-summary role="status" aria-live="polite" hidden></span>
        </div>
        <div class="settings-nav-list">
        ${NAV_GROUPS.map(([group, entries]) => `<div class="nav-group"><div class="nav-group-title">${group}</div><div class="nav-group-items">
          ${entries.map(([id, label, ic, color]) => navItemHtml(id, label, ic, color)).join('')}</div></div>`).join('')}
        </div>
      </nav>
      <div class="settings-content"><div class="settings-inner">
      <button class="settings-back" data-settings-back>${icon('arrowLeft')} Settings</button>
      <div class="settings-search-empty" data-settings-search-empty hidden><h2>No matching settings</h2><p>Try different words or clear the search.</p></div>

      <section class="settings-section" id="set-general">${head('general', 'General', 'How the library looks and behaves on this computer.')}
        ${sub('Appearance')}
        <div class="card">
          ${themeTiles()}
          ${segmentedRow('density', 'Row density', '', [['comfortable', 'Comfortable'], ['compact', 'Compact']])}
          <div class="setting"><div class="s-text"><div class="s-title">Text size</div></div>
            <div class="s-control">${saved}<span class="fs-small" aria-hidden="true">A</span><input type="range" min="11" max="18" step="1" value="${s.font_size}" data-key="font_size" aria-label="Text size" class="fs-range">
            <span class="fs-big" aria-hidden="true">A</span><span class="mono fs-label" data-fs-label>${s.font_size}px</span></div></div>
        </div>
        ${sub('Items')}
        <div class="card">
          ${switchRow('open_pdf_on_double_click', 'Double-click opens the PDF', 'Otherwise double-click renames the item.')}
          ${switchRow('show_subfolder_items', 'Folders include their subfolders’ items')}
          ${switchRow('confirm_trash', 'Ask before moving to the Trash', 'You can always Undo, or restore from the Trash.')}
        </div>
        ${resetFoot}</section>

      <section class="settings-section" id="set-notes">${head('notes', 'Notes', 'Markdown notes for papers and ideas, stored as files in your data folder.')}
        <div class="card">
          ${switchRow('notes_enabled', 'Notes', 'When off, Notes is hidden in the sidebar and the item pane.')}
          ${segmentedRow('notes_width', 'Note width', 'Full uses the whole note pane.', [['readable', 'Readable'], ['full', 'Full']])}
          ${selectRow('notes_trash_retention', 'Empty the notes trash', 'Deleted notes, and images no note uses, are deleted for good after this long.',
    [['never', 'Never'], ['1d', 'After 1 day'], ['1w', 'After 1 week'], ['2w', 'After 2 weeks'], ['1m', 'After 1 month'], ['6m', 'After 6 months']])}
          ${textRow('notes_export_author', 'Author of exported notes', 'Printed under the title, with the export date, when a note is exported as PDF, HTML, Markdown or LaTeX. Empty: no author line.', 'Your name')}
        </div>
        ${sub('Equations')}
        <div class="card">
          ${selectRow('notes_equation_numbers', 'Equation numbers', 'Labelled: a $$ equation with a \\label{name} is numbered (cite it with \\eqref{name}), as are equation and align environments.',
    [['labelled', 'Labelled equations'], ['all', 'Every display equation']])}
          ${switchRow('notes_math_physics', 'Physics package commands', `${PHYSICS_SHOWN}… as in LaTeX.`)}
          <div class="setting stack"><div class="s-text"><div class="s-title">Math macros ${saved}</div>
            <div class="s-desc">For every note, one per line, e.g. <span class="mono">\\newcommand{\\R}{\\mathbb{R}}</span>. A \\newcommand in a note’s formula works in the whole note.</div></div>
            <textarea class="input mono" data-key="notes_math_macros" rows="4" spellcheck="false" aria-label="Math macros" placeholder="\\newcommand{\\R}{\\mathbb{R}}">${escapeHtml(s.notes_math_macros || '')}</textarea>
            <div class="s-desc" data-macro-check></div></div>
        </div>
        ${resetFoot}</section>

      <section class="settings-section" id="set-viewer">${head('viewer', 'PDF Viewer', 'Where PDFs open, and how annotating them starts.')}
        <div class="card">
          ${segmentedRow('pdf_open_in', 'Open PDFs in', `Switch tabs with <kbd>Ctrl</kbd><kbd>Tab</kbd>, close one with <kbd>${tabMod}W</kbd>.`, [['tabs', 'Synapse Tabs'], ['browser', 'Browser Tabs']])}
          ${switchRow('annotation_snap_words', 'Highlight whole words', 'Hold Alt (⌥) while selecting to take exactly the characters you select.')}
        </div>
        ${sub('Annotation colours')}
        <p class="sec-lead">The colour each tool starts with. To recolour an annotation, select it in the viewer.</p>
        <div class="card">
          ${colorRow('annotation_color_highlight', 'Highlight', '')}
          ${colorRow('annotation_color_underline', 'Underline', '')}
          ${colorRow('annotation_color_text', 'Text box', '')}
          ${colorRow('annotation_color_box', 'Box border', '')}
          ${colorRow('annotation_fill_box', 'Box fill', 'A filled box without a border highlights an area of the page.')}
          ${colorRow('annotation_color_draw', 'Drawing', 'Pen, Pencil or finger.')}
        </div>
        ${resetFoot}</section>

      <section class="settings-section" id="set-notifications">${head('notifications', 'Notifications', 'Whether important notices may also appear through your operating system.')}
        <div class="card"><div class="setting"><div class="s-text"><div class="s-title">OS notifications</div>
          <div class="s-desc" data-desktop-notifications-desc></div></div>
          <div class="s-control"><button class="btn" data-desktop-notifications></button></div></div></div>
        <p class="sec-lead">This belongs to this browser profile. Every notice and running job stays available from the bell.</p>
      </section>

      <section class="settings-section" id="set-adding">${head('adding', 'Adding Items', 'What happens right after items are added. <b>Ask</b> puts a ticked checkbox where you add them.')}
        <div class="card">
          ${segmentedRow('add_fetch_metadata', 'Retrieve metadata for imported items', 'Look up imported entries online, then review the changes. Items added by identifier or PDF are always looked up.', ASK)}
          ${segmentedRow('add_find_pdf', 'Find an available PDF', 'For new items that have none, in the background.', ASK)}
          ${segmentedRow('add_fetch_references', 'Fetch reference lists', 'The papers a new item cites, for References and the citation graph. Needs a DOI or arXiv ID.', ASK)}
          ${segmentedRow('import_tags', 'Turn imported keywords into tags', 'Otherwise they stay in the Keywords field.', ASK)}
        </div>
        ${sub('Browser button')}
        <div class="card"><div class="setting"><div class="s-text"><div class="s-title">Save to Library (Chrome)</div>
          <div class="s-desc">Save the article you’re reading with one click. In <span class="mono">chrome://extensions</span> turn on Developer mode,
          choose “Load unpacked” and pick the <span class="mono">browser_extension</span> folder.</div></div></div></div>
        ${resetFoot}</section>

      <section class="settings-section" id="set-citekeys">${head('citekeys', 'Citation Keys', 'The keys BibTeX and LaTeX cite (<span class="mono">\\cite{key}</span>). Duplicates get a, b, c… automatically.')}
        <div class="card">
          ${selectRow('_preset', 'Style', '', [...(meta.citekeyPresets || []).map((p): [string, string] => [p.pattern, p.label]), ['custom', 'Custom pattern…']])}
          <div class="setting stack"><div class="s-text"><div class="s-title">Pattern</div>
            <div class="s-desc">Tokens in braces are replaced; the token’s case sets the output’s case.</div></div>
            <div class="pattern-row"><input class="input" data-pattern aria-label="Citation key pattern" value="${escapeHtml(s.citekey_pattern)}" spellcheck="false"></div>
            <div class="token-list">${TOKENS.map((t) => `<button class="token" data-token="${t}">${t}</button>`).join('')}</div>
            <div class="preview-box" data-preview></div><div class="form-error" data-pattern-error hidden></div></div>
          ${switchRow('citekey_auto', 'Generate keys for new items', 'Also for imported entries without a key.')}
          <div class="setting"><div class="s-text"><div class="s-title">Regenerate all keys</div><div class="s-desc">Rewrite every item’s key with the current pattern.</div></div>
            <div class="s-control"><button class="btn" data-regen-all>${icon('refresh')} Regenerate…</button></div></div>
        </div>
        ${resetFoot}</section>

      <section class="settings-section" id="set-search">${head('search', 'Search &amp; Full Text', 'The full-text index (<span class="mono">data/fulltext.db</span>) is kept apart from your library and can be rebuilt any time.')}
        <div class="card">
          <div class="setting stack"><div class="setting-line"><div class="s-text"><div class="s-title">Full-text index</div>
            <div class="s-desc" data-index-desc>Loading…</div></div>
            <div class="s-control"><button class="btn" data-rebuild>${icon('refresh')} Rebuild Index</button></div></div>
            <div class="progress" data-index-progress hidden><div style="width:0"></div></div></div>
          ${switchRow('fulltext_auto_index', 'Index PDFs automatically', 'So the text of new attachments can be searched.')}
          ${selectRow('default_search_mode', 'Default search scope', 'When the app opens. Switch it from the search box any time.', Object.entries(meta.searchModes || {}))}
        </div>
        ${resetFoot}</section>

      <section class="settings-section" id="set-io">${head('io', 'Import &amp; Export', 'BibTeX, RIS, EndNote (.enw and XML) and CSL JSON, both ways.')}
        <div class="card">
          <div class="setting"><div class="s-text"><div class="s-title">Transfer</div><div class="s-desc">Bring references in, or take your whole library elsewhere.</div></div>
            <div class="s-control"><button class="btn" data-import>${icon('import')} Import…</button><button class="btn" data-export>${icon('export')} Export Library…</button></div></div>
          ${selectRow('export_format', 'Default export format', '', [['export-formats.bibtex', 'BibTeX'], ['export-formats.ris', 'RIS'], ['export-formats.endnote', 'EndNote (.enw)'], ['export-formats.endnote_xml', 'EndNote XML'], ['export-formats.csljson', 'CSL JSON']])}
          ${switchRow('bibtex_unicode', 'Keep Unicode in BibTeX', 'For biber/BibLaTeX. Off: “Bañuls” is written “Ba{\\~{n}}uls” for classic BibTeX.')}
          ${switchRow('export_abstract', 'Include abstracts when exporting', 'Off makes smaller .bib files.')}
          ${switchRow('import_skip_duplicates', 'Untick duplicates when importing', 'Entries whose DOI or title and year are already in the library.')}
        </div>
        ${resetFoot}</section>

      <section class="settings-section" id="set-online">${head('online', 'Online Services', 'Crossref, OpenAlex, arXiv, Open Library, Unpaywall and Semantic Scholar: for metadata, PDFs and reference lists.')}
        <div class="card">
          <div class="setting stack"><div class="s-text"><div class="s-title">Contact e-mail ${saved}</div>
            <div class="s-desc">Gets faster, more reliable answers from Crossref, OpenAlex and Unpaywall. Only sent to the scholarly services.</div></div>
            <input class="input" type="email" data-email aria-label="Contact e-mail" placeholder="you@university.edu" value="${escapeHtml(s.contact_email)}"></div>
          ${switchRow('references_auto', 'Fetch reference lists with Retrieve Metadata', 'For existing items. New items follow Adding Items.')}
          <div class="setting stack"><div class="s-text"><div class="s-title">API keys for reference lists (optional) ${saved}</div>
            <div class="s-desc">Without a key, OpenAlex allows only a few lookups a day and Semantic Scholar shares a rate limit with everyone. Free keys:
              openalex.org/settings/api and semanticscholar.org/product/api. Each key only goes to its own service.</div></div>
            <input class="input" type="text" data-apikey="openalex_api_key" aria-label="OpenAlex API key" placeholder="OpenAlex API key" spellcheck="false" autocomplete="off" value="${escapeHtml(s.openalex_api_key || '')}">
            <input class="input" type="text" data-apikey="semantic_scholar_api_key" aria-label="Semantic Scholar API key" placeholder="Semantic Scholar API key" spellcheck="false" autocomplete="off" value="${escapeHtml(s.semantic_scholar_api_key || '')}"></div>
        </div>
        ${resetFoot}</section>

      <section class="settings-section" id="set-backups">${head('backups', 'Backups', 'Your library, settings and notes. PDFs are not included: back up <span class="mono">data/pdfs</span> separately.')}
        <div class="card status-card" data-backup-status>
          <span class="status-ico" data-backup-status-icon>${icon('check')}</span>
          <div class="s-text"><div class="status-title" data-backup-status-title>Loading…</div><div class="s-desc" data-backup-desc></div></div>
          <div class="s-control"><button class="btn btn-primary" data-backup-now>${icon('download')} Back Up Now</button></div>
        </div>
        ${sub('Schedule')}
        <div class="card">
          ${segmentedRow('backup_interval', 'Back up automatically', 'Also right before emptying the Trash, deleting items for good, importing and regenerating keys.', [['off', 'Off'], ['daily', 'Daily'], ['weekly', 'Weekly']])}
          ${selectRow('backup_keep', 'Keep', 'Older backups are deleted.', [[7, 'Last 7 backups'], [14, 'Last 14 backups'], [30, 'Last 30 backups'], [90, 'Last 90 backups']])}
        </div>
        ${sub('Where')}
        <div class="card">
          <div class="setting"><div class="s-text"><div class="s-title">Destination</div><div class="s-desc">Remote copies are uploaded with rclone; a local copy is always kept for offline restores and retries.</div></div>
            <div class="s-control">${saved}<select class="select" data-backup-destination aria-label="Backup destination"><option value="local" ${s.backup_destination === 'local' ? 'selected' : ''}>This computer</option><option value="remote" ${s.backup_destination === 'remote' ? 'selected' : ''}>This computer + remote (rclone)</option></select></div></div>
          <div class="setting stack"><div class="s-text"><div class="s-title">Backup folder ${saved}</div>
            <div class="s-desc">Empty: the backups folder in your data folder. A folder synced by iCloud Drive or Dropbox keeps a copy off this computer.</div></div>
            <input class="input mono" type="text" data-backup-dir aria-label="Backup folder" spellcheck="false" autocomplete="off" placeholder="Default: data/backups" value="${escapeHtml(s.backup_dir || '')}">
            <div class="form-error" data-backup-dir-error hidden></div></div>
        </div>
        <div data-remote-backup hidden>${sub('Remote storage')}<div data-remote-backup-form><div class="card"><div class="setting"><div class="s-desc">Loading remote storage settings…</div></div></div></div></div>
        <h3 class="sec-h"><span>Recent backups</span><button class="link-btn" data-backup-more hidden></button></h3>
        <div class="card"><div class="setting stack backups-recent"><div class="backup-list collapsed" data-backup-list></div></div></div>
        <div data-remote-backup-archives hidden>
          <h3 class="sec-h"><span>On the remote</span><button class="link-btn" data-remote-backup-refresh>Refresh</button></h3>
          <div class="card"><div class="setting stack backups-recent"><div class="s-desc" data-remote-backup-status></div>
            <div class="backup-list" data-remote-backup-list></div></div></div></div>
        ${sub('Notes history')}
        <div class="card"><div class="setting"><div class="s-text"><div class="s-title">Every version of your notes</div>
            <div class="s-desc" data-history-desc>Loading…</div></div>
            <div class="s-control"><button class="btn" data-history-open>${icon('restore')} Notes History…</button></div></div></div>
        ${resetFoot}</section>

      <section class="settings-section" id="set-devices">${head('devices', 'Devices', 'Use the library from an iPad or a phone. Each device is paired once and stays paired until you remove it; this computer never needs pairing.')}
        <div class="card">
          <div class="setting stack" data-device-add><div class="s-desc">Loading…</div></div>
          <div class="setting stack"><div class="s-text"><div class="s-title">Paired devices</div>
            <div class="s-desc" data-devices-desc></div></div>
            <div class="backup-list" data-device-list></div></div>
          <div class="setting stack"><div class="setting-line"><div class="s-text"><div class="s-title">Passkeys</div>
            <div class="s-desc" data-passkeys-desc>Loading…</div></div>
            <div class="s-control" data-passkey-control></div></div>
            <div class="backup-list" data-passkey-list></div></div>
        </div></section>

      <section class="settings-section" id="set-ai">${head('ai', 'AI Access', `Let AI apps on another computer (Claude Code, Codex, any MCP app) use the library over the network.
          On this computer an AI app can start Synapse’s MCP server itself: <span class="mono">python mcp_server.py --config</span> sets up Claude and Codex.`)}
        <div class="card">
          <div class="setting stack"><div class="setting-line"><div class="s-text"><div class="s-title">Access tokens</div>
            <div class="s-desc" data-ai-desc>Loading…</div></div>
            <div class="s-control" data-ai-control></div></div>
            <div data-ai-fresh></div>
            <div class="backup-list" data-ai-list></div></div>
        </div>
        <p class="sec-lead">What an AI app changes in notes and tasks is shown with its name.</p></section>

      <section class="settings-section" id="set-plugins">${head('plugins', 'Plugins', 'Optional features. Some are bundled with Synapse, others installed by you; like any program they run with full access to your library and this computer, so turn on only ones you trust.')}
        <div class="plugin-filters" role="group" aria-label="Show plugins">
          <button class="chip active" data-pfilter="all">All</button><button class="chip" data-pfilter="on">On</button><button class="chip" data-pfilter="off">Off</button><button class="chip" data-pfilter="mine">Installed by you</button>
        </div>
        <div class="card plugin-card">
          <div class="plugin-list" data-plugin-list></div>
          <div class="plugin-foot"><div class="s-desc" data-plugins-desc>Loading…</div>
            <button class="btn btn-sm" data-plugins-rescan>${icon('refresh')} Look Again</button></div>
        </div></section>

      <section class="settings-section" id="set-library">${head('library', 'Library', '', ' data-data-dir')}
        <div class="card"><div class="stat-grid" data-stats></div></div>
        ${sub('Start over')}
        <div class="card"><div class="setting"><div class="s-text"><div class="s-title">Reset all settings</div>
          <div class="s-desc">Every setting on every page goes back to its default. Your library, notes and plugins are not touched.</div></div>
          <div class="s-control"><button class="btn btn-danger" data-reset>Reset All…</button></div></div></div>
      </section>

      <section class="settings-section" id="set-updates">${head('updates', 'Updates', 'Newer versions of Synapse.', ' data-update-lead')}
        <div class="card">
          <div class="setting stack"><div class="setting-line"><div class="s-text"><div class="s-title">Version</div>
            <div class="s-desc" data-update-desc>Loading…</div></div>
            <div class="s-control"><button class="btn" data-update-check>${icon('refresh')} Check for Updates</button></div></div>
            <div data-update-body></div></div>
        </div></section>

      <section class="settings-section" id="set-shortcuts">${head('shortcuts', 'Keyboard Shortcuts', `Open Settings with <kbd>${modLabel},</kbd>.`)}
        ${sub('Library')}
        ${shortcuts([
    [`<kbd>${modLabel}F</kbd>`, 'Search'],
    [`<kbd>${modLabel}N</kbd>`, 'New item'],
    [`<kbd>${modLabel}⇧N</kbd>`, 'Add item by identifier'],
    [`<kbd>${modLabel}I</kbd>`, 'Import…'],
    [`<kbd>${modLabel}E</kbd>`, 'Export selected items (or the library)…'],
    ['<kbd>↑</kbd> <kbd>↓</kbd>', 'Move selection (with <kbd>⇧</kbd> to extend)'],
    ['<kbd>←</kbd> <kbd>→</kbd>', 'Collapse / expand attachments'],
    ['<kbd>↵</kbd>', 'Open PDF'],
    ['<kbd>⌫</kbd>', `Move to Trash — inside a folder: remove from that folder (<kbd>${modLabel}⌫</kbd> moves to Trash)`],
    [`<kbd>${modLabel}C</kbd>`, 'Copy BibTeX of the selected items'],
    [`<kbd>${modLabel}⇧C</kbd>`, 'Copy citation keys'],
    [`<kbd>${modLabel}T</kbd>`, 'Edit tags of the selected items'],
    [`<kbd>${modLabel}A</kbd>`, 'Select all'],
    ['<kbd>Esc</kbd>', 'Clear search / close dialogs'],
    [`<kbd>${modLabel}⇧L</kbd>`, 'Switch between Library and Notes'],
    ['Back / Forward', `Return to the previous note or library view (mouse buttons, <kbd>${modLabel}[</kbd> / <kbd>Alt←</kbd>)`],
  ])}
        ${sub('Tabs')}
        ${shortcuts([
    ['<kbd>Ctrl Tab</kbd> <kbd>Ctrl ⇧Tab</kbd>', 'Next / previous tab (in the app window; a browser keeps these for its own tabs)'],
    [`<kbd>${tabMod}1</kbd> … <kbd>${tabMod}9</kbd>`, 'Go to a tab (1: Library or Tasks, 9: the last one)'],
    [`<kbd>${tabMod}W</kbd>, middle click`, 'Close the tab'],
    [`<kbd>${tabMod}⇧T</kbd>`, 'Reopen the last closed tab'],
    ['Double-click a tab', 'Keep a note’s preview tab (italic title): the next note opens in a tab of its own'],
    ['Drag a tab', 'Change the order; onto another Synapse window to move it there, outside every window for a new one; right-click for more'],
  ])}
        ${sub('Notes list')}
        ${shortcuts([
    [`${modLabel}-click, ⇧-click`, 'Select several notes and folders (or <kbd>⇧↑</kbd> <kbd>⇧↓</kbd>)'],
    ['<kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd>', 'Move; fold and unfold folders'],
    ['<kbd>↵</kbd>, click', 'Open the note (in the preview tab)'],
    ['Double-click', 'Open the note in a tab that stays'],
    ['<kbd>F2</kbd>', 'Rename the note or folder'],
    ['<kbd>⌫</kbd>', 'Move the selection to the notes trash (with Undo)'],
    [`<kbd>${modLabel}N</kbd>`, 'New note'],
    [`<kbd>${modLabel}E</kbd>`, 'Read / edit the open note'],
  ])}
      </section>
      </div></div>
    </div>`;
}
