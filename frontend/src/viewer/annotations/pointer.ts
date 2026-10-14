// Which pointer is in use: mouse, Apple Pencil or finger. Shared by the tools (input.ts) and
// highlighting text (textMarks.ts). See annotations.ts for the overview.
import { tool } from './model.js';

/* Mouse, Apple Pencil and fingers all arrive as pointer events.
   - Once a pen has been used (remembered), fingers scroll and pinch while the pen draws, highlights
     and erases; before that (a phone, an iPad without a Pencil) fingers draw too.
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
  // touchType is Safari's (not in the standard DOM types)
  if (![...e.changedTouches].some((t) => (t as Touch & { touchType?: string }).touchType === 'stylus')) return;
  if (!(e.target as Element).closest?.('.page-wrapper')) return;
  // a tap with the text tool must stay a tap (it opens the keyboard); everything else is drawn
  if (tool() === 'text' && e.type === 'touchstart') return;
  e.preventDefault();
}
document.addEventListener('touchstart', stopPenScroll, { passive: false });
document.addEventListener('touchmove', stopPenScroll, { passive: false });
