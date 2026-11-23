// Which pointer is in use: mouse, Apple Pencil or finger. Shared by the tools (input.ts) and
// highlighting text (textMarks.ts). See annotations.ts for the overview.
import { tool } from './model.js';

/* Mouse, Apple Pencil and fingers all arrive as pointer events.
   - Once a pen has been used (remembered), fingers scroll and pinch while the pen draws, highlights
     and erases; before that (a phone, an iPad without a Pencil) fingers draw and highlight too.
   - Safari scrolls the page under the Pencil unless its touches are cancelled; touchstart/touchmove
     of a stylus are cancelled while an annotation tool is chosen (see stopPenScroll). */
let penSeen = false;
try { penSeen = localStorage.getItem('zc.penSeen') === '1'; } catch { /* private mode */ }
document.body.classList.toggle('zc-pen', penSeen);
function notePen(e: PointerEvent): void {
  if (e.pointerType !== 'pen' || penSeen) return;
  penSeen = true;
  document.body.classList.add('zc-pen');
  try { localStorage.setItem('zc.penSeen', '1'); } catch { /* private mode */ }
}
/** May this pointer draw? Mouse and pen always; a finger only while no pen has been seen. */
export const draws = (e: PointerEvent) => e.pointerType !== 'touch' || !penSeen;
/** The kind of the pointer that last went down: 'mouse', 'pen' or 'touch'. */
export let lastPointer = 'mouse';
document.addEventListener('pointerdown', (e) => { lastPointer = e.pointerType || 'mouse'; notePen(e); }, true);

function stopPenScroll(e: TouchEvent): void {
  if (tool() === 'select') return;
  // before any Pencil, one finger on the text highlights it (textMarks.ts): no scroll, no long-press selection
  if (!penSeen && (tool() === 'highlight' || tool() === 'underline') && e.touches.length === 1
      && (e.target as Element).closest?.('.textLayer span')) { e.preventDefault(); return; }
  // touchType is Safari's (not in the standard DOM types)
  if (![...e.changedTouches].some((t) => (t as Touch & { touchType?: string }).touchType === 'stylus')) return;
  if (!(e.target as Element).closest?.('.page-wrapper')) return;
  // a tap with the text tool must stay a tap (it opens the keyboard); everything else is drawn
  if (tool() === 'text' && e.type === 'touchstart') return;
  e.preventDefault();
}
/* A non-passive touch listener makes Safari wait for the page's script before every scroll, and
   that script is often busy drawing pages: scrolling stutters. So listen only while a tool that
   may cancel touches is chosen; with Select, scrolling never waits. */
let listening = false;
function syncPenScroll(): void {
  const want = tool() !== 'select';
  if (want === listening) return;
  listening = want;
  for (const type of ['touchstart', 'touchmove'] as const) {
    if (want) document.addEventListener(type, stopPenScroll, { passive: false });
    else document.removeEventListener(type, stopPenScroll);
  }
}
syncPenScroll();
document.addEventListener('zc:toolchange', syncPenScroll);
