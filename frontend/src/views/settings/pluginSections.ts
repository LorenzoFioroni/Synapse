// What plugins' frontends draw in Settings (p.settingsPage.register), kept in step with the
// registry while Settings stays open. A plugin's part goes inside its own section (plugins.ts
// makes those once the plugins are listed); a part with no plugin behind it gets a section of its
// own. Each mount owns its DOM and cleanup; the other parts keep their controls and pending edits.
import { type SettingsSection, settingsSections } from '../../extend.js';
import { icon } from '../../icons.js';
import { escapeHtml } from '../../util.js';
import { SECTIONS_EVENT, refreshPlugins } from './plugins.js';

interface Mounted {
  spec: SettingsSection;
  /** The element the part was drawn in. */
  holder: HTMLElement;
  /** A section and navigation entry of its own (parts with no plugin behind them). */
  section?: HTMLElement;
  nav?: HTMLButtonElement;
  cleanup?: () => void;
}

export function mountPluginSections(page: HTMLElement, onActiveRemoved: () => void): () => void {
  const mounted = new Map<string, Mounted>();
  const remove = (id: string, entry: Mounted) => {
    mounted.delete(id);
    try { entry.cleanup?.(); } catch (e) { console.error(e); }
    entry.holder.remove();
    entry.section?.remove();
    entry.nav?.remove();
  };
  const draw = (entry: Mounted) => {
    try {
      const cleanup = entry.spec.mount(entry.holder);
      if (typeof cleanup === 'function') entry.cleanup = cleanup;
    } catch (e) { entry.holder.textContent = (e as Error).message; }
  };
  /** A part inside its plugin's section, or null while that section is not there. */
  const inPlugin = (spec: SettingsSection): Mounted | null => {
    const box = page.querySelector<HTMLElement>(`.plugin-settings[data-plugin="${CSS.escape(spec.owner!)}"] .plugin-custom`);
    if (!box) return null;
    const holder = document.createElement('div');
    holder.className = 'plugin-part';
    holder.dataset.part = spec.id;
    const pluginName = box.closest('.plugin-settings')!.querySelector('h2')?.firstChild?.textContent?.trim();
    if (spec.title && spec.title !== pluginName) holder.innerHTML = `<h3 class="plugin-part-title">${escapeHtml(spec.title)}</h3>`;
    const body = document.createElement('div');
    body.className = 'plugin-section';
    holder.appendChild(body);
    box.appendChild(holder);
    return { spec, holder: body, section: holder };
  };
  const ownSection = (spec: SettingsSection): Mounted => {
    const id = `x-${spec.id.replace(/[^a-z0-9-]/gi, '-')}`;
    const section = document.createElement('section');
    section.className = 'settings-section';
    section.id = `set-${id}`;
    section.innerHTML = `<h2>${escapeHtml(spec.title)}${spec.source ? ` <span class="ip-source">${escapeHtml(spec.source)}</span>` : ''}</h2><div class="plugin-section"></div>`;
    const after = spec.after ? page.querySelector(`#set-${CSS.escape(spec.after)}`) : null;
    if (after) after.after(section); else page.querySelector('#set-plugins')!.before(section);
    const nav = document.createElement('button');
    nav.className = 'side-item';
    nav.dataset.nav = id;
    nav.innerHTML = `${icon(spec.icon || 'plugin')}<span>${escapeHtml(spec.title)}</span>`;
    const navAfter = spec.after ? page.querySelector(`.settings-nav [data-nav="${CSS.escape(spec.after)}"]`) : null;
    if (navAfter) navAfter.after(nav); else page.querySelector('.settings-nav [data-nav="plugins"]')!.before(nav);
    return { spec, holder: section.querySelector<HTMLElement>('.plugin-section')!, section, nav };
  };
  const sync = () => {
    let activeRemoved = false;
    const wanted = new Map(settingsSections.all().map((s) => [s.id, s]));
    for (const [id, entry] of mounted) {
      // gone from the registry, replaced, or its plugin's section was taken away (turned off)
      if (wanted.get(id) === entry.spec && entry.holder.isConnected) continue;
      activeRemoved ||= !!entry.nav?.classList.contains('active');
      remove(id, entry);
    }
    for (const [key, spec] of wanted) {
      if (mounted.has(key)) continue;
      const entry = spec.owner ? inPlugin(spec) : ownSection(spec);
      if (!entry) continue;   // drawn once its plugin's section is there
      mounted.set(key, entry);
      draw(entry);
    }
    if (activeRemoved) onActiveRemoved();
  };
  // A plugin's frontend came or went: the plugin started or stopped, so its section may come or go
  // too, and a part that draws some of its settings itself takes them out of the generic list.
  const unsubscribe = settingsSections.subscribe(() => { sync(); void refreshPlugins(page); });
  page.addEventListener(SECTIONS_EVENT, sync);
  sync();
  return () => {
    unsubscribe();
    page.removeEventListener(SECTIONS_EVENT, sync);
    for (const [id, entry] of mounted) remove(id, entry);
  };
}
