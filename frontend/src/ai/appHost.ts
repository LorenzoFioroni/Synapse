// Ask AI in the app: one panel for the whole window, opened from the floating button (launcher.ts),
// the palette or a selection, and kept open across the library, notes and PDF tabs. A PDF tab's
// viewer (a frame) adds to this panel through ``window.zcAskAi`` and tells what it shows through
// ``window.zcViewerHere``. Opening a cited PDF goes through the app's own tabs (api.ts).
import { openAttachment, openNotePdf } from '../api.js';
import type { Attached } from './context.js';
import { mountLauncher } from './launcher.js';
import type { Dock } from './placement.js';
import type { AskHost } from './panel.js';
import { HERE_EVENT } from './status.js';

export const hereChanged = (): void => { window.dispatchEvent(new Event(HERE_EVENT)); };

/** Docked, the panel is a column on the right of the window that every view makes room for (css/ai.css). */
const appDock: Dock = {
  available: () => !document.body.classList.contains('phone') && matchMedia('(min-width: 1101px)').matches,
  mount(el) { document.body.classList.add('ai-docked'); document.body.append(el); },
  unmount() { document.body.classList.remove('ai-docked'); },
};

/** What is on screen, asked of each source in turn; the first that answers (not null) wins. */
const sources: (() => Attached[] | null)[] = [];
export const addHereSource = (fn: () => Attached[] | null): void => { sources.push(fn); };

const host: AskHost = {
  dock: appDock,
  openPdf: (item, pdf, page) => openAttachment(item, pdf, page),
  showItem: (id) => { location.hash = `item=${id}`; },
  openNotePdf: (pdf, page, quote) => openNotePdf(pdf, page, quote ? { q: quote } : {}),
  openNote: (path) => { location.hash = `note=${encodeURIComponent(path)}`; },   // main.ts openFromHash
  here() {
    for (const s of sources) {
      try { const r = s(); if (r) return r; } catch { /* a closing viewer frame */ }
    }
    return [];
  },
  watch(fn) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const soon = () => { clearTimeout(timer); timer = setTimeout(fn, 120); };
    document.addEventListener('selectionchange', soon);
    window.addEventListener(HERE_EVENT, soon);
    return () => { clearTimeout(timer); document.removeEventListener('selectionchange', soon); window.removeEventListener(HERE_EVENT, soon); };
  },
};

/** Open the panel (loaded on first use), with ``add`` added to the next question. */
export async function askAi(add?: Attached): Promise<void> {
  const { openAskAi } = await import('./panel.js');
  openAskAi(host, add);
}

/** The floating button, and the entry point for viewer frames. */
export function initAskAi(): void {
  window.zcAskAi = (add) => void askAi(add);
  mountLauncher(() => void askAi());
}
