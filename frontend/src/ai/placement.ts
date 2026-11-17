// Where the panel sits: a small floating popup in the lower right corner, or docked in a column of
// the page (in the app a column on the right of the window, beside every view and PDF tab; in the
// PDF viewer of its own a column the PDF makes room for). Docking moves the same element, so a conversation in progress is untouched. The choice is
// remembered per device; where docking has no room (a narrow window, the phone) it is a popup.
import { load, save } from './prefs.js';

export type Placement = 'popup' | 'dock';

/** The page's own column for the panel. */
export interface Dock {
  /** Is there room for a column now? */
  available(): boolean;
  mount(el: HTMLElement): void;
  unmount(el: HTMLElement): void;
}

const KEY = 'zc.ai.placement';

export const wanted = (): Placement => (load<string>(KEY, 'popup') === 'dock' ? 'dock' : 'popup');
export const remember = (p: Placement): void => save(KEY, p);

/** Put ``el`` where the preference says and the page allows; returns what it ended up as. */
export function place(el: HTMLElement, dock: Dock | undefined): Placement {
  const target: Placement = wanted() === 'dock' && dock?.available() ? 'dock' : 'popup';
  if (el.dataset.placement === target && el.isConnected) return target;
  if (el.dataset.placement === 'dock') dock?.unmount(el);
  el.dataset.placement = target;
  if (target === 'dock') dock!.mount(el);
  else document.body.appendChild(el);
  return target;
}
