// Filter existing Settings sections without rebuilding controls or indexing their saved values.
import { modKey, plural } from '../../util.js';

export interface SettingsSearch {
  readonly filtering: boolean;
  clear(): void;
  reveal(id: string): void;
  dispose(): void;
}

const fold = (text: string) => text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();

function searchableText(section: Element): string {
  const walker = document.createTreeWalker(section, NodeFilter.SHOW_TEXT);
  const text: string[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    // Labels and help text are searchable; credentials, macros and other field values are not.
    if (!node.parentElement?.closest('input, textarea, select, script, style, [contenteditable="true"], .saved-pill, [data-macro-check]')) text.push(node.textContent || '');
  }
  return fold(text.join(' '));
}

export function mountSettingsSearch(page: HTMLElement, activate: (id: string, scroll?: boolean) => void): SettingsSearch {
  const input = page.querySelector<HTMLInputElement>('[data-settings-search]')!;
  const clearButton = page.querySelector<HTMLButtonElement>('[data-settings-search-clear]')!;
  const summary = page.querySelector<HTMLElement>('[data-settings-search-summary]')!;
  const empty = page.querySelector<HTMLElement>('[data-settings-search-empty]')!;
  const nav = page.querySelector<HTMLElement>('.settings-nav')!;
  const content = page.querySelector<HTMLElement>('.settings-content')!;
  let before: { id: string; top: number } | null = null;

  const apply = (changed = false) => {
    const words = fold(input.value).trim().split(/\s+/u).filter(Boolean);
    if (words.length && !before) before = { id: nav.querySelector<HTMLElement>('.active')?.dataset.nav || 'general', top: content.scrollTop };
    const sections = [...page.querySelectorAll<HTMLElement>('.settings-section')];
    const matches = sections.filter((section) => {
      const text = words.length ? searchableText(section) : '';
      section.hidden = !words.every((word) => text.includes(word));
      return !section.hidden;
    });
    const ids = new Set(matches.map((section) => section.id.slice(4)));
    nav.querySelectorAll<HTMLElement>('[data-nav]').forEach((button) => { button.hidden = !ids.has(button.dataset.nav!); });
    nav.classList.toggle('settings-searching', !!words.length);
    clearButton.hidden = !input.value;
    summary.hidden = !words.length;
    empty.hidden = !words.length || !!matches.length;
    const message = words.length ? `${plural(matches.length, 'section')} found` : '';
    if (summary.textContent !== message) summary.textContent = message;
    if (!words.length && before) {
      const restore = before;
      before = null;
      activate(ids.has(restore.id) ? restore.id : 'general', false);
      content.scrollTop = restore.top;
    } else if (words.length) {
      if (changed) content.scrollTop = 0;
      const active = nav.querySelector<HTMLElement>('.active')?.dataset.nav;
      if (changed || !active || !ids.has(active)) {
        nav.querySelectorAll('.active').forEach((button) => button.classList.remove('active'));
        if (matches[0]) activate(matches[0].id.slice(4), false);
      }
    }
  };
  const clear = () => { input.value = ''; apply(); };
  input.addEventListener('input', () => apply(true));
  clearButton.addEventListener('click', () => { clear(); input.focus(); });
  page.addEventListener('keydown', (event) => {
    if (modKey(event) && !event.shiftKey && event.key.toLowerCase() === 'f') {
      event.preventDefault(); event.stopPropagation(); input.focus(); input.select();
    } else if (event.key === 'Escape' && event.target === input && input.value) {
      event.preventDefault(); event.stopPropagation(); clear();
    }
  });
  // Plugins may add sections or render controls asynchronously while the filter is active.
  const observer = new MutationObserver(() => { if (input.value.trim()) apply(); });
  observer.observe(page.querySelector('.settings-inner')!, { childList: true, subtree: true, characterData: true });
  return {
    get filtering() { return !!input.value.trim(); },
    clear,
    reveal(id) { if (page.querySelector<HTMLElement>(`#set-${CSS.escape(id)}`)?.hidden) clear(); },
    dispose() { observer.disconnect(); },
  };
}
