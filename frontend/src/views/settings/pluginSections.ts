// Keep plugin settings sections in step with the registry while Settings stays open. Each mount
// owns its DOM and cleanup; unrelated sections retain their controls and pending edits.
import { type SettingsSection, settingsSections } from '../../extend.js';
import { icon } from '../../icons.js';
import { escapeHtml } from '../../util.js';

interface Mounted {
  spec: SettingsSection;
  section: HTMLElement;
  nav: HTMLButtonElement;
  cleanup?: () => void;
}

export function mountPluginSections(page: HTMLElement, onActiveRemoved: () => void): () => void {
  const mounted = new Map<string, Mounted>();
  const remove = (id: string, entry: Mounted) => {
    mounted.delete(id);
    try { entry.cleanup?.(); } catch (e) { console.error(e); }
    entry.section.remove();
    entry.nav.remove();
  };
  const sync = () => {
    let activeRemoved = false;
    const wanted = new Map(settingsSections.all().map((s) => [s.id, s]));
    for (const [id, entry] of mounted) {
      if (wanted.get(id) === entry.spec) continue;
      activeRemoved ||= entry.nav.classList.contains('active');
      remove(id, entry);
    }
    for (const [key, spec] of wanted) {
      if (mounted.has(key)) continue;
      const id = `x-${key.replace(/[^a-z0-9-]/gi, '-')}`;
      const section = document.createElement('section');
      section.className = 'settings-section';
      section.id = `set-${id}`;
      section.innerHTML = `<h2>${escapeHtml(spec.title)}${spec.source ? ` <span class="ip-source">${escapeHtml(spec.source)}</span>` : ''}</h2><div class="plugin-section"${spec.owner ? ` data-plugin="${escapeHtml(spec.owner)}"` : ''}></div>`;
      const after = spec.after ? page.querySelector(`#set-${CSS.escape(spec.after)}`) : null;
      if (after) after.after(section); else page.querySelector('#set-plugins')!.before(section);
      const nav = document.createElement('button');
      nav.className = 'side-item';
      nav.dataset.nav = id;
      nav.innerHTML = `${icon(spec.icon || 'plugin')}<span>${escapeHtml(spec.title)}</span>`;
      const navAfter = spec.after ? page.querySelector(`.settings-nav [data-nav="${CSS.escape(spec.after)}"]`) : null;
      if (navAfter) navAfter.after(nav); else page.querySelector('.settings-nav [data-nav="plugins"]')!.before(nav);
      const entry: Mounted = { spec, section, nav };
      mounted.set(key, entry);
      try {
        const cleanup = spec.mount(section.querySelector<HTMLElement>('.plugin-section')!);
        if (typeof cleanup === 'function') entry.cleanup = cleanup;
      } catch (e) { section.querySelector('.plugin-section')!.textContent = (e as Error).message; }
    }
    if (activeRemoved) onActiveRemoved();
  };
  const unsubscribe = settingsSections.subscribe(sync);
  sync();
  return () => {
    unsubscribe();
    for (const [id, entry] of mounted) remove(id, entry);
  };
}
