// Search across every Settings page without rebuilding controls or indexing their saved values.
// While searching, the matching rows of every page are shown together, under their page's title
// with an "Open page" link, and the navigation counts the matches of each page.
import { plural, modKey } from '../../util.js';

export interface SettingsSearch {
  readonly filtering: boolean;
  clear(): void;
  dispose(): void;
}

const fold = (text: string) => text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();

/** What is not searched: field values, macro checks, and the search's own additions. */
const SKIP = 'input, textarea, select, script, style, [contenteditable="true"], .saved-pill, [data-macro-check], .open-page';

function searchableText(el: Element): string {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const text: string[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    // Labels and help text are searchable; credentials, macros and other field values are not.
    if (!node.parentElement?.closest(SKIP)) text.push(node.textContent || '');
  }
  return fold(text.join(' '));
}

/** The rows of a section a search can show on their own: settings and plugins, outermost only. */
function rowsOf(section: Element): HTMLElement[] {
  return [...section.querySelectorAll<HTMLElement>('.setting, .plugin-row')]
    .filter((row) => !row.parentElement?.closest('.setting, .plugin-row'));
}

/** The navigation entry a section counts towards: a plugin's page counts for Plugins. */
const navOf = (sectionId: string) => (sectionId.startsWith('plugin-') ? 'plugins' : sectionId);

export function mountSettingsSearch(page: HTMLElement, activate: (id: string) => void, currentId: () => string): SettingsSearch {
  const input = page.querySelector<HTMLInputElement>('[data-settings-search]')!;
  const clearButton = page.querySelector<HTMLButtonElement>('[data-settings-search-clear]')!;
  const summary = page.querySelector<HTMLElement>('[data-settings-search-summary]')!;
  const empty = page.querySelector<HTMLElement>('[data-settings-search-empty]')!;
  const nav = page.querySelector<HTMLElement>('.settings-nav')!;
  const inner = page.querySelector<HTMLElement>('.settings-inner')!;
  const content = page.querySelector<HTMLElement>('.settings-content')!;
  let before: string | null = null;

  /** Each section gets an "Open page" link, shown only while searching. */
  const addLinks = () => page.querySelectorAll<HTMLElement>('.settings-section').forEach((section) => {
    if (section.querySelector(':scope > .open-page')) return;
    const link = document.createElement('button');
    link.className = 'link-btn open-page';
    link.dataset.openSection = section.id.slice(4);
    link.textContent = 'Open page →';
    section.prepend(link);
  });

  const apply = (changed = false) => {
    const words = fold(input.value).trim().split(/\s+/u).filter(Boolean);
    const searching = !!words.length;
    if (searching && before === null) before = currentId();
    if (searching) addLinks();
    page.classList.toggle('settings-searching', searching);
    inner.classList.toggle('searching', searching);
    const counts = new Map<string, number>();
    let rows = 0, pages = 0;
    for (const section of page.querySelectorAll<HTMLElement>('.settings-section')) {
      const all = rowsOf(section);
      const hit = searching && words.every((word) => searchableText(section).includes(word));
      let n = 0;
      if (hit) {
        const matching = new Set(all.filter((row) => { const t = searchableText(row); return words.every((word) => t.includes(word)); }));
        // Rows that match on their own are shown alone; a page that matches only as a whole shows everything.
        all.forEach((row) => row.classList.toggle('search-miss', matching.size > 0 && !matching.has(row)));
        section.querySelectorAll<HTMLElement>('.card').forEach((card) => {
          const inCard = all.filter((row) => card.contains(row));
          card.classList.toggle('search-miss', inCard.length > 0 && inCard.every((row) => row.classList.contains('search-miss')));
        });
        n = Math.max(1, matching.size);
        rows += n; pages += 1;
      } else {
        all.forEach((row) => row.classList.remove('search-miss'));
        section.querySelectorAll('.card.search-miss').forEach((card) => card.classList.remove('search-miss'));
      }
      section.classList.toggle('search-hit', hit);
      const id = navOf(section.id.slice(4));
      counts.set(id, (counts.get(id) || 0) + n);
    }
    nav.querySelectorAll<HTMLElement>('[data-nav]').forEach((button) => {
      const n = counts.get(button.dataset.nav!) || 0;
      button.classList.toggle('nav-miss', searching && !n);
      const count = button.querySelector<HTMLElement>('[data-nav-count]');
      if (count) { count.hidden = !searching || !n; count.textContent = String(n); }
    });
    clearButton.hidden = !input.value;
    summary.hidden = !searching;
    empty.hidden = !searching || !!rows;
    const message = !searching ? '' : rows ? `${plural(rows, 'setting')} on ${plural(pages, 'page')}` : 'No settings found';
    if (summary.textContent !== message) summary.textContent = message;
    if (!searching && before !== null) {
      const back = before;
      before = null;
      activate(back);
    }
    if (changed) content.scrollTop = 0;
  };
  const clear = () => { if (!input.value) return; input.value = ''; apply(); };
  input.addEventListener('input', () => apply(true));
  clearButton.addEventListener('click', () => { clear(); input.focus(); });
  page.addEventListener('keydown', (event) => {
    if (modKey(event) && !event.shiftKey && event.key.toLowerCase() === 'f') {
      event.preventDefault(); event.stopPropagation(); input.focus(); input.select();
    } else if (event.key === '/' && !(event.target as Element).closest('input, textarea, select, [contenteditable="true"]')) {
      event.preventDefault(); input.focus(); input.select();
    } else if (event.key === 'Escape' && event.target === input && input.value) {
      event.preventDefault(); event.stopPropagation(); clear();
    }
  });
  // Plugins may add sections or render controls asynchronously while the filter is active.
  const observer = new MutationObserver(() => { if (input.value.trim()) apply(); });
  observer.observe(inner, { childList: true, subtree: true, characterData: true });
  return {
    get filtering() { return !!input.value.trim(); },
    clear,
    dispose() { observer.disconnect(); },
  };
}
