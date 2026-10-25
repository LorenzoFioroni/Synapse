// The carousel of a ```gallery block (markdown.ts draws it, gallery.ts reads the block). The panels
// sit side by side in a track that scrolls horizontally with snapping, so a swipe or a trackpad
// moves it without any code; the arrows, the dots and the arrow keys scroll it, and the dots and
// arrows follow whatever moved it. One set of listeners on the document serves every gallery:
// read mode, the live preview and embedded notes alike, however often they are redrawn.

let installed = false;

const trackOf = (g: Element) => g.querySelector<HTMLElement>('.gallery-track');
const panelCount = (track: HTMLElement) => track.querySelectorAll(':scope > .gallery-panel').length;

/** The panel the track shows (the one closest to its left edge). */
export function currentPanel(track: HTMLElement): number {
  const w = track.clientWidth;
  return w ? Math.round(track.scrollLeft / w) : 0;
}

/** Scroll a gallery to its panel ``index`` (clamped). */
export function showPanel(gallery: Element, index: number, smooth = true): void {
  const track = trackOf(gallery);
  if (!track) return;
  const n = panelCount(track);
  const i = Math.max(0, Math.min(n - 1, index));
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  track.scrollTo({ left: i * track.clientWidth, behavior: smooth && !reduce ? 'smooth' : 'auto' });
  sync(gallery, i);
}

/** Mark the dot of panel ``i`` and enable the arrows that lead somewhere. */
function sync(gallery: Element, i: number): void {
  const track = trackOf(gallery);
  if (!track) return;
  const n = panelCount(track);
  gallery.querySelectorAll<HTMLElement>('.gallery-dot').forEach((d) => {
    const on = Number(d.dataset.index) === i;
    d.classList.toggle('active', on);
    if (on) d.setAttribute('aria-current', 'true'); else d.removeAttribute('aria-current');
  });
  const prev = gallery.querySelector<HTMLButtonElement>('.gallery-prev');
  const next = gallery.querySelector<HTMLButtonElement>('.gallery-next');
  if (prev) prev.disabled = i <= 0;
  if (next) next.disabled = i >= n - 1;
}

function onClick(e: MouseEvent): void {
  const t = e.target as Element | null;
  const btn = t?.closest?.<HTMLElement>('.gallery-btn, .gallery-dot');
  const gallery = btn?.closest('.gallery');
  if (!btn || !gallery) return;
  e.preventDefault();
  e.stopPropagation();   // not a click on the note (read mode would go to the line)
  const track = trackOf(gallery);
  if (!track) return;
  if (btn.classList.contains('gallery-dot')) showPanel(gallery, Number(btn.dataset.index) || 0);
  else showPanel(gallery, currentPanel(track) + (btn.classList.contains('gallery-prev') ? -1 : 1));
}

function onKey(e: KeyboardEvent): void {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  const t = e.target as Element | null;
  // only when focus is on the gallery's own controls: elsewhere the arrows belong to the editor
  if (!t?.closest?.('.gallery-btn, .gallery-dot, .gallery-track')) return;
  const gallery = t.closest('.gallery');
  const track = gallery && trackOf(gallery);
  if (!gallery || !track) return;
  e.preventDefault();
  showPanel(gallery, currentPanel(track) + (e.key === 'ArrowLeft' ? -1 : 1));
  gallery.querySelector<HTMLElement>('.gallery-dot.active')?.focus({ preventScroll: true });
}

function onScroll(e: Event): void {
  const t = e.target as Element | null;
  if (!(t instanceof HTMLElement) || !t.classList.contains('gallery-track')) return;
  const gallery = t.closest('.gallery');
  if (gallery) sync(gallery, currentPanel(t));
}

const onControl = (e: Event) => !!(e.target as Element | null)?.closest?.('.gallery-btn, .gallery-dot');
// the editor must not see a press on the arrows as a click into the note (which would open its source)
function onPress(e: MouseEvent): void {
  if (!onControl(e)) return;
  e.stopPropagation();
  // in the editor, keep its focus and cursor; elsewhere the control takes focus (for the arrow keys)
  if ((e.target as Element).closest('.cm-editor')) e.preventDefault();
}
function onTouch(e: TouchEvent): void { if (onControl(e)) e.stopPropagation(); }

/** Install the listeners (once); the function returned takes them away. */
export function installGalleries(): () => void {
  if (installed || typeof document === 'undefined') return () => {};
  installed = true;
  document.addEventListener('click', onClick, true);
  document.addEventListener('mousedown', onPress, true);
  document.addEventListener('touchstart', onTouch, { capture: true, passive: true });
  document.addEventListener('keydown', onKey, true);
  document.addEventListener('scroll', onScroll, true);   // scroll does not bubble: listen while capturing
  return () => {
    installed = false;
    document.removeEventListener('click', onClick, true);
    document.removeEventListener('mousedown', onPress, true);
    document.removeEventListener('touchstart', onTouch, true);
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('scroll', onScroll, true);
  };
}
