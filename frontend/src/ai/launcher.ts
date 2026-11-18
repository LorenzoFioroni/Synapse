// The floating Ask AI button: shown while Ask AI is set up (status.ts) and the panel is closed. The
// app has one for the whole window; the PDF viewer in a browser tab of its own has its own. It sits
// in one of the four corners of the window (the lower right to start with) and can be moved: drag it
// with a mouse, a pen or a finger (a press that hardly moves is still a click) and it settles into the
// nearest corner; with the keyboard, the arrow keys move it while it has the focus. The corner is
// remembered per device and the panel opens from it (layout.ts, css/ai.css).
import { icon } from '../icons.js';
import { type Corner, DRAG_THRESHOLD, cornerAfterKey, nearestCorner, saveCorner, savedCorner } from './layout.js';
import { PANEL_EVENT, aiConfigured, onAiStatus, refreshAiStatus } from './status.js';

const CORNER_NAMES: Record<Corner, string> = { tl: 'upper left', tr: 'upper right', bl: 'lower left', br: 'lower right' };
const reducedMotion = (): boolean => matchMedia('(prefers-reduced-motion: reduce)').matches;

export function mountLauncher(open: () => void): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'ai-fab';
  b.setAttribute('aria-label', 'Ask AI');
  b.title = 'Ask AI (drag, or use the arrow keys, to move this button)';
  b.dataset.corner = savedCorner();
  b.innerHTML = icon('sparkle');
  const live = document.createElement('span');   // says where the keys moved it
  live.className = 'ai-sr';
  live.setAttribute('aria-live', 'polite');
  let panelOpen = false;
  const paint = () => { b.hidden = panelOpen || !aiConfigured(); };

  /** Rest in ``corner``: its place comes from the CSS of that corner, so margins and the phone's safe areas apply. */
  const setCorner = (corner: Corner, remember = true): void => {
    b.dataset.corner = corner;
    if (remember) saveCorner(corner);
  };

  /* ---- drag: pointer events cover mouse, pen and touch */
  let press: { id: number; x: number; y: number; dx: number; dy: number; moving: boolean } | null = null;
  let swallowClick = false;
  const clearInline = () => { for (const p of ['left', 'top', 'right', 'bottom', 'transition']) b.style.removeProperty(p); };

  b.addEventListener('pointerdown', (e) => {
    if (e.button > 0 || press) return;
    const r = b.getBoundingClientRect();
    press = { id: e.pointerId, x: e.clientX, y: e.clientY, dx: e.clientX - r.left, dy: e.clientY - r.top, moving: false };
    b.setPointerCapture?.(e.pointerId);
  });
  b.addEventListener('pointermove', (e) => {
    if (!press || e.pointerId !== press.id) return;
    if (!press.moving) {
      if (Math.hypot(e.clientX - press.x, e.clientY - press.y) < DRAG_THRESHOLD) return;
      press.moving = true;
      b.classList.add('dragging');
    }
    const w = b.offsetWidth, h = b.offsetHeight;
    const left = Math.min(Math.max(e.clientX - press.dx, 0), innerWidth - w);
    const top = Math.min(Math.max(e.clientY - press.dy, 0), innerHeight - h);
    Object.assign(b.style, { left: `${left}px`, top: `${top}px`, right: 'auto', bottom: 'auto', transition: 'none' });
    e.preventDefault();
  });
  const release = (e: PointerEvent) => {
    if (!press || e.pointerId !== press.id) return;
    const moved = press.moving;
    press = null;
    b.releasePointerCapture?.(e.pointerId);
    if (!moved) return;
    swallowClick = true;
    setTimeout(() => { swallowClick = false; }, 0);
    b.classList.remove('dragging');
    if (e.type === 'pointercancel') { setCorner(savedCorner(), false); clearInline(); return; }
    const from = b.getBoundingClientRect();
    const corner = nearestCorner(from.left + from.width / 2, from.top + from.height / 2, { w: innerWidth, h: innerHeight });
    // where that corner puts it: read from the CSS, with the drag's inline position out of the way
    const dragged = { left: b.style.left, top: b.style.top };
    clearInline();
    setCorner(corner);
    const to = b.getBoundingClientRect();
    if (reducedMotion() || (Math.abs(to.left - from.left) < 1 && Math.abs(to.top - from.top) < 1)) return;
    // slide from where it was let go to the corner, then hand its place back to the CSS
    Object.assign(b.style, { left: dragged.left, top: dragged.top, right: 'auto', bottom: 'auto', transition: 'none' });
    void b.offsetWidth;
    b.style.transition = 'left .22s var(--ease), top .22s var(--ease)';
    b.style.left = `${to.left}px`;
    b.style.top = `${to.top}px`;
    let done = false;
    const end = () => { if (done) return; done = true; clearInline(); };
    b.addEventListener('transitionend', end, { once: true });
    setTimeout(end, 400);
  };
  b.addEventListener('pointerup', release);
  b.addEventListener('pointercancel', release);
  b.addEventListener('click', (e) => { if (swallowClick) { e.preventDefault(); e.stopImmediatePropagation(); return; } open(); });

  /* ---- keyboard: the arrow keys move it between the corners */
  b.addEventListener('keydown', (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (!e.key.startsWith('Arrow')) return;
    const now = b.dataset.corner as Corner;
    const next = cornerAfterKey(now, e.key);
    e.preventDefault();
    if (next === now) return;
    setCorner(next);
    live.textContent = `Ask AI button moved to the ${CORNER_NAMES[next]}`;
  });

  window.addEventListener(PANEL_EVENT, (e) => { panelOpen = !!(e as CustomEvent<boolean>).detail; paint(); });
  onAiStatus(paint);
  paint();
  document.body.append(b, live);
  void refreshAiStatus(60_000).then(paint);
  return b;
}
