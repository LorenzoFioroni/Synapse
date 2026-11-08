// Settings page (full-screen view). Changes save immediately. The page's markup is in
// settings/page.ts; Backups, Updates, Devices, Passkeys, AI Access and Plugins have modules of their own in settings/.
// One section is shown at a time (its navigation entry is active); Plugins shows its list beside the
// page of the plugin chosen in it. On phones Settings opens on the navigation as a list, and a
// section replaces it until Back.
import * as A from '../actions.js';
import { api } from '../api.js';
import { emit, on, state } from '../store.js';
import { desktopControl, toggleDesktop } from '../notifications/desktop.js';
import { confirmDialog } from '../ui/dialog.js';
import { toast, toastError } from '../ui/toast.js';
import type { SearchMode, Settings } from '../types.js';
import { debounce, escapeHtml, formatBytes, formatDate, plural, store } from '../util.js';
import { baseMacros } from './notes/macros.js';
import { bindBackups, refreshBackups } from './settings/backups.js';
import { bindUpdates, refreshUpdates } from './settings/updates.js';
import { bindAiAccess, forgetFreshToken, refreshAiAccess } from './settings/aiAccess.js';
import { bindDevices, cancelPairing, refreshDevices } from './settings/devices.js';
import { bindPasskeys, refreshPasskeys } from './settings/passkeys.js';
import { asAdmin } from './settings/confirm.js';
import { SECTIONS_EVENT, bindPlugins, refreshPlugins } from './settings/plugins.js';
import { refreshExportFormats } from './settings/exportFormats.js';
import { mountPluginSections } from './settings/pluginSections.js';
import { mountSettingsSearch, type SettingsSearch } from './settings/search.js';
import { TOKENS, flashSaved, settingsHtml } from './settings/page.js';
import { paintNavValues } from './settings/navValues.js';

let view: HTMLElement | null = null;
let settingsSearch: SettingsSearch | null = null;
on('export-formats', () => { if (view) refreshExportFormats(view); });
let unmountPluginSections: (() => void) | null = null;
let statsTimer: ReturnType<typeof setInterval> | undefined;
let unbindBackups: (() => void) | undefined;
/** The section shown, and the plugin last shown beside the Plugins list. */
let currentId = 'general';
let lastPlugin: string | null = null;

/** A phone-sized window: Settings is a list of sections, then one section at a time. */
const narrow = (): boolean => matchMedia('(max-width: 700px)').matches;

export function applyAppearance(s: Settings = state.settings): void {
  const root = document.documentElement;
  const dark = s.theme === 'dark' || (s.theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  root.dataset.theme = dark ? 'dark' : 'light';
  store('zc.themeHint', root.dataset.theme);
  root.dataset.density = s.density || 'comfortable';
  root.style.setProperty('--fs', `${s.font_size || 13}px`);
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyAppearance());

/** Put the controls of these settings back to the saved values (after a change was not saved). */
function showSaved(keys: string[]): void {
  if (!view) return;
  const saved = state.settings as unknown as Record<string, unknown>;
  for (const k of keys) {
    const v = saved[k];
    view.querySelectorAll<HTMLInputElement | HTMLSelectElement>(`[data-key="${CSS.escape(k)}"]`).forEach((el) => {
      if (el instanceof HTMLInputElement && el.type === 'checkbox') el.checked = !!v;
      else el.value = String(v ?? '');
    });
    view.querySelectorAll<HTMLElement>(`[data-seg="${CSS.escape(k)}"] button`).forEach((b) => b.classList.toggle('active', b.dataset.v === String(v)));
  }
}

async function save(changes: Partial<Settings>, el?: Element): Promise<boolean> {
  try {
    // Backup settings need a passkey confirmation when this is not the computer Synapse runs on.
    const res = await asAdmin(() => api.saveSettings(changes));
    if (!res) {
      showSaved(Object.keys(changes));
      toast('Not saved: changing this from another device needs your passkey.', { type: 'error' });
      return false;
    }
    state.settings = res.settings;
    applyAppearance();
    emit('settings');
    if (view) paintNavValues(view);
    flashSaved(el?.closest('.setting') || view);
    return true;
  } catch (e) { toastError(e); return false; }
}

export function openSettings(sectionId: string | null = null): void {
  closeSettings();
  const s = state.settings;
  const meta = state.settingsMeta;
  const presetMatch = (meta.citekeyPresets || []).find((p) => p.pattern === s.citekey_pattern);
  const page = document.createElement('div');
  view = page;
  page.id = 'settings-view';
  page.innerHTML = settingsHtml(s, meta);
  page.classList.toggle('at-root', narrow() && !sectionId);
  document.body.appendChild(page);
  paintNavValues(page);
  settingsSearch = mountSettingsSearch(page, (id) => show(id), () => currentId);
  bind(page);
  paintDesktopNotifications(page);
  refreshPreview(s.citekey_pattern);
  refreshStats();
  refreshBackups(page);
  refreshUpdates(page);
  refreshDevices(page);
  refreshPasskeys(page);
  refreshAiAccess(page);
  refreshPlugins(page);
  refreshExportFormats(page);
  pendingNav = null;
  page.addEventListener(SECTIONS_EVENT, () => {
    if (pendingNav && page.querySelector(`#set-${CSS.escape(pendingNav)}`)) setNav(pendingNav, false);
    // the plugins' pages came (or one went): show the chosen one beside the list again
    else if (pluginMode(currentId) || !page.querySelector(`#set-${CSS.escape(currentId)}`)) show(currentId);
  });
  unmountPluginSections = mountPluginSections(page, () => show('general'));
  statsTimer = setInterval(refreshStats, 2000);
  const sel = page.querySelector<HTMLSelectElement>('[data-key="_preset"]')!;
  sel.value = presetMatch ? presetMatch.pattern : 'custom';
  lastPlugin = null;
  setNav(sectionId || 'general', false);
}

export function closeSettings(): void {
  if (!view) return;
  settingsSearch?.dispose();
  settingsSearch = null;
  unmountPluginSections?.();
  unmountPluginSections = null;
  clearInterval(statsTimer);
  unbindBackups?.();
  unbindBackups = undefined;
  cancelPairing();
  forgetFreshToken();
  view.remove();
  view = null;
  emit('focus-list');
}
export const settingsOpen = (): boolean => !!view;

/** A section asked for before it was there: a plugin's, which comes once the plugins are listed. */
let pendingNav: string | null = null;

const pluginMode = (id: string): boolean => id === 'plugins' || id.startsWith('plugin-');

/** Go to section ``id`` (a navigation id: ``general``, ``plugins``, ``plugin-<id>``…), leaving a
 *  search. A plugin's page that is not there yet is shown once the plugins are listed. */
function setNav(id: string, fromNav = true): void {
  if (!view) return;
  if (settingsSearch?.filtering) settingsSearch.clear();
  if (!view.querySelector(`#set-${CSS.escape(id)}`)) {
    pendingNav = id;
    if (!pluginMode(id)) return;
  } else pendingNav = null;
  show(id);
  if (!fromNav) return;
  view.classList.remove('at-root');
  view.querySelector('.settings-content')!.scrollTop = 0;
}

/** Show section ``id`` (or, for Plugins, the list beside the chosen plugin's page). */
function show(id: string): void {
  if (!view) return;
  const inner = view.querySelector<HTMLElement>('.settings-inner')!;
  if (!pluginMode(id) && !view.querySelector(`#set-${CSS.escape(id)}`)) id = 'general';
  currentId = id;
  let pluginPage: string | null = null;
  if (pluginMode(id)) {
    const pages = [...view.querySelectorAll<HTMLElement>('.plugin-settings')].map((x) => x.id.slice(4));
    if (id !== 'plugins' && pages.includes(id)) lastPlugin = id;
    const firstOn = view.querySelector<HTMLElement>('.plugin-row[data-state="on"]')?.dataset.plugin;
    pluginPage = lastPlugin && pages.includes(lastPlugin) ? lastPlugin
      : firstOn && pages.includes(`plugin-${firstOn}`) ? `plugin-${firstOn}` : (pages[0] ?? null);
  }
  const shown = new Set(pluginMode(id) ? ['plugins', pluginPage] : [id]);
  view.querySelectorAll<HTMLElement>('.settings-section').forEach((sec) => sec.classList.toggle('current', shown.has(sec.id.slice(4))));
  inner.classList.toggle('plugins-mode', pluginMode(id));
  // phones show the list or the plugin's page, not both
  inner.dataset.pluginView = id === 'plugins' ? 'list' : pluginMode(id) ? 'page' : '';
  view.querySelectorAll<HTMLElement>('.plugin-row').forEach((row) => row.classList.toggle('selected', !!pluginPage && pluginPage === `plugin-${row.dataset.plugin}`));
  const back = view.querySelector<HTMLElement>('[data-settings-back]')!;
  back.lastChild!.textContent = id.startsWith('plugin-') ? ' Plugins' : ' Settings';
  const navId = pluginMode(id) ? 'plugins' : id;
  view.querySelectorAll<HTMLElement>('.settings-nav [data-nav]').forEach((b) => {
    b.classList.toggle('active', b.dataset.nav === navId);
    if (b.dataset.nav === navId) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
}

const refreshPreview = debounce(async (pattern: string) => {
  if (!view) return;
  const box = view.querySelector<HTMLElement>('[data-preview]')!;
  try {
    const res = await api.citekeyPreview(pattern);
    const names = ['Example (Cui, Cirac & Bañuls 2015)', ...state.items.slice(0, 3).map((i) => i.title)];
    box.innerHTML = res.examples.map((k, i) => `<div class="ex"><code>${escapeHtml(k)}</code><span>${escapeHtml(names[i] || '')}</span></div>`).join('');
  } catch (e) { box.textContent = (e as Error).message; }
}, 200);

async function refreshStats(): Promise<void> {
  if (!view) return;
  const page = view;
  try {
    const st = await api.stats();
    page.querySelector('[data-data-dir]')!.innerHTML = `Your library lives in <span class="mono">${escapeHtml(st.data_dir)}</span> — back up this folder to keep everything (database, PDFs, settings).`;
    page.querySelector('[data-stats]')!.innerHTML = [
      [st.counts.library, 'items'], [st.attachments, 'attachments'], [st.counts.trash, 'in Trash'],
      [st.fulltext.documents, 'PDFs indexed'], [formatBytes(st.fulltext.bytes), 'index size'],
      [st.fulltext.pages.toLocaleString(), 'pages indexed'],
    ].map(([v, l]) => `<div class="stat"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');
    const ix = st.indexer;
    const desc = page.querySelector<HTMLElement>('[data-index-desc]')!;
    const prog = page.querySelector<HTMLElement>('[data-index-progress]')!;
    if (ix.running) {
      desc.textContent = `Indexing ${ix.done + 1} of ${ix.total}: ${ix.current}`;
      prog.hidden = false; (prog.firstElementChild as HTMLElement).style.width = `${(ix.done / Math.max(1, ix.total)) * 100}%`;
    } else {
      prog.hidden = true;
      desc.textContent = `${plural(st.fulltext.documents, 'PDF')} of ${plural(st.attachments, 'attachment')} indexed · ${formatBytes(st.fulltext.bytes)}`
        + `${st.fulltext.fts5 ? '' : ' · FTS5 unavailable (slower fallback)'}${ix.last_run ? ` · last run ${formatDate(ix.last_run, true)}` : ''}${ix.errors ? ` · ${ix.errors} failed` : ''}`;
    }
  } catch { /* server gone; ignore */ }
}

function bind(view: HTMLElement): void {
  view.querySelector<HTMLElement>('[data-close]')!.onclick = closeSettings;
  view.querySelector<HTMLButtonElement>('[data-desktop-notifications]')!.onclick = async () => {
    await toggleDesktop();
    paintDesktopNotifications(view);
  };
  view.querySelector('.settings-nav')!.addEventListener('click', (e) => {
    const b = (e.target as Element).closest<HTMLElement>('[data-nav]');
    if (b) setNav(b.dataset.nav!);
  });
  view.querySelector<HTMLElement>('[data-settings-back]')!.onclick = () => {
    if (settingsSearch?.filtering) { settingsSearch.clear(); view.classList.add('at-root'); return; }
    if (currentId.startsWith('plugin-')) { setNav('plugins'); return; }
    view.classList.add('at-root');
  };
  view.addEventListener('click', (e) => {
    const b = (e.target as Element).closest<HTMLButtonElement>('[data-reset-page]');
    if (b) void resetPage(b.closest<HTMLElement>('.settings-section')!);
    const open = (e.target as Element).closest<HTMLElement>('[data-open-section]');
    if (open) setNav(open.dataset.openSection!);
  });

  view.addEventListener('change', async (e) => {
    const el = e.target as HTMLInputElement | HTMLSelectElement;
    const key = el.dataset.key;
    if (!key) return;
    const pattern = view.querySelector<HTMLInputElement>('[data-pattern]')!;
    if (key === '_preset') {
      if (el.value === 'custom') { pattern.focus(); return; }
      pattern.value = el.value;
      refreshPreview(el.value);
      await save({ citekey_pattern: el.value }, el);
      return;
    }
    const value = el.type === 'checkbox' ? (el as HTMLInputElement).checked : (el.type === 'range' ? Number(el.value) : el.value);
    if (!(await save({ [key]: value }, el))) return;
    if (key === 'default_search_mode') emit('set-search-mode', value as SearchMode);
    if (key === 'fulltext_auto_index' && value) {
      // index the PDFs that arrived while it was off, now rather than at the next start
      try { await api.syncFulltext(); setTimeout(refreshStats, 300); } catch (err) { toastError(err); }
    }
  });
  // Math macros: say what the definitions define, and which lines are not definitions
  const mt = view.querySelector<HTMLTextAreaElement>('[data-key="notes_math_macros"]')!;
  const mcheck = () => {
    const r = baseMacros({ physics: false, text: mt.value });
    const out = view.querySelector('[data-macro-check]')!;
    out.innerHTML = (r.names.length ? `Defines ${r.names.map((n) => `<span class="mono">${escapeHtml(n)}</span>`).join(', ')}.` : '')
      + (r.problems.length ? ` <span class="warn">Not a definition: <span class="mono">${escapeHtml(r.problems[0].slice(0, 60))}</span>${r.problems.length > 1 ? ` (and ${r.problems.length - 1} more)` : ''}</span>` : '');
  };
  mt.addEventListener('input', mcheck);
  mcheck();
  const fs = view.querySelector<HTMLInputElement>('[data-key="font_size"]')!;
  fs.addEventListener('input', () => {
    view.querySelector('[data-fs-label]')!.textContent = `${fs.value}px`;
    document.documentElement.style.setProperty('--fs', `${fs.value}px`);
  });
  view.querySelectorAll<HTMLElement>('[data-seg]').forEach((seg) => seg.addEventListener('click', async (e) => {
    const b = (e.target as Element).closest('button');
    if (!b) return;
    seg.querySelectorAll('button').forEach((x) => x.classList.toggle('active', x === b));
    await save({ [seg.dataset.seg!]: b.dataset.v }, seg);
  }));

  // citation key pattern
  const pat = view.querySelector<HTMLInputElement>('[data-pattern]')!;
  const err = view.querySelector<HTMLElement>('[data-pattern-error]')!;
  const savePattern = debounce(async () => {
    const v = pat.value.trim();
    err.hidden = true;
    if (!v) { err.textContent = 'The pattern cannot be empty.'; err.hidden = false; return; }
    const unknown = (v.match(/\{[^}]*\}/g) || []).filter((t) => !TOKENS.map((x) => x.toLowerCase()).includes(t.toLowerCase()) && !/^\{(authors2|authetal)\}$/i.test(t));
    if (unknown.length) { err.textContent = `Unknown token${unknown.length > 1 ? 's' : ''}: ${unknown.join(', ')}`; err.hidden = false; }
    const preset = (state.settingsMeta.citekeyPresets || []).find((p) => p.pattern === v);
    view.querySelector<HTMLSelectElement>('[data-key="_preset"]')!.value = preset ? preset.pattern : 'custom';
    await save({ citekey_pattern: v }, pat);
  }, 500);
  pat.addEventListener('input', () => { refreshPreview(pat.value); savePattern(); });
  view.querySelector('.token-list')!.addEventListener('click', (e) => {
    const t = (e.target as Element).closest<HTMLElement>('[data-token]');
    if (!t) return;
    const token = t.dataset.token!;
    const s = pat.selectionStart ?? pat.value.length, en = pat.selectionEnd ?? s;
    pat.value = pat.value.slice(0, s) + token + pat.value.slice(en);
    pat.focus(); pat.selectionStart = pat.selectionEnd = s + token.length;
    pat.dispatchEvent(new Event('input'));
  });
  view.querySelector<HTMLElement>('[data-regen-all]')!.onclick = async () => {
    const n = state.counts.library;
    if (!(await confirmDialog(`Regenerate citation keys for all ${plural(n, 'item')}?`,
      { title: 'Regenerate all keys', confirm: 'Regenerate', detail: `Existing keys are replaced using the pattern ${state.settings.citekey_pattern}. Your notes are updated to the new keys automatically (after a backup); LaTeX documents that cite the old keys will need updating.` }))) return;
    const all = await api.listItems({ view: 'library' });
    await A.regenerateCitekeys(all.items.map((i) => i.id));
    refreshPreview(pat.value);
  };

  const email = view.querySelector<HTMLInputElement>('[data-email]')!;
  email.addEventListener('change', () => save({ contact_email: email.value.trim() }, email));
  view.querySelectorAll<HTMLInputElement>('[data-apikey]').forEach((inp) => inp.addEventListener('change', () => save({ [inp.dataset.apikey!]: inp.value.trim() }, inp)));

  bindDevices(view);
  bindPasskeys(view);
  bindAiAccess(view);
  unbindBackups = bindBackups(view);
  bindUpdates(view);
  bindPlugins(view, (id) => setNav(id));

  view.querySelector<HTMLElement>('[data-rebuild]')!.onclick = async () => {
    await api.rebuildFulltext();
    toast('Rebuilding the full-text index…');
    setTimeout(refreshStats, 300);
  };
  view.querySelector<HTMLElement>('[data-import]')!.onclick = () => emit('open-import');
  view.querySelector<HTMLElement>('[data-export]')!.onclick = async () => (await import('./exportDialog.js')).openExport(null);
  view.querySelector<HTMLElement>('[data-reset]')!.onclick = async () => {
    if (!(await confirmDialog('Reset all settings to their defaults?', { confirm: 'Reset All', danger: true, detail: 'Every page of Settings goes back to its defaults. Your library, notes and plugins are not changed.' }))) return;
    const res = await asAdmin(() => api.resetSettings());
    if (!res) return;
    state.settings = res.settings;
    applyAppearance();
    emit('settings');
    openSettings('library');
    toast('Settings reset', { type: 'success' });
  };
}

/** Put the settings that have a control in ``section`` back to their defaults. */
async function resetPage(section: HTMLElement): Promise<void> {
  const defaults = state.settingsMeta.defaults as unknown as Record<string, unknown> | undefined;
  if (!defaults) return;
  const keys = new Set<string>();
  section.querySelectorAll<HTMLElement>('[data-key], [data-seg]').forEach((el) => keys.add(el.dataset.key || el.dataset.seg!));
  if (section.querySelector('[data-pattern]')) keys.add('citekey_pattern');
  if (section.querySelector('[data-backup-destination]')) keys.add('backup_destination');
  if (section.querySelector('[data-backup-dir]')) keys.add('backup_dir');
  const current = state.settings as unknown as Record<string, unknown>;
  const changes = Object.fromEntries([...keys].filter((k) => k in defaults && JSON.stringify(defaults[k]) !== JSON.stringify(current[k])).map((k) => [k, defaults[k]]));
  const title = section.querySelector('h2')?.textContent?.trim() || 'this page';
  if (!Object.keys(changes).length) { toast(`${title} already uses the defaults`); return; }
  if (!(await confirmDialog(`Reset ${title} to its defaults?`, { confirm: 'Reset', detail: `${plural(Object.keys(changes).length, 'setting')} on this page change back. Settings on other pages stay as they are.` }))) return;
  if (!(await save(changes as Partial<Settings>))) return;
  openSettings(section.id.slice(4));
  toast(`${title} reset`, { type: 'success' });
}

function paintDesktopNotifications(page: HTMLElement): void {
  const status = desktopControl();
  const button = page.querySelector<HTMLButtonElement>('[data-desktop-notifications]')!;
  button.textContent = status.label;
  button.disabled = status.disabled;
  button.classList.toggle('btn-primary', status.enabled);
  page.querySelector<HTMLElement>('[data-desktop-notifications-desc]')!.textContent = status.description;
}
