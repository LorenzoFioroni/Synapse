// Display equations wider than the note: shrink them a little (down to MIN_SCALE) before
// falling back to a horizontal scroll bar.
export const MIN_SCALE = 0.75;

/** Fit every display equation under ``root``. Returns true when a size changed. */
export function fitEquations(root: HTMLElement): boolean {
  let changed = false;
  const list = root.matches?.('.katex-display') ? [root] : root.querySelectorAll<HTMLElement>('.katex-display');
  for (const d of list) {
    const box = d.closest<HTMLElement>('.math-block, .math.display, .cm-lp-mathblock, .cm-lp-math') || d.parentElement;
    const html = d.querySelector('.katex-html');
    if (!box || !html) continue;
    const old = d.style.getPropertyValue('--eq-scale');
    d.style.removeProperty('--eq-scale');
    const main = html.querySelector(':scope > .eq-main');
    const tag = html.querySelector<HTMLElement>(':scope > .katex-tag:not(.eq-ghost)');
    const need = main ? main.scrollWidth + (tag ? tag.offsetWidth : 0) : html.scrollWidth;
    const room = box.clientWidth - 2;
    let scale = '';
    if (need > room + 1 && room > 0) scale = String(Math.max(MIN_SCALE, Math.floor((room / need) * 1000) / 1000));
    if (scale) d.style.setProperty('--eq-scale', scale);
    if (scale !== old) changed = true;
  }
  return changed;
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

/** Fit once the element is on screen, and again when KaTeX's fonts have loaded (they change widths). */
export function fitSoon(el: HTMLElement, after?: () => void): void {
  const run = () => { if (el.isConnected && fitEquations(el)) after?.(); };
  requestAnimationFrame(run);
  if (document.fonts && document.fonts.status !== 'loaded') document.fonts.ready.then(run);
}
