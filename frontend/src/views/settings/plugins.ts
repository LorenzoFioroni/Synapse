// Settings → Plugins: the plugins found in the plugins folder, turning them on and off, their
// settings (drawn from what each manifest declares), and their log. See docs/plugins.md.
import { api } from '../../api.js';
import { syncPlugins } from '../../plugins/host.js';
import { icon } from '../../icons.js';
import { confirmDialog, openDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import type { PluginInfo, PluginSetting, PluginsOverview } from '../../types.js';
import { escapeHtml, formatDate } from '../../util.js';
import { flashSaved } from './page.js';

let current: HTMLElement | null = null;
let last: PluginsOverview | null = null;
const expanded = new Set<string>();

const STATUS: Record<PluginInfo['status'], string> = {
  on: 'On', off: 'Off', failed: 'Turned off after a failure', incompatible: 'Needs another version of Synapse',
  invalid: 'Cannot be read', 'safe-mode': 'Not started (safe mode)',
};

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
  if (perms.mcp) out.push('Offers tools to Claude');
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

function detailsHtml(p: PluginInfo, safeMode: boolean): string {
  const settings = (p.settings || []).map((s) => `<div class="p-setting" data-pkey="${escapeHtml(s.key)}">
      <div class="s-text"><div class="s-title">${escapeHtml(s.label)} <span class="saved-pill">${icon('check', 'icon-sm')} Saved</span></div>${s.description ? `<div class="s-desc">${escapeHtml(s.description)}</div>` : ''}</div>
      <div class="p-control">${settingControl(p, s)}</div></div>`).join('');
  const offers = [
    p.tools?.length ? `Tools for Claude: ${p.tools.map((t) => `<span class="mono">${escapeHtml(t)}</span>`).join(', ')}` : '',
    p.routes?.length ? `Serves ${p.routes.map((r) => `<span class="mono">/api/plugins/${escapeHtml(p.id)}${escapeHtml(r.rule)}</span>`).join(', ')}` : '',
    p.events?.length ? `Listens to ${p.events.map((e) => `<span class="mono">${escapeHtml(e)}</span>`).join(', ')}` : '',
    p.schedules?.length ? `Runs ${p.schedules.map((x) => `<span class="mono">${escapeHtml(x.name)}</span> (${escapeHtml(x.rule)})`).join(', ')}` : '',
    p.jobs ? `${p.jobs} job${p.jobs === 1 ? '' : 's'} waiting` : '',
  ].filter(Boolean);
  const remote = p.permissions?.mcp ? `<div class="p-setting">
      <div class="s-text"><div class="s-title">Tools for Claude on other computers</div>
        <div class="s-desc">Also offer this plugin's tools to AI apps using the library over the network (Settings → AI Access).</div></div>
      <div class="p-control"><label class="switch"><input type="checkbox" data-premote ${p.remote_mcp ? 'checked' : ''}><span></span></label></div></div>` : '';
  return `<div class="p-details">
    ${settings}${remote}
    ${offers.length ? `<div class="p-offers">${offers.map((o) => `<div>${o}</div>`).join('')}</div>` : ''}
    <div class="p-foot"><span class="mono p-folder" title="${escapeHtml(p.folder)}">${escapeHtml(p.folder)}</span>
      ${p.enabled && !safeMode ? `<button class="btn btn-sm" data-prestart>${icon('refresh')} Restart</button>` : ''}
      <button class="btn btn-sm" data-plog>${icon('text')} Log</button></div>
  </div>`;
}

function rowHtml(p: PluginInfo, safeMode: boolean): string {
  const usable = p.status !== 'invalid' && p.status !== 'incompatible' && !safeMode;
  const perms = permissionWords(p);
  const meta = [p.author && `by ${escapeHtml(p.author)}`, p.version && `version ${escapeHtml(p.version)}`,
    p.core ? 'core plugin: part of Synapse' : p.source === 'bundled' ? 'comes with Synapse' : p.source === 'dev' ? 'being written (restarts when its files change)' : p.replaces_bundled ? 'replaces the one that comes with Synapse' : ''].filter(Boolean).join(' · ');
  const problem = p.problem ? `<div class="form-error">${escapeHtml(STATUS[p.status])}: ${escapeHtml(p.problem)}${p.failed_at ? ` (${escapeHtml(formatDate(p.failed_at, true))})` : ''}</div>` : '';
  const open = expanded.has(p.id) && p.status !== 'invalid';
  return `<div class="plugin-row ${open ? 'open' : ''}" data-plugin="${escapeHtml(p.id)}">
    <div class="p-head">
      <span class="p-icon">${icon('plugin')}</span>
      <div class="p-main">
        <div class="p-name">${escapeHtml(p.name)}${p.running ? '' : p.enabled && safeMode ? ' <span class="badge warn">Safe mode</span>' : ''}</div>
        ${p.description ? `<div class="p-desc">${escapeHtml(p.description)}</div>` : ''}
        ${meta ? `<div class="p-meta">${meta}</div>` : ''}
        ${perms.length ? `<div class="p-perms">${perms.map((w) => `<span>${escapeHtml(w)}</span>`).join('')}</div>` : ''}
        ${problem}
      </div>
      <div class="p-side">
        <label class="switch" title="${p.enabled ? 'Turn off' : 'Turn on'}"><input type="checkbox" data-ptoggle ${p.enabled && p.status !== 'failed' ? 'checked' : ''} ${usable ? '' : 'disabled'}><span></span></label>
        ${p.status !== 'invalid' ? `<button class="btn btn-ghost btn-sm" data-pmore aria-expanded="${open}">${open ? 'Less' : 'More'}</button>` : ''}
      </div>
    </div>
    ${open ? detailsHtml(p, safeMode) : ''}
  </div>`;
}

function render(view: HTMLElement, data: PluginsOverview): void {
  const desc = view.querySelector<HTMLElement>('[data-plugins-desc]')!;
  const list = view.querySelector<HTMLElement>('[data-plugin-list]')!;
  desc.innerHTML = (data.safe_mode ? `<div class="form-error">Synapse was started in safe mode (<span class="mono">--safe-mode</span>): no plugin runs until it is started normally.</div>` : '')
    + `Put a plugin's folder in <span class="mono">${escapeHtml(data.dir)}</span>, then press Look Again. Plugin API ${escapeHtml(data.api)}.`;
  list.innerHTML = data.plugins.length
    ? data.plugins.map((p) => rowHtml(p, data.safe_mode)).join('')
    : '<div class="p-empty">No plugins yet.</div>';
}

/** Show the plugins in the open Settings page ``view``. */
export async function refreshPlugins(view: HTMLElement): Promise<void> {
  current = view;
  try {
    const data = await api.plugins();
    if (!view.isConnected) return;
    last = data;
    render(view, data);
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
    await refreshPlugins(view);
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

export function bindPlugins(view: HTMLElement): void {
  view.querySelector<HTMLButtonElement>('[data-plugins-rescan]')!.onclick = () => refreshPlugins(view);
  const list = view.querySelector<HTMLElement>('[data-plugin-list]')!;
  list.addEventListener('change', (e) => {
    const el = e.target as HTMLInputElement;
    const id = el.closest<HTMLElement>('[data-plugin]')?.dataset.plugin;
    const p = id ? find(id) : undefined;
    if (!p) return;
    if (el.matches('[data-ptoggle]')) toggle(view, p, el);
    else if (el.matches('[data-premote]')) {
      api.setPluginRemoteMcp(p.id, el.checked).then(() => refreshPlugins(view), (err) => { toastError(err); refreshPlugins(view); });
    } else if (el.closest('[data-pset]')) saveSetting(view, p.id, el);
  });
  list.addEventListener('click', async (e) => {
    const t = e.target as Element;
    const id = t.closest<HTMLElement>('[data-plugin]')?.dataset.plugin;
    const p = id ? find(id) : undefined;
    if (!p || !last) return;
    if (t.closest('[data-pmore]')) {
      if (expanded.has(p.id)) expanded.delete(p.id); else expanded.add(p.id);
      render(view, last);
    } else if (t.closest('[data-plog]')) showLog(p);
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
