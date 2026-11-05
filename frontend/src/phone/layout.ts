// Which layout this page uses: the phone shell (frontend/src/phone/) or the desktop one. Phones
// get their own shell; tablets and computers keep the desktop layout. The choice is made once,
// when the page loads, so a window that changes size never swaps shells under the reader.
// A device can override it (stored in its browser): "Use the desktop layout" on a phone, "Use the
// phone layout" from the command palette on a touch screen, or ?layout=phone|desktop in the URL.

export type Layout = 'phone' | 'desktop';

const KEY = 'zc.layout';

/** A small touch screen: a phone upright (narrow) or on its side (short). A mouse never counts, so
 *  a narrow browser window on a computer keeps the desktop layout. */
export const PHONE_QUERY = '(pointer: coarse) and (max-width: 600px), (pointer: coarse) and (max-height: 500px)';

/** The layout this device was told to use, or null (decided by the screen). */
export function chosenLayout(): Layout | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'phone' || v === 'desktop' ? v : null;
  } catch { return null; }
}

/** Whether the screen alone would pick the phone layout. */
export function phoneScreen(): boolean {
  try { return matchMedia(PHONE_QUERY).matches; } catch { return false; }
}

/** The layout for this page load (reading ?layout= once and remembering it). */
export function pageLayout(): Layout {
  try {
    const asked = new URLSearchParams(location.search).get('layout');
    if (asked === 'phone' || asked === 'desktop' || asked === 'auto') {
      if (asked === 'auto') localStorage.removeItem(KEY); else localStorage.setItem(KEY, asked);
      const url = new URL(location.href);
      url.searchParams.delete('layout');
      history.replaceState(history.state, '', url.toString());
    }
  } catch { /* storage unavailable: the screen decides */ }
  return chosenLayout() ?? (phoneScreen() ? 'phone' : 'desktop');
}

/** Use ``layout`` on this device from now on (null: let the screen decide) and reload into it. */
export function switchLayout(layout: Layout | null): void {
  try {
    if (layout && layout !== (phoneScreen() ? 'phone' : 'desktop')) localStorage.setItem(KEY, layout);
    else localStorage.removeItem(KEY);
  } catch { /* storage unavailable */ }
  location.reload();
}
