// Display equations wider than the note: shrink them a little (down to MIN_SCALE) before
// falling back to a horizontal scroll bar.
export const MIN_SCALE = 0.75;

interface Fit { root: HTMLElement; d: HTMLElement; box: HTMLElement; width: () => number; old: string; room: number; scale: string }

/**
 * Fit the display equations under ``roots``; returns the roots where a size changed. Reads and
 * writes are batched (clear every scale, measure every equation, set every scale) so a long note
 * costs a few layouts, not one per equation: interleaving them forced the whole page to be laid
 * out again for each formula.
 */
function fit(roots: Iterable<HTMLElement>): Set<HTMLElement> {
  const fits: Fit[] = [];
  for (const root of roots) {
    const list = root.matches?.('.katex-display') ? [root] : root.querySelectorAll<HTMLElement>('.katex-display');
    for (const d of list) {
      const box = d.closest<HTMLElement>('.math-block, .math.display, .cm-lp-mathblock, .cm-lp-math') || d.parentElement;
      const html = d.querySelector('.katex-html');
      if (!box || !html) continue;
      const main = html.querySelector(':scope > .eq-main');
      const tag = html.querySelector<HTMLElement>(':scope > .katex-tag:not(.eq-ghost)');
      // Without a number, .katex-html is a full-width block: its scrollWidth is never below the
      // box's width, so only a real overflow counts (else every such formula seemed 2px too wide).
      const width = () => main ? main.scrollWidth + (tag ? tag.offsetWidth : 0) : html.scrollWidth > html.clientWidth ? html.scrollWidth : 0;
      fits.push({ root, d, box, width, old: d.style.getPropertyValue('--eq-scale'), room: 0, scale: '' });
      d.style.removeProperty('--eq-scale');
    }
  }
  for (const f of fits) {
    const need = f.width();
    f.room = f.box.clientWidth - 2;
    if (need > f.room + 1 && f.room > 0) f.scale = String(Math.max(MIN_SCALE, Math.floor((f.room / need) * 1000) / 1000));
  }
  let pending = fits.filter((f) => f.scale);
  for (const f of pending) f.d.style.setProperty('--eq-scale', f.scale);
  // Fixed tag padding and font hinting do not shrink in proportion to font size.
  // Measure the fitted result too, retaining the minimum scale and scroll fallback.
  for (let i = 0; i < 3 && pending.length; i++) {
    pending = pending.filter((f) => {
      const fitted = f.width();
      if (fitted <= f.room + 1 || Number(f.scale) <= MIN_SCALE) return false;
      f.scale = String(Math.max(MIN_SCALE, Math.floor((Number(f.scale) * f.room / fitted) * 1000) / 1000));
      return true;
    });
    for (const f of pending) f.d.style.setProperty('--eq-scale', f.scale);
  }
  return new Set(fits.filter((f) => f.scale !== f.old).map((f) => f.root));
}

/** Fit every display equation under ``root`` (or each of several roots). Returns true when a size changed. */
export function fitEquations(root: HTMLElement | HTMLElement[]): boolean {
  return fit(Array.isArray(root) ? root : [root]).size > 0;
}

/** Refit when ``el`` changes width (a pane opens or closes, the window is resized). */
export function refitOnResize(el: HTMLElement, after?: () => void): ResizeObserver {
  let last = el.clientWidth, timer: ReturnType<typeof setTimeout> | undefined;
  const ro = new ResizeObserver(() => {
    if (el.clientWidth === last) return;
    last = el.clientWidth;
    clearTimeout(timer);
    timer = setTimeout(() => { if (fitEquations(el)) after?.(); }, 120);
  });
  ro.observe(el);
  return ro;
}

type Queued = [HTMLElement, (() => void) | undefined];
let frameQueue: Queued[] = [];
let fontQueue: Queued[] = [];

function fitQueued(queue: Queued[]): void {
  const live = queue.filter(([el]) => el.isConnected);
  const changed = fit(live.map(([el]) => el));
  for (const [el, after] of live) if (changed.has(el)) after?.();
}

/** Fit again when KaTeX's fonts have loaded (they change widths), if they are still loading. */
export function fitAfterFonts(el: HTMLElement, after?: () => void): void {
  if (!document.fonts || document.fonts.status === 'loaded') return;
  if (!fontQueue.length) document.fonts.ready.then(() => { const q = fontQueue; fontQueue = []; fitQueued(q); });
  fontQueue.push([el, after]);
}

/** Fit once the element is on screen, and again when KaTeX's fonts have loaded.
 *  Elements queued in the same frame are fitted together. */
export function fitSoon(el: HTMLElement, after?: () => void): void {
  if (!frameQueue.length) requestAnimationFrame(() => { const q = frameQueue; frameQueue = []; fitQueued(q); });
  frameQueue.push([el, after]);
  fitAfterFonts(el, after);
}
