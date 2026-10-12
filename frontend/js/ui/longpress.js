// Long press on touch screens (issue #34). A tablet has no right-click and no hover, so holding a
// finger (or the Pencil) still for half a second stands in for both:
//   1. a cancelable `zc:longpress` event is sent to the element first (detail: {x, y}); hover
//      cards and link previews take it (preventDefault) and open;
//   2. otherwise a `contextmenu` event is sent, which opens the same menu a right-click does.
// The click that follows the press is swallowed. Browsers that open a context menu on a long press
// themselves (Chrome on Android) are left alone: their own event arrives first.

const DELAY = 500;
const SLOP = 10;   // px a finger may move and still be "still"

let press = null;          // { id, x, y, target, timer, native }
let swallowUntil = 0;
let swallowDblUntil = 0;  // the swallowed click still counts towards a double click: don't let it make one
let held = null;           // pointer id of a press that fired and has not been lifted yet

function cancel() {
  if (press) clearTimeout(press.timer);
  press = null;
}

function fire() {
  const p = press;
  press = null;
  if (!p || p.native || !p.target.isConnected) return;
  held = p.id;
  swallowUntil = Infinity;   // until the finger is lifted (and a moment after)
  const lp = new CustomEvent('zc:longpress', { bubbles: true, cancelable: true, detail: { x: p.x, y: p.y } });
  if (!p.target.dispatchEvent(lp)) return;
  p.target.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: p.x, clientY: p.y, button: 2 }));
}

export function installLongPress(root = document) {
  if (root.zcLongPress) return;
  root.zcLongPress = true;
  root.addEventListener('pointerdown', (e) => {
    cancel();
    if (e.pointerType !== 'touch' && e.pointerType !== 'pen') return;
    // text fields keep the system's own long press (select, paste, Scribble) — except citations,
    // whose card opens on a long press
    if (e.target.closest?.('input, textarea, [contenteditable="true"], .cm-editor, .anno-layer, .zc-snap-layer')
        && !e.target.closest?.('.cite, [data-cite-keys], .cm-lp-citesrc')) return;
    press = { id: e.pointerId, x: e.clientX, y: e.clientY, target: e.target, native: false };
    press.timer = setTimeout(fire, DELAY);
  }, true);
  root.addEventListener('pointermove', (e) => {
    if (press && e.pointerId === press.id && Math.hypot(e.clientX - press.x, e.clientY - press.y) > SLOP) cancel();
  }, true);
  for (const t of ['pointerup', 'pointercancel']) {
    root.addEventListener(t, (e) => {
      if (press && e.pointerId === press.id) cancel();
      if (held === e.pointerId) { held = null; swallowUntil = performance.now() + 400; swallowDblUntil = performance.now() + 1000; }
    }, true);
  }
  root.addEventListener('scroll', cancel, true);
  root.addEventListener('contextmenu', (e) => {
    if (!e.isTrusted) return;
    if (press) { press.native = true; return; }                  // the browser does it itself
    if (performance.now() < swallowUntil) { e.preventDefault(); e.stopImmediatePropagation(); }   // ours already opened
  }, true);
  root.addEventListener('click', (e) => {
    if (performance.now() < swallowUntil) {
      e.preventDefault(); e.stopImmediatePropagation(); swallowUntil = 0;
      swallowDblUntil = performance.now() + 1000;
    }
  }, true);
  root.addEventListener('dblclick', (e) => {
    if (performance.now() < swallowDblUntil) { e.preventDefault(); e.stopImmediatePropagation(); swallowDblUntil = 0; }
  }, true);
}
