// Settings → Plugins: the plugins found in the plugins folder and turning them on and off. Each
// plugin that is on gets a section of its own further down the page (and an entry under Plugins in
// the navigation): its settings, drawn from what its manifest declares, the sections its frontend
// adds (pluginSections.ts mounts those inside), what it offers, Restart and its log. A row's
// Settings button goes to that section. See docs/plugins.md.
import { api } from '../../api.js';
import { settingsSections } from '../../extend.js';
import { syncPlugins } from '../../plugins/host.js';
import { icon } from '../../icons.js';
import { confirmDialog, openDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import type { PluginInfo, PluginSetting, PluginsOverview } from '../../types.js';
import { escapeHtml, formatDate } from '../../util.js';
import { flashSaved } from './page.js';

let current: HTMLElement | null = null;
let last: PluginsOverview | null = null;

const STATUS: Record<PluginInfo['status'], string> = {
  on: 'On', off: 'Off', failed: 'Turned off after a failure', incompatible: 'Needs another version of Synapse',
  invalid: 'Cannot be read', 'safe-mode': 'Not started (safe mode)',
};

/** The id of a plugin's section in Settings (its navigation entry is ``plugin-<id>``). */
export const pluginNavId = (id: string): string => `plugin-${id}`;

/** Does the plugin have a section of its own? Those that are on (or were, until they failed). */
export const hasSection = (p: PluginInfo): boolean => p.enabled && p.status !== 'invalid';

/** The event the Settings page hears when the plugins' sections were added, removed or redrawn. */
export const SECTIONS_EVENT = 'plugin-sections';

/** What the plugin may do, in words. */
export function permissionWords(p: PluginInfo): string[] {
  const out: string[] = [];
  const perms = p.permissions;
  if (!perms) return out;
  const lib = perms.library;
  if (lib.length) {
    const verbs = [lib.includes('read') && 'reads', lib.includes('add') && 'adds items to', lib.includes('edit') && 'changes items in'].filter(Boolean);
    out.push(`${verbs.join(', ').replace(/^./, (c) => c.toUpperCase())} your library`);
  }
  if (perms.network.length) out.push(`Connects to ${perms.network.join(', ')}`);
  if (perms.mcp) out.push('Offers tools to AI apps');
  if (perms.python?.length) out.push(`Installs Python packages: ${perms.python.join(', ')}`);
  return out;
}

function settingControl(p: PluginInfo, s: PluginSetting): string {
  const v = p.values?.[s.key];
  const attrs = `data-pset="${escapeHtml(s.key)}" data-ptype="${s.type}"`;
  switch (s.type) {
    case 'bool':
      return `<label class="switch"><input type="checkbox" ${attrs} ${v ? 'checked' : ''}><span></span></label>`;
    case 'int': case 'float':
      return `<input class="input p-num" type="number" ${attrs} value="${escapeHtml(String(v ?? ''))}" step="${s.type === 'int' ? 1 : 'any'}"${s.min != null ? ` min="${s.min}"` : ''}${s.max != null ? ` max="${s.max}"` : ''}>`;
    case 'choice':
      return `<select class="select" ${attrs}>${(s.options || []).map(([o, label]) => `<option value="${escapeHtml(o)}" ${o === v ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('')}</select>`;
    case 'multi':
      if (s.options?.length) {
        const chosen = new Set(Array.isArray(v) ? v.map(String) : []);
        return `<div class="p-multi" ${attrs}>${s.options.map(([o, label]) => `<label class="check"><input type="checkbox" value="${escapeHtml(o)}" ${chosen.has(o) ? 'checked' : ''}> ${escapeHtml(label)}</label>`).join('')}</div>`;
      }
      return `<input class="input" type="text" ${attrs} value="${escapeHtml(Array.isArray(v) ? v.join(', ') : '')}" placeholder="Separate with commas" spellcheck="false">`;
    default:
      return `<input class="input" type="text" ${attrs} value="${escapeHtml(String(v ?? ''))}" spellcheck="false">`;
  }
}

const savedPill = `<span class="saved-pill">${icon('check', 'icon-sm')} Saved</span>`;

/** A plugin's section, except what its frontend draws: settings, remote tools, what it offers. */
function sectionBodyHtml(p: PluginInfo, safeMode: boolean, drawnElsewhere: Set<string>): string {
  const settings = (p.settings || []).filter((s) => !drawnElsewhere.has(s.key)).map((s) => `<div class="setting p-setting" data-pkey="${escapeHtml(s.key)}">
      <div class="s-text"><div class="s-title">${escapeHtml(s.label)}</div>${s.description ? `<div class="s-desc">${escapeHtml(s.description)}</div>` : ''}</div>
      <div class="s-control p-control">${savedPill}${settingControl(p, s)}</div></div>`).join('');
  const remote = p.permissions?.mcp ? `<div class="setting p-setting">
      <div class="s-text"><div class="s-title">Tools for AI apps on other computers</div>
        <div class="s-desc">Also offer this plugin's tools to AI apps using the library over the network (Settings → AI Access).</div></div>
      <div class="s-control p-control"><label class="switch"><input type="checkbox" data-premote ${p.remote_mcp ? 'checked' : ''}><span></span></label></div></div>` : '';
  const offers = [
    p.reference_sources?.length ? `Reference sources: ${p.reference_sources.map((s) => escapeHtml(s.title)).join(', ')}` : '',
    p.export_formats?.length ? `Export formats: ${p.export_formats.map((f) => escapeHtml(f.title)).join(', ')}` : '',
    p.tools?.length ? `Tools for AI apps: ${p.tools.map((t) => `<span class="mono">${escapeHtml(t)}</span>`).join(', ')}` : '',
    p.routes?.length ? `Serves ${p.routes.map((r) => `<span class="mono">/api/plugins/${escapeHtml(p.id)}${escapeHtml(r.rule)}</span>`).join(', ')}` : '',
    p.events?.length ? `Listens to ${p.events.map((e) => `<span class="mono">${escapeHtml(e)}</span>`).join(', ')}` : '',
    p.schedules?.length ? `Runs ${p.schedules.map((x) => `<span class="mono">${escapeHtml(x.name)}</span> (${escapeHtml(x.rule)})`).join(', ')}` : '',
    p.jobs ? `${p.jobs} job${p.jobs === 1 ? '' : 's'} waiting` : '',
  ].filter(Boolean);
  const problem = p.problem ? `<div class="form-error">${escapeHtml(STATUS[p.status])}: ${escapeHtml(p.problem)}${p.failed_at ? ` (${escapeHtml(formatDate(p.failed_at, true))})` : ''}</div>` : '';
  return `${problem}
    ${settings || remote ? `<div class="card">${settings}${remote}</div>` : ''}
    <div class="card p-about">
      ${offers.length ? `<div class="p-offers">${offers.map((o) => `<div>${o}</div>`).join('')}</div>` : ''}
      <div class="p-foot"><span class="mono p-folder" title="${escapeHtml(p.folder)}">${escapeHtml(p.folder)}</span>
        ${p.enabled && (!safeMode || p.core) ? `<button class="btn btn-sm" data-prestart>${icon('refresh')} Restart</button>` : ''}
        <button class="btn btn-sm" data-plog>${icon('text')} Log</button></div>
    </div>`;
}

function rowHtml(p: PluginInfo, safeMode: boolean): string {
  const usable = p.status !== 'invalid' && p.status !== 'incompatible' && (!safeMode || p.core);
  const perms = permissionWords(p);
  const meta = [p.author && `by ${escapeHtml(p.author)}`, p.version && `version ${escapeHtml(p.version)}`,
    p.core ? 'core plugin: part of Synapse' : p.source === 'bundled' ? 'comes with Synapse' : p.source === 'dev' ? 'being written (restarts when its files change)' : p.replaces_bundled ? 'replaces the one that comes with Synapse' : ''].filter(Boolean).join(' · ');
  const problem = p.problem ? `<div class="form-error">${escapeHtml(STATUS[p.status])}: ${escapeHtml(p.problem)}${p.failed_at ? ` (${escapeHtml(formatDate(p.failed_at, true))})` : ''}</div>` : '';
  const button = hasSection(p)
    ? `<button class="btn btn-ghost btn-sm" data-popen title="This plugin's settings, what it offers and its log">${icon('gear', 'icon-sm')} Settings</button>`
    : p.problem ? `<button class="btn btn-ghost btn-sm" data-plog>${icon('text', 'icon-sm')} Log</button>` : '';
  return `<div class="plugin-row" data-plugin="${escapeHtml(p.id)}">
    <div class="p-head">
      <span class="p-icon">${icon('plugin')}</span>
      <div class="p-main">
        <div class="p-name">${escapeHtml(p.name)}${p.running ? '' : p.enabled && safeMode && !p.core ? ' <span class="badge warn">Safe mode</span>' : ''}</div>
        ${p.description ? `<div class="p-desc">${escapeHtml(p.description)}</div>` : ''}
        ${meta ? `<div class="p-meta">${meta}</div>` : ''}
        ${perms.length ? `<div class="p-perms">${perms.map((w) => `<span>${escapeHtml(w)}</span>`).join('')}</div>` : ''}
        ${problem}
      </div>
      <div class="p-side">
        ${button}
        <label class="switch" title="${p.enabled ? 'Turn off' : 'Turn on'}"><input type="checkbox" data-ptoggle ${p.enabled && p.status !== 'failed' ? 'checked' : ''} ${usable ? '' : 'disabled'}><span></span></label>
      </div>
    </div>
  </div>`;
}

/** The manifest settings a plugin's frontend draws in its own part of the section (``shows``). */
function drawnByFrontend(pluginId: string): Set<string> {
  return new Set(settingsSections.all().filter((s) => s.owner === pluginId).flatMap((s) => s.shows || []));
}

function render(view: HTMLElement, data: PluginsOverview, force = false): void {
  const desc = view.querySelector<HTMLElement>('[data-plugins-desc]')!;
  const list = view.querySelector<HTMLElement>('[data-plugin-list]')!;
  desc.innerHTML = (data.safe_mode ? `<div class="form-error">Synapse was started in safe mode (<span class="mono">--safe-mode</span>): no plugin runs until it is started normally.</div>` : '')
    + `Put a plugin's folder in <span class="mono">${escapeHtml(data.dir)}</span>, then press Look Again. Plugin API ${escapeHtml(data.api)}.`;
  const group = (title: string, plugins: PluginInfo[], empty: string) => `<section class="plugin-group">
    <h3>${title}</h3><div class="plugin-group-list">${plugins.length
      ? plugins.map((p) => rowHtml(p, data.safe_mode)).join('')
      : `<div class="p-empty">${empty}</div>`}</div></section>`;
  list.innerHTML = group('Core plugins', data.plugins.filter((p) => p.core), 'No core plugins found.')
    + group('Contributed plugins', data.plugins.filter((p) => !p.core), 'No contributed plugins installed.');
  renderSections(view, data, force);
}

/** Add, redraw and remove the plugins' own sections. A section keeps the part its frontend drew
 *  (``.plugin-custom``) and is not redrawn while one of its fields is being typed in. */
function renderSections(view: HTMLElement, data: PluginsOverview, force = false): void {
  const anchor = view.querySelector<HTMLElement>('#set-plugins')!;
  const navAnchor = view.querySelector<HTMLElement>('.settings-nav [data-nav="plugins"]')!;
  const sections = data.plugins.filter(hasSection);
  const wanted = [...sections.filter((p) => p.core), ...sections.filter((p) => !p.core)];
  const keep = new Set(wanted.map((p) => pluginNavId(p.id)));
  let activeRemoved = false;
  view.querySelectorAll<HTMLElement>('.plugin-settings').forEach((s) => { if (!keep.has(s.dataset.section!)) s.remove(); });
  view.querySelectorAll<HTMLElement>('.settings-nav [data-plugin-nav]').forEach((b) => {
    if (keep.has(b.dataset.nav!)) return;
    activeRemoved ||= b.classList.contains('active');
    b.remove();
  });
  let after: Element = anchor, navAfter: Element = navAnchor;
  for (const p of wanted) {
    const id = pluginNavId(p.id);
    let section = view.querySelector<HTMLElement>(`#set-${CSS.escape(id)}`);
    if (!section) {
      section = document.createElement('section');
      section.className = 'settings-section plugin-settings';
      section.id = `set-${id}`;
      section.dataset.section = id;
      section.dataset.plugin = p.id;
      section.innerHTML = `<h2></h2><p class="lead"></p><div class="plugin-custom"></div><div class="plugin-generic"></div>`;
    }
    section.querySelector('h2')!.innerHTML = `${escapeHtml(p.name)}${p.running ? '' : ` <span class="badge warn">${escapeHtml(STATUS[p.status])}</span>`}`;
    section.querySelector('.lead')!.textContent = p.description || '';
    const generic = section.querySelector<HTMLElement>('.plugin-generic')!;
    const typing = generic.contains(document.activeElement) && document.activeElement!.matches('input[type=text], input[type=number]');
    if (force || !typing) generic.innerHTML = sectionBodyHtml(p, data.safe_mode, drawnByFrontend(p.id));
    if (after.nextElementSibling !== section) after.after(section);
    after = section;

    let nav = view.querySelector<HTMLElement>(`.settings-nav [data-nav="${CSS.escape(id)}"]`);
    if (!nav) {
      nav = document.createElement('button');
      nav.className = 'side-item sub';
      nav.dataset.nav = id;
      nav.dataset.pluginNav = p.id;
    }
    nav.innerHTML = `${icon('plugin')}<span>${escapeHtml(p.name)}</span>`;
    if (navAfter.nextElementSibling !== nav) navAfter.after(nav);
    navAfter = nav;
  }
  view.dispatchEvent(new CustomEvent(SECTIONS_EVENT, { detail: { activeRemoved } }));
}

/** Show the plugins in the open Settings page ``view``; ``force`` redraws a section being typed in
 *  too (a value it holds was refused). */
export async function refreshPlugins(view: HTMLElement, force = false): Promise<void> {
  current = view;
  try {
    const data = await api.plugins();
    if (!view.isConnected) return;
    last = data;
    render(view, data, force);
  } catch (e) { view.querySelector('[data-plugins-desc]')!.textContent = (e as Error).message; }
}

/** The plugins changed elsewhere (another window, or one was turned off after failing). */
export function pluginsChanged(): void {
  if (!current?.isConnected) return;
  const a = document.activeElement;
  if (a && current.querySelector('[data-plugin-list]')?.contains(a) && a.matches('input[type=text], input[type=number]')) return;
  refreshPlugins(current);
}

const find = (id: string) => last?.plugins.find((p) => p.id === id);

async function toggle(view: HTMLElement, p: PluginInfo, input: HTMLInputElement): Promise<void> {
  if (input.checked) {
    const perms = permissionWords(p);
    const ok = await confirmDialog(`Turn on ${p.name}?`, {
      title: 'Turn on plugin', confirm: 'Turn On',
      detail: `A plugin runs with full access to your library and this computer. ${perms.length ? `${p.name}: ${perms.join('; ')}.` : ''} Turn it on only if you trust where it comes from.`,
    });
    if (!ok) { input.checked = false; return; }
  }
  input.disabled = true;
  try {
    const res = input.checked ? await api.enablePlugin(p.id) : await api.disablePlugin(p.id);
    if (res.plugin.status === 'failed') toast(`${p.name}: ${res.plugin.problem}`, { type: 'error', timeout: 8000 });
  } catch (e) { toastError(e); }
  await refreshPlugins(view);
  syncPlugins();   // this window's own change is not in the change feed
}

async function saveSetting(view: HTMLElement, id: string, el: HTMLElement): Promise<void> {
  const holder = el.closest<HTMLElement>('[data-pset]')!;
  const key = holder.dataset.pset!;
  let value: unknown;
  if (holder.classList.contains('p-multi')) value = [...holder.querySelectorAll<HTMLInputElement>('input:checked')].map((i) => i.value);
  else if (holder.dataset.ptype === 'bool') value = (holder as HTMLInputElement).checked;
  else if (holder.dataset.ptype === 'multi') value = (holder as HTMLInputElement).value;
  else if (holder.dataset.ptype === 'int' || holder.dataset.ptype === 'float') value = (holder as HTMLInputElement).value === '' ? null : Number((holder as HTMLInputElement).value);
  else value = (holder as HTMLInputElement | HTMLSelectElement).value;
  try {
    const res = await api.setPluginSettings(id, { [key]: value });
    const p = find(id);
    if (p) p.values = res.values;
    flashSaved(holder.closest('.p-setting'));
  } catch (e) {
    toastError(e);
    await refreshPlugins(view, true);
  }
}

function showLog(p: PluginInfo): void {
  openDialog({
    title: `${p.name}: log`, wide: true,
    render: async (body) => {
      body.innerHTML = '<div class="muted">Loading…</div>';
      try {
        const { lines } = await api.pluginLog(p.id);
        body.innerHTML = lines.length ? `<pre class="plugin-log">${escapeHtml(lines.join('\n'))}</pre>` : '<div class="muted">Nothing logged yet.</div>';
        const pre = body.querySelector('pre');
        if (pre) pre.scrollTop = pre.scrollHeight;
      } catch (e) { body.textContent = (e as Error).message; }
    },
    buttons: [{ label: 'Close', kind: 'primary' }],
  });
}

/** Wire the list and the sections; ``go`` shows a section of the page (a row's Settings button). */
export function bindPlugins(view: HTMLElement, go: (navId: string) => void): void {
  view.querySelector<HTMLButtonElement>('[data-plugins-rescan]')!.onclick = () => refreshPlugins(view);
  const content = view.querySelector<HTMLElement>('.settings-content')!;
  // the generic parts only: what a plugin's frontend draws in its section is its own
  const ours = (el: Element) => !!el.closest('[data-plugin-list], .plugin-generic');
  content.addEventListener('change', (e) => {
    const el = e.target as HTMLInputElement;
    if (!ours(el)) return;
    const id = el.closest<HTMLElement>('[data-plugin]')?.dataset.plugin;
    const p = id ? find(id) : undefined;
    if (!p) return;
    if (el.matches('[data-ptoggle]')) toggle(view, p, el);
    else if (el.matches('[data-premote]')) {
      api.setPluginRemoteMcp(p.id, el.checked).then(() => refreshPlugins(view), (err) => { toastError(err); refreshPlugins(view); });
    } else if (el.closest('[data-pset]')) saveSetting(view, p.id, el);
  });
  content.addEventListener('click', async (e) => {
    const t = e.target as Element;
    if (!ours(t)) return;
    const id = t.closest<HTMLElement>('[data-plugin]')?.dataset.plugin;
    const p = id ? find(id) : undefined;
    if (!p || !last) return;
    if (t.closest('[data-popen]')) go(pluginNavId(p.id));
    else if (t.closest('[data-plog]')) showLog(p);
    else if (t.closest('[data-prestart]')) {
      try {
        const res = await api.restartPlugin(p.id);
        toast(res.plugin.running ? `${p.name} restarted` : `${p.name}: ${res.plugin.problem}`, { type: res.plugin.running ? 'success' : 'error' });
      } catch (err) { toastError(err); }
      refreshPlugins(view);
      syncPlugins();
    }
  });
}
