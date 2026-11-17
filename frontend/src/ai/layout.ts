// Where the Ask AI parts sit and how big they are: the floating button's corner, the popup's size
// and the docked column's width, remembered per device (prefs.ts). The arithmetic lives here, apart
// from the DOM, so it can be tested: snapping a dragged button to the nearest corner, keeping a
// size inside the window, and the sizes a drag of the popup's inner corner or the column's inner edge gives.
import { load, save } from './prefs.js';

export type Corner = 'tl' | 'tr' | 'bl' | 'br';
export interface Size { w: number; h: number }
export interface Viewport { w: number; h: number }

export const CORNERS: Corner[] = ['tl', 'tr', 'bl', 'br'];
export const DEFAULT_CORNER: Corner = 'br';
export const DEFAULT_POPUP: Size = { w: 390, h: 580 };
export const MIN_POPUP: Size = { w: 300, h: 320 };
export const DEFAULT_DOCK = 360;
export const MIN_DOCK = 280;
export const MAX_DOCK = 640;
/** The popup leaves this much of the window around it (also what it is clamped to). */
export const POPUP_MARGIN = 12;
/** Pixels a press must travel before it is a drag and not a click. */
export const DRAG_THRESHOLD = 6;

const KEY_CORNER = 'zc.ai.fab';
const KEY_POPUP = 'zc.ai.popupSize';
const KEY_DOCK = 'zc.ai.dockWidth';
export const CORNER_EVENT = 'zc:ai-corner';

const num = (v: unknown, fallback: number): number => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);

/** The corner of the window nearest to a point (a dragged button's centre). */
export function nearestCorner(x: number, y: number, view: Viewport): Corner {
  return `${y < view.h / 2 ? 't' : 'b'}${x < view.w / 2 ? 'l' : 'r'}` as Corner;
}

/** The corner a key press moves to from ``from`` (an arrow key; others leave it): one step along that axis, never past the edge. */
export function cornerAfterKey(from: Corner, key: string): Corner {
  const v = from[0], h = from[1];
  if (key === 'ArrowLeft') return `${v}l` as Corner;
  if (key === 'ArrowRight') return `${v}r` as Corner;
  if (key === 'ArrowUp') return `t${h}` as Corner;
  if (key === 'ArrowDown') return `b${h}` as Corner;
  return from;
}

/** The corner of the popup that faces the middle of the window: where its resize grip is (the opposite of the anchor). */
export function innerCorner(anchor: Corner): Corner {
  return `${anchor[0] === 't' ? 'b' : 't'}${anchor[1] === 'l' ? 'r' : 'l'}` as Corner;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi));

/** ``size`` kept between the minimum and the room the window leaves (the minimum wins in a tiny window). */
export function clampPopup(size: Size, view: Viewport, margin = POPUP_MARGIN, min: Size = MIN_POPUP): Size {
  return { w: Math.round(clamp(size.w, min.w, view.w - 2 * margin)), h: Math.round(clamp(size.h, min.h, view.h - 2 * margin)) };
}

/** The popup's size after dragging its inner corner by (dx, dy): it grows as the corner moves away from its anchor. */
export function resizedPopup(start: Size, anchor: Corner, dx: number, dy: number, view: Viewport, margin = POPUP_MARGIN): Size {
  const sx = anchor[1] === 'r' ? -1 : 1, sy = anchor[0] === 'b' ? -1 : 1;
  return clampPopup({ w: start.w + sx * dx, h: start.h + sy * dy }, view, margin);
}

/** The widest the docked column may be in a window: not more than MAX_DOCK, nor most of the window. */
export const maxDock = (view: Viewport): number => Math.max(MIN_DOCK, Math.min(MAX_DOCK, Math.round(view.w * 0.6)));
export const clampDock = (w: number, view: Viewport): number => Math.round(clamp(w, MIN_DOCK, maxDock(view)));

/** The column's width after dragging its inner edge by ``dx``: a column on the right grows as the edge moves left. */
export const resizedDock = (start: number, side: 'left' | 'right', dx: number, view: Viewport): number =>
  clampDock(start + (side === 'right' ? -dx : dx), view);

/* ------------------------------------------------------------------ remembered per device */
export function savedCorner(): Corner {
  const c = load<string>(KEY_CORNER, DEFAULT_CORNER);
  return (CORNERS as string[]).includes(c) ? (c as Corner) : DEFAULT_CORNER;
}
export function saveCorner(c: Corner): void {
  save(KEY_CORNER, c);
  window.dispatchEvent(new CustomEvent(CORNER_EVENT, { detail: c }));
}

export function savedPopup(): Size {
  const s = load<Partial<Size> | null>(KEY_POPUP, null);
  return { w: num(s?.w, DEFAULT_POPUP.w), h: num(s?.h, DEFAULT_POPUP.h) };
}
export const savePopup = (s: Size): void => save(KEY_POPUP, s);

export const savedDock = (): number => num(load<number | null>(KEY_DOCK, null), DEFAULT_DOCK);
export const saveDock = (w: number): void => save(KEY_DOCK, w);
