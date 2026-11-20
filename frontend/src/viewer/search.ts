// Find in the PDF, independently of its virtualized canvas/text layers and the Ask AI panel.
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { icon } from '../icons.js';
import type { PageWrapper } from './viewer.js';
import { PdfSearchIndex, type SearchMatch } from './searchModel.js';

export function initPdfSearch(container: HTMLElement, doc: () => PDFDocumentProxy | null): { open(): void; ready(): void } {
  const toggle = document.getElementById('pdf-find-toggle')!;
  const bar = document.createElement('div');
  bar.className = 'pdf-findbar';
  bar.id = 'pdf-findbar';
  bar.hidden = true;
  bar.setAttribute('role', 'search');
  bar.setAttribute('aria-label', 'Find in PDF');
  bar.innerHTML = `<div class="pdf-find-query">${icon('search')}
    <input type="search" aria-label="Find in PDF" placeholder="Find in PDF…" autocomplete="off" spellcheck="false">
    <span class="pdf-find-count" role="status" aria-live="polite" aria-atomic="true"></span></div>
    <div class="pdf-find-actions">
      <button type="button" class="icon-btn" data-prev title="Previous match (Shift+Enter)" aria-label="Previous match">${icon('chevronUp')}</button>
      <button type="button" class="icon-btn" data-next title="Next match (Enter)" aria-label="Next match">${icon('chevronDown')}</button>
      <button type="button" class="icon-btn" data-close title="Close search (Esc)" aria-label="Close PDF search">${icon('close')}</button>
    </div><div class="pdf-find-options">
      <label><input type="checkbox" data-case> Match case</label>
      <label><input type="checkbox" data-word> Whole words</label>
    </div>`;
  document.getElementById('toolbar')!.after(bar);
  toggle.setAttribute('aria-controls', bar.id);
  toggle.setAttribute('aria-expanded', 'false');
  const input = bar.querySelector<HTMLInputElement>('input[type="search"]')!;
  const count = bar.querySelector<HTMLElement>('.pdf-find-count')!;
  const prev = bar.querySelector<HTMLButtonElement>('[data-prev]')!;
  const next = bar.querySelector<HTMLButtonElement>('[data-next]')!;
  const caseBox = bar.querySelector<HTMLInputElement>('[data-case]')!;
  const wordBox = bar.querySelector<HTMLInputElement>('[data-word]')!;
  let index: PdfSearchIndex | null = null;
  let hasText = false;
  let generation = 0, navigation = 0, scanned = 0, failed = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let matches: SearchMatch[] = [], selected: SearchMatch | null = null;
  let pendingNavigation: { match: SearchMatch; ticket: number } | null = null;
  const pages = new Map<number, SearchMatch[]>();
  let previousFocus: HTMLElement | null = null;

  function status(): void {
    const total = doc()?.numPages || 0, running = !!input.value.trim() && scanned < total;
    prev.disabled = next.disabled = !matches.length;
    const number = selected ? matches.indexOf(selected) + 1 : 0;
    const result = matches.length ? `${number} of ${matches.length}` : !running && !failed && !hasText ? 'No searchable text' : 'No matches';
    const progress = running ? ` · Searching ${scanned}/${total} pages…` : '';
    const errors = failed ? ` · ${failed} ${failed === 1 ? 'page' : 'pages'} could not be searched` : '';
    count.textContent = !input.value.trim() ? '' : !doc() ? 'Loading PDF…' : result + progress + errors;
    bar.setAttribute('aria-busy', String(running));
  }

  /** Paint only live text layers. Fractions of the page keep the overlays aligned during zoom. */
  function paint(wrapper: PageWrapper): void {
    wrapper.querySelector('.pdf-search-layer')?.remove();
    if (bar.hidden) return;
    const text = wrapper.zcTextLayer;
    const layer = wrapper.querySelector<HTMLElement>('.textLayer');
    const results = pages.get(Number(wrapper.dataset.pageNumber));
    if (!text || !layer || !results?.length) return;
    const overlay = document.createElement('div');
    overlay.className = 'pdf-search-layer';
    overlay.setAttribute('aria-hidden', 'true');
    const box = wrapper.getBoundingClientRect();
    if (!box.width || !box.height) return;
    // Very common queries must not create unbounded DOM. The active result is always painted.
    for (const match of results.filter((m, i) => i < 1000 || m === selected)) {
      for (let i = match.begin.item; i <= match.end.item; i++) {
        const span = text.textDivs[i], node = span?.firstChild;
        if (!node || node.nodeType !== Node.TEXT_NODE || !layer.contains(span)) continue;
        const start = i === match.begin.item ? match.begin.offset : 0;
        const end = i === match.end.item ? match.end.offset : node.textContent!.length;
        if (start >= end) continue;
        const range = document.createRange();
        range.setStart(node, start); range.setEnd(node, end);
        for (const rect of range.getClientRects()) {
          if (!rect.width || !rect.height) continue;
          const mark = document.createElement('div');
          mark.className = `pdf-search-hit${match === selected ? ' current' : ''}`;
          mark.style.left = `${100 * (rect.left - box.left) / box.width}%`;
          mark.style.top = `${100 * (rect.top - box.top) / box.height}%`;
          mark.style.width = `${100 * rect.width / box.width}%`;
          mark.style.height = `${100 * rect.height / box.height}%`;
          overlay.append(mark);
        }
      }
    }
    layer.append(overlay);
  }

  function paintLive(): void {
    for (const wrapper of container.querySelectorAll<PageWrapper>('.page-wrapper:has(.textLayer)')) paint(wrapper);
  }

  function scrollToMatch(wrapper: PageWrapper): boolean {
    paint(wrapper);
    const mark = wrapper.querySelector<HTMLElement>('.pdf-search-hit.current');
    if (!mark) return false;
    const r = mark.getBoundingClientRect(), c = container.getBoundingClientRect();
    container.scrollTop += r.top - c.top - container.clientHeight / 3;
    if (r.left < c.left || r.right > c.right) container.scrollLeft += r.left - c.left - container.clientWidth / 2;
    return true;
  }

  function reveal(match: SearchMatch): void {
    pendingNavigation = { match, ticket: ++navigation };
    const wrapper = container.querySelector<PageWrapper>(`.page-wrapper[data-page-number="${match.page}"]`);
    if (!wrapper) return;
    if (scrollToMatch(wrapper)) pendingNavigation = null;
    else {
      // This is enough to render a distant page. The text-ready event finishes the precise jump.
      const r = wrapper.getBoundingClientRect(), c = container.getBoundingClientRect();
      container.scrollTop += r.top - c.top;
    }
  }

  function step(dir: number): void {
    if (!matches.length) return;
    const at = selected ? matches.indexOf(selected) : -1;
    selected = matches[(at + dir + matches.length) % matches.length];
    status(); paintLive(); reveal(selected);
  }

  function changed(): void {
    const epoch = ++generation;
    navigation++;
    clearTimeout(timer);
    matches = []; selected = null; pages.clear(); scanned = failed = 0;
    paintLive(); status();
    if (!input.value.trim() || bar.hidden || !doc()) return;
    timer = setTimeout(() => void search(epoch), 150);
  }

  async function search(epoch: number): Promise<void> {
    const pdf = doc();
    if (!pdf || epoch !== generation || bar.hidden) return;
    index ??= new PdfSearchIndex(pdf.numPages, async (num) => {
      const content = await (await pdf.getPage(num)).getTextContent();
      hasText ||= content.items.some((item) => 'str' in item && !!item.str.trim());
      return content.items.filter((item) => 'str' in item);
    });
    const start = currentPage(container);
    await index.search(input.value, { matchCase: caseBox.checked, wholeWord: wordBox.checked }, start,
      () => epoch !== generation || bar.hidden, (num, found, error) => {
        scanned++; failed += Number(error); pages.set(num, found);
        matches = [...pages].sort(([a], [b]) => a - b).flatMap(([, m]) => m);
        status();
        if (!selected && found.length) {
          selected = found[0];
          status(); reveal(selected);
        } else {
          const wrapper = container.querySelector<PageWrapper>(`.page-wrapper[data-page-number="${num}"]`);
          if (wrapper?.zcTextLayer) paint(wrapper);
        }
      });
    if (epoch === generation && !bar.hidden) status();
  }

  function open(): void {
    if (bar.hidden) {
      previousFocus = document.activeElement as HTMLElement | null;
      bar.hidden = false; toggle.setAttribute('aria-expanded', 'true');
      changed();
    }
    input.focus(); input.select();
  }

  function close(): void {
    generation++; navigation++; clearTimeout(timer);
    bar.hidden = true; toggle.setAttribute('aria-expanded', 'false');
    paintLive();
    (previousFocus?.isConnected ? previousFocus : toggle).focus({ preventScroll: true });
  }

  input.addEventListener('input', (e) => { if (!(e as InputEvent).isComposing) changed(); });
  input.addEventListener('compositionend', changed);
  caseBox.addEventListener('change', changed); wordBox.addEventListener('change', changed);
  prev.addEventListener('click', () => step(-1)); next.addEventListener('click', () => step(1));
  toggle.addEventListener('click', () => bar.hidden ? open() : close());
  bar.querySelector('[data-close]')!.addEventListener('click', close);
  document.addEventListener('keydown', (e) => {
    if (e.isComposing || e.altKey || e.defaultPrevented) return;
    const mod = e.metaKey || e.ctrlKey, key = e.key.toLowerCase();
    if (mod && key === 'f' && !e.shiftKey) { e.preventDefault(); e.stopPropagation(); open(); }
    else if (!bar.hidden && ((mod && key === 'g') || (e.target === input && e.key === 'Enter'))) {
      e.preventDefault(); e.stopPropagation(); step(e.shiftKey ? -1 : 1);
    } else if (!bar.hidden && e.key === 'Escape' && !(e.target as Element).closest('.ai-panel, .zc-menu, .zc-link-pop')) {
      e.preventDefault(); e.stopPropagation(); close();
    } else if (!bar.hidden && !bar.contains(e.target as Node) &&
      !(e.target as HTMLElement).isContentEditable && !['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement).tagName) &&
      ['PageDown', 'PageUp', 'Home', 'End', 'ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', ' '].includes(e.key)) {
      navigation++;
    }
  }, true);
  // Stop late text rendering from pulling the reader back after they deliberately move away.
  for (const event of ['wheel', 'pointerdown', 'touchstart']) container.addEventListener(event, () => navigation++, { passive: true });
  document.addEventListener('zc:textlayerrendered', ({ detail: { wrapper, num } }) => {
    paint(wrapper);
    const pending = pendingNavigation;
    if (bar.hidden || !pending || pending.match.page !== num) return;
    requestAnimationFrame(() => {
      if (!bar.hidden && navigation === pending.ticket && selected === pending.match && scrollToMatch(wrapper)) pendingNavigation = null;
    });
  });
  status();
  return { open, ready: changed };
}

/** The page at the top of the viewport, regardless of which pages currently have text layers. */
function currentPage(container: HTMLElement): number {
  const top = container.getBoundingClientRect().top;
  for (const wrapper of container.querySelectorAll<HTMLElement>('.page-wrapper')) {
    if (wrapper.getBoundingClientRect().bottom > top) return Number(wrapper.dataset.pageNumber);
  }
  return 1;
}
