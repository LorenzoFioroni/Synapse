// The viewer inside one of the app's tabs (tabs/tabs.ts) instead of a browser tab of its own.
// The page around it is the same app, on the same origin, so the two call each other directly.
import type { ZcTabs } from '../globals.js';

/** The app's tabs around this viewer, or null in a browser tab of its own. */
export function appTabs(): ZcTabs | null {
  try {
    return window.parent !== window ? window.parent.zcTabs || null : null;
  } catch {
    return null;   // framed by another site
  }
}

export const inAppTab = (): boolean => !!appTabs();

const tabs = appTabs();
if (tabs) {
  // The tab keys (next tab, close tab…) work while the PDF has the keyboard too.
  document.addEventListener('keydown', (e) => {
    if (tabs.shortcut(e)) { e.preventDefault(); e.stopPropagation(); }
  }, true);
  // A click in the PDF closes a menu the page around has open.
  document.addEventListener('pointerdown', () => tabs.touched(), true);
}
