// Settings page (full-screen view). Changes save immediately. The page's markup is in
// settings/page.ts; Backups and Devices have modules of their own in settings/.
import * as A from '../actions.js';
import { api } from '../api.js';
import { emit, state } from '../store.js';
import { confirmDialog } from '../ui/dialog.js';
import { toast, toastError } from '../ui/toast.js';
import type { SearchMode, Settings } from '../types.js';
import { debounce, escapeHtml, formatBytes, formatDate, plural, store } from '../util.js';
import { baseMacros } from './notes/macros.js';
import { bindBackups, refreshBackups } from './settings/backups.js';
import { bindDevices, refreshDevices, stopPairing } from './settings/devices.js';
import { TOKENS, flashSaved, settingsHtml } from './settings/page.js';

let view: HTMLElement | null = null;
let statsTimer: ReturnType<typeof setInterval> | undefined;

export function applyAppearance(s: Settings = state.settings): void {
  const root = document.documentElement;
  const dark = s.theme === 'dark' || (s.theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  root.dataset.theme = dark ? 'dark' : 'light';
  store('zc.themeHint', root.dataset.theme);
  root.dataset.density = s.density || 'comfortable';
  root.style.setProperty('--fs', `${s.font_size || 13}px`);
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyAppearance());

async function save(changes: Partial<Settings>, el?: Element): Promise<boolean> {
  try {
    const res = await api.saveSettings(changes);
    state.settings = res.settings;
    applyAppearance();
    emit('settings');
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
  document.body.appendChild(page);
  bind(page);
  refreshPreview(s.citekey_pattern);
  refreshStats();
  refreshBackups(page);
  refreshDevices(page);
  statsTimer = setInterval(refreshStats, 2000);
  const sel = page.querySelector<HTMLSelectElement>('[data-key="_preset"]')!;
  sel.value = presetMatch ? presetMatch.pattern : 'custom';
  setNav(sectionId || 'general', !!sectionId);
}

export function closeSettings(): void {
  if (!view) return;
  clearInterval(statsTimer);
  stopPairing();
  view.remove();
  view = null;
  emit('focus-list');
}
export const settingsOpen = (): boolean => !!view;

function setNav(id: string, scroll = true): void {
  if (!view) return;
  view.querySelectorAll<HTMLElement>('[data-nav]').forEach((b) => b.classList.toggle('active', b.dataset.nav === id));
  if (scroll) view.querySelector(`#set-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
  view.querySelector('.settings-nav')!.addEventListener('click', (e) => {
    const b = (e.target as Element).closest<HTMLElement>('[data-nav]');
    if (b) setNav(b.dataset.nav!);
  });
  const content = view.querySelector('.settings-content')!;
  content.addEventListener('scroll', debounce(() => {
    const secs = [...view.querySelectorAll('.settings-section')];
    const cur = secs.find((s) => s.getBoundingClientRect().bottom > 140) || secs[0];
    setNav(cur.id.replace('set-', ''), false);
  }, 60));

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
  bindBackups(view);

  view.querySelector<HTMLElement>('[data-rebuild]')!.onclick = async () => {
    await api.rebuildFulltext();
    toast('Rebuilding the full-text index…');
    setTimeout(refreshStats, 300);
  };
  view.querySelector<HTMLElement>('[data-import]')!.onclick = () => emit('open-import');
  view.querySelector<HTMLElement>('[data-export]')!.onclick = async () => (await import('./exportDialog.js')).openExport(null);
  view.querySelector<HTMLElement>('[data-reset]')!.onclick = async () => {
    if (!(await confirmDialog('Reset all settings to their defaults?', { confirm: 'Reset' }))) return;
    const res = await api.resetSettings();
    state.settings = res.settings;
    applyAppearance();
    emit('settings');
    openSettings();
    toast('Settings reset', { type: 'success' });
  };
}
