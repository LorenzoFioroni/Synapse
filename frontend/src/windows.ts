// Opening a place of the app in a new window. Each window has its own tabs (tabs/tabs.ts), so the
// new one starts with only the Synapse tab, plus the PDF it was asked for. The place is named in
// the new window's address after '#' and read once by main.ts (`openFromHash`).
//
//   #item=<id>                     the library with that item selected (also the browser extension's
//                                  "Open in Library")
//   #view=folder:<id> | smart:<id> a folder or smart folder of the library
//   #note=<path>&line=<n>          a note in Notes
//   #task=<id>                     the Tasks page with that task selected
//   #pdf=<viewer address>          a PDF in a tab (with any of the above for the Synapse tab)
//   #window=new                    added to all of these: start without the opener's tabs

/** A place to open, read from or written into a window's address. */
export interface Place {
  item?: string;
  view?: string;
  note?: string;
  line?: number | null;
  task?: string;
  pdf?: string;
  /** Opened as a new window: its tabs start empty. */
  fresh?: boolean;
}

const VIEW = /^(folder|smart):\d+$/;
const VIEWER = '/viewer.html?';

/** The '#…' part of the address of a window that opens at `p`. */
export function placeHash(p: Place): string {
  const q = new URLSearchParams();
  if (p.item) q.set('item', p.item);
  if (p.view && VIEW.test(p.view)) q.set('view', p.view);
  if (p.note) q.set('note', p.note);
  if (p.note && p.line) q.set('line', String(p.line));
  if (p.task) q.set('task', p.task);
  if (p.pdf && p.pdf.startsWith(VIEWER)) q.set('pdf', p.pdf);
  q.set('window', 'new');
  return `#${q}`;
}

/** The place an address's '#…' names, or null when it names none. Anything that does not look
 *  like a place (a view that is no folder, a PDF that is not the viewer) is left out. */
export function readPlace(hash: string): Place | null {
  const q = new URLSearchParams(hash.replace(/^#/, ''));
  const p: Place = {};
  const item = q.get('item'), view = q.get('view'), note = q.get('note'), task = q.get('task'), pdf = q.get('pdf');
  if (item) p.item = item;
  if (view && VIEW.test(view)) p.view = view;
  if (note) {
    p.note = note;
    const line = parseInt(q.get('line') || '', 10);
    if (line >= 1) p.line = line;
  }
  if (task) p.task = task;
  if (pdf && pdf.startsWith(VIEWER)) p.pdf = pdf;
  if (q.get('window') === 'new') p.fresh = true;
  return Object.keys(p).length ? p : null;
}

/** The size and place of a new window: like this one, a little further down and right. */
function features(): string {
  const w = Math.max(800, window.outerWidth || 1280), h = Math.max(600, window.outerHeight || 800);
  const left = (window.screenX || 0) + 32, top = (window.screenY || 0) + 32;
  // `popup` asks for a window rather than a browser tab; `noopener` keeps it apart from this one
  // (its own tabs, and nothing copied from this window's session storage).
  return `popup,noopener,width=${w},height=${h},left=${left},top=${top}`;
}

/** Open the app at `p` in a new window. Call it inside a click: browsers block windows opened
 *  later. */
export function openNewWindow(p: Place = {}): void {
  window.open(`${location.origin}/${placeHash(p)}`, '_blank', features());
}

/** Open a page of its own (the viewer, when PDFs open in browser tabs) in a new window. */
export function openPageInNewWindow(url: string): void {
  window.open(url, '_blank', features());
}
