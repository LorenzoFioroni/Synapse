// The colour of the window's title bar (the page's theme-color, which Chrome and Edge use for the
// app window, and Safari for its tab bar): the colour of the shown note's folder, else neutral —
// the tab bar's own background, so the title bar and the tab bar look like one strip.

let wanted: string | null = null;

function neutral(): string {
  return getComputedStyle(document.documentElement).getPropertyValue('--bg-sidebar').trim() || '#f1f3f6';
}

/** One theme-color, set from here: index.html has one per system theme for the first paint, but
 *  the app's own theme setting may differ from the system's. */
let meta: HTMLMetaElement | null = null;

function paint(): void {
  if (!meta) {
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.remove());
    meta = document.createElement('meta');
    meta.name = 'theme-color';
    document.head.appendChild(meta);
  }
  meta.content = wanted || neutral();
}

/** The title bar in ``color`` (#rrggbb), or neutral (null). */
export function setWindowColor(color: string | null): void {
  if (color === wanted && meta) return;
  wanted = color;
  paint();
}

// Neutral follows the theme: light, dark, or the system's.
new MutationObserver(paint).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', paint);
