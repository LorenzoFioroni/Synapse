// PDF viewer: clickable links, a preview of the target on hover, and Back/Forward between the
// places you jumped to.
//
// Loaded by viewer.html as a separate module, like notesBridge.ts. The viewer exposes
// window.zcViewer ({pdfDoc, scale, container}) and fires 'zc:pagecreated' when it lays out a page.
//
// - Internal links (citations, figures, sections…) jump inside the PDF; external links open in a
//   new tab. Links are only live with the Select tool, so highlighting over them still works.
// - Hovering a link shows a popup: for internal links the page it points to, scrolled to the
//   destination and centred on its column (rendered on demand, like Zotero; scroll inside it to
//   look around), for external ones the address. Links to figures and tables point at their
//   caption, so when the destination is a caption (found in the page text: "FIG. 3", "Table 2"…,
//   or a LaTeX name like figure.3) the popup is taller and ends with the caption, the figure above it.
// - Every jump is a browser history entry holding the reading position, so Back (toolbar arrow,
//   Alt+←, Cmd+[, the mouse's back button) returns to where you were before following the link.
//   Inside one of the app's tabs the jumps are kept in memory instead (jumpHistory.ts), with the
//   same keys and buttons.

import type { PageViewport, PDFPageProxy, RenderTask } from 'pdfjs-dist';
import type { TextItem } from 'pdfjs-dist/types/src/display/api.js';
import { installLongPress } from '../ui/longpress.js';
import { inAppTab } from './inTab.js';
import { JumpHistory } from './jumpHistory.js';
import {
  type Box, type PageText, type ReadingPosition, destTarget, figureRegion, findCaption, fitRegion, graphicsBoxes, isFloatDest,
  popupPlacement, positionAt, previewOrigin, rectPercent, scrollFor,
} from './linkNav.js';
import type { PageWrapper } from './viewer.js';

/** What a link in the PDF points to: a destination in it, a web address, or a page action. */
interface LinkTarget { dest?: unknown; url?: string; action?: string }
/** A destination resolved to a page, with x/y in page CSS pixels at scale 1 (null = free). */
interface Resolved { pageNum: number; x: number | null; y: number | null; page?: PDFPageProxy; vp?: PageViewport }
type LinkEl = HTMLAnchorElement & { zcTarget: LinkTarget };
/** A reading position, plus the horizontal scroll when the page is wider than the view. */
type Position = ReadingPosition & { sx?: number };
interface PageBox { num: number; top: number; height: number; el: HTMLElement }

/** pdf.js 3.4 types its point conversions as Object; they return [x, y]. */
const xy = (p: object) => p as [number, number];

const HOVER_DELAY = 250;
const PREVIEW_W = 460, PREVIEW_H = 260, PREVIEW_H_FLOAT = 360, PREVIEW_SCALE = 1.35;

const css = document.createElement('style');
css.textContent = `
  .zc-links { position: absolute; inset: 0; z-index: 4; pointer-events: none; }
  .zc-link { position: absolute; pointer-events: auto; cursor: pointer; border-radius: 2px; }
  .zc-link:hover { background: rgba(47,111,237,.12); }
  body[data-zc-tool]:not([data-zc-tool="select"]) .zc-link,
  .zc-snap-on .zc-link { pointer-events: none; }
  .zc-link-pop { position: fixed; z-index: 2500; background: var(--bg-elev, #fff); color: var(--text, #222);
    border: 1px solid var(--border, #ddd); border-radius: 8px; box-shadow: 0 8px 28px rgba(0,0,0,.22);
    font: 12px -apple-system, system-ui, sans-serif; overflow: hidden; }
  .zc-link-pop.preview { cursor: pointer; }
  .zc-link-pop canvas { display: block; background: #fff; }
  .zc-link-pop .zc-pop-page { overflow: scroll; overscroll-behavior: contain; scrollbar-width: thin; }
  .zc-link-pop .zc-pop-page::-webkit-scrollbar { width: 8px; height: 8px; }
  .zc-link-pop .zc-pop-page::-webkit-scrollbar-thumb { background: rgba(0,0,0,.25); border-radius: 4px; }
  .zc-pop-zoom { display: inline-flex; align-items: center; gap: 4px; }
  .zc-pop-zoom button { border: 1px solid var(--border, #ddd); background: var(--bg, #fff); color: var(--text-2, #555); border-radius: 4px;
    width: 18px; height: 16px; line-height: 12px; padding: 0; cursor: pointer; font: inherit; }
  .zc-pop-zoom button:hover { background: var(--bg-hover, #eef); }
  .zc-pop-pct { min-width: 34px; text-align: center; font-variant-numeric: tabular-nums; }
  .zc-link-pop .zc-pop-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 4px 9px;
    color: var(--text-3, #888); border-bottom: 1px solid var(--border, #eee); font-size: 11px; }
  .zc-link-pop .zc-pop-url { padding: 6px 10px; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #zc-nav { display: flex; gap: 2px; margin-right: 6px; }
  #zc-nav .icon-btn:disabled { opacity: .35; cursor: default; background: transparent; }
`;
document.head.appendChild(css);

const viewer = () => window.zcViewer;
const container = document.getElementById('pdf-container')!;

/* ---------------------------------------------------------------- link layer */
async function addLinks(wrapper: HTMLElement, page: PDFPageProxy): Promise<void> {
  if (wrapper.querySelector('.zc-links')) return;
  const layer = document.createElement('div');
  layer.className = 'zc-links';
  wrapper.appendChild(layer);
  let annots: any[];   // pdf.js annotation data (link annotations: rect, dest, url, action)
  try { annots = await page.getAnnotations({ intent: 'display' }); } catch { return; }
  const vp = page.getViewport({ scale: 1 });
  for (const a of annots) {
    if (a.subtype !== 'Link' || !a.rect) continue;
    const target = linkTarget(a);
    if (!target) continue;
    const el = document.createElement('a') as LinkEl;
    el.className = 'zc-link';
    Object.assign(el.style, rectPercent(vp.convertToViewportRectangle(a.rect), vp.width, vp.height));
    if (target.url) { el.href = target.url; el.target = '_blank'; el.rel = 'noopener noreferrer'; }
    el.zcTarget = target;
    layer.appendChild(el);
  }
}

function linkTarget(a: { dest?: unknown; url?: string; action?: string }): LinkTarget | null {
  if (a.dest) return { dest: a.dest };
  if (a.url) return { url: a.url };
  if (a.action && ['NextPage', 'PrevPage', 'FirstPage', 'LastPage'].includes(a.action)) return { action: a.action };
  return null;
}

/** {pageNum, x, y} for a link target, with x/y in page CSS pixels at scale 1 (null = free). */
const resolved = new Map<string, Resolved | null>();
async function resolveTarget(target: LinkTarget): Promise<Resolved | null> {
  const doc = viewer()?.pdfDoc;
  if (!doc) return null;
  if (target.action) {
    const cur = currentPosition()?.page || 1;
    const n = ({ NextPage: cur + 1, PrevPage: cur - 1, FirstPage: 1, LastPage: doc.numPages } as Record<string, number>)[target.action];
    return n >= 1 && n <= doc.numPages ? { pageNum: n, x: null, y: null } : null;
  }
  const key = JSON.stringify(target.dest);
  if (resolved.has(key)) return resolved.get(key)!;
  let out: Resolved | null = null;
  try {
    const explicit = typeof target.dest === 'string' ? await doc.getDestination(target.dest) : target.dest;
    const t = destTarget(explicit);
    if (t) {
      const index = typeof t.ref === 'object' && t.ref ? await doc.getPageIndex(t.ref as Parameters<typeof doc.getPageIndex>[0])
        : Number.isInteger(t.ref) ? t.ref as number : null;
      if (index != null && index >= 0 && index < doc.numPages) {
        const page = await doc.getPage(index + 1);
        const vp = page.getViewport({ scale: 1 });
        // a free coordinate is converted as the page's top-left corner, then reported as null
        const [cx, cy] = xy(vp.convertToViewportPoint(t.x ?? page.view[0], t.y ?? page.view[3]));
        out = { pageNum: index + 1, x: t.x == null ? null : cx, y: t.y == null ? null : cy, page, vp };
      }
    }
  } catch (e) { console.warn('Could not resolve link destination', e); }
  resolved.set(key, out);
  return out;
}

/* ------------------------------------------------------------------ positions */
function pageBoxes(): PageBox[] {
  const cTop = container.getBoundingClientRect().top - container.scrollTop + container.clientTop;
  return [...container.querySelectorAll<HTMLElement>('.page-wrapper')].map((w) => {
    const r = w.getBoundingClientRect();
    return { num: Number(w.dataset.pageNumber), top: r.top - cTop, height: r.height, el: w };
  }).sort((a, b) => a.num - b.num);
}

function currentPosition(): Position | null {
  const pos: Position | null = positionAt(pageBoxes(), container.scrollTop);
  if (pos && container.scrollWidth > container.clientWidth) pos.sx = Math.round(1000 * container.scrollLeft / container.scrollWidth) / 1000;
  return pos;
}

/** Wait (up to 20 s) for a page to be laid out — the viewer creates them one after another. */
function waitForPage(num: number): Promise<boolean> {
  const has = () => container.querySelector(`.page-wrapper[data-page-number="${num}"]`);
  if (has()) return Promise.resolve(true);
  return new Promise((resolve) => {
    const obs = new MutationObserver(() => { if (has()) { obs.disconnect(); resolve(true); } });
    obs.observe(container, { childList: true });
    setTimeout(() => { obs.disconnect(); resolve(!!has()); }, 20000);
  });
}

async function scrollToPosition(pos: Position | null | undefined): Promise<void> {
  if (!pos?.page || !(await waitForPage(pos.page))) return;
  const top = scrollFor(pageBoxes(), pos);
  if (top != null) container.scrollTop = top;
  if (pos.sx != null) container.scrollLeft = pos.sx * container.scrollWidth;
}

async function scrollToTarget(t: Resolved): Promise<void> {
  if (!(await waitForPage(t.pageNum))) return;
  const box = pageBoxes().find((b) => b.num === t.pageNum)!;
  const scale = box.height / Number(box.el.dataset.baseHeight || box.height);
  const y = t.y == null ? 0 : t.y * scale;
  container.scrollTop = box.top + y - Math.min(24, y);   // a little context above, never the previous page
  if (t.x != null) {
    const wl = box.el.getBoundingClientRect().left - container.getBoundingClientRect().left + container.scrollLeft;
    const x = wl + t.x * scale;
    if (x < container.scrollLeft || x > container.scrollLeft + container.clientWidth - 40) container.scrollLeft = Math.max(0, x - 40);
  }
}

/* -------------------------------------------------------------------- history */
// Each history entry's state is {zcIdx, zcPos}; the current entry's position is kept up to date
// while scrolling, so Back/Forward return exactly to where you left each place. In one of the
// app's tabs the same is kept in `jumps` (see the top of this file).
const jumps: JumpHistory<Position> | null = inAppTab() ? new JumpHistory<Position>() : null;
if (jumps) jumps.entries[0].url = location.href;
let navIdx = jumps ? 0 : history.state?.zcIdx ?? 0;
let navMax = navIdx;
if (!jumps) history.replaceState({ ...(history.state || {}), zcIdx: navIdx }, '');

function recordPosition() {
  const pos = currentPosition();
  if (jumps) jumps.record(pos);
  else if (pos) history.replaceState({ ...(history.state || {}), zcIdx: navIdx, zcPos: pos }, '');
}

let scrollTimer: ReturnType<typeof setTimeout> | undefined;
container.addEventListener('scroll', () => {
  clearTimeout(scrollTimer);
  scrollTimer = setTimeout(recordPosition, 200);
}, { passive: true });

async function followInternal(target: LinkTarget): Promise<void> {
  const t = await resolveTarget(target);
  if (!t) return;
  await jumpRecorded(() => scrollToTarget(t));
}

/** Run a jump (`scroll`, async) as a new history entry, so Back returns to where the reader was.
 *  `url` (optional) becomes the entry's address, so a reload reopens at the new spot. */
async function jumpRecorded(scroll: () => unknown, url?: string): Promise<void> {
  clearTimeout(scrollTimer);
  recordPosition();                         // where to come back to
  await scroll();
  if (jumps) {
    jumps.push(currentPosition(), url);
    if (url) history.replaceState(history.state, '', url);   // never a new entry: the page around shares the history
    navIdx = jumps.idx;
    navMax = jumps.entries.length - 1;
  } else {
    navIdx += 1;
    navMax = navIdx;
    history.pushState({ zcIdx: navIdx, zcPos: currentPosition() }, '', url);
  }
  updateNavButtons();
}

window.addEventListener('popstate', (e) => {
  if (jumps || e.state?.zcIdx == null) return;
  navIdx = e.state.zcIdx;
  navMax = Math.max(navMax, navIdx);
  hidePopup();
  if (e.state.zcPos) scrollToPosition(e.state.zcPos);
  updateNavButtons();
});

/** Back (-1) or Forward (+1). */
function go(dir: number): void {
  if (!jumps) { if (dir < 0) history.back(); else history.forward(); return; }
  clearTimeout(scrollTimer);
  recordPosition();
  const to = dir < 0 ? jumps.back() : jumps.forward();
  if (!to) return;
  navIdx = jumps.idx;
  hidePopup();
  if (to.url) history.replaceState(history.state, '', to.url);
  if (to.pos) scrollToPosition(to.pos);
  updateNavButtons();
}

if (jumps) {
  // What the browser would do with these, done here so they stay inside this PDF.
  document.addEventListener('keydown', (e) => {
    const mac = /Mac|iPhone|iPad/.test(navigator.platform);
    const arrow = e.altKey && !e.metaKey && !e.ctrlKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight');
    const bracket = mac && e.metaKey && !e.altKey && !e.ctrlKey && (e.key === '[' || e.key === ']');
    if (!arrow && !bracket) return;
    const t = e.target as HTMLElement;
    if (arrow && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable)) return;
    e.preventDefault();
    go(e.key === 'ArrowLeft' || e.key === '[' ? -1 : 1);
  });
  // The mouse's back and forward buttons (Chrome acts on the button's release).
  document.addEventListener('mouseup', (e) => {
    if (e.button !== 3 && e.button !== 4) return;
    e.preventDefault();
    go(e.button === 3 ? -1 : 1);
  });
}

let backBtn: HTMLButtonElement | null = null, fwdBtn: HTMLButtonElement | null = null;
function addNavButtons() {
  const center = document.querySelector('.toolbar-section.center');
  if (!center || document.getElementById('zc-nav')) return;
  const nav = document.createElement('span');
  nav.id = 'zc-nav';
  nav.innerHTML = `
    <button class="icon-btn" id="zc-back" title="Back (Alt+←)"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3L5 8l5 5"/></svg></button>
    <button class="icon-btn" id="zc-forward" title="Forward (Alt+→)"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l5 5-5 5"/></svg></button>`;
  center.prepend(nav);
  backBtn = nav.querySelector<HTMLButtonElement>('#zc-back')!;
  fwdBtn = nav.querySelector<HTMLButtonElement>('#zc-forward')!;
  backBtn.addEventListener('click', () => go(-1));
  fwdBtn.addEventListener('click', () => go(1));
  updateNavButtons();
}

function updateNavButtons() {
  if (backBtn) backBtn.disabled = navIdx <= 0;
  if (fwdBtn) fwdBtn.disabled = navIdx >= navMax;
}

/* ---------------------------------------------------------------------- popup */
let popup: HTMLElement | null = null, hoverLink: LinkEl | null = null;
let hoverTimer: ReturnType<typeof setTimeout> | undefined, hideTimer: ReturnType<typeof setTimeout> | undefined;

function hidePopup() {
  clearTimeout(hoverTimer);
  clearTimeout(hideTimer);
  popup?.remove();
  popup = null;
}

function scheduleHide() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(hidePopup, 180);
}

function placePopup(el: HTMLElement, link: HTMLElement): void {
  const a = link.getBoundingClientRect();
  document.body.appendChild(el);
  const { left, top } = popupPlacement(a, el.offsetWidth, el.offsetHeight, window.innerWidth, window.innerHeight);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
  el.addEventListener('mouseenter', () => clearTimeout(hideTimer));
  el.addEventListener('mouseleave', scheduleHide);
  popup = el;
}

async function showPopup(link: LinkEl): Promise<void> {
  hidePopup();
  const target = link.zcTarget;
  const el = document.createElement('div');
  el.className = 'zc-link-pop';
  if (target.url) {
    const u = document.createElement('div');
    u.className = 'zc-pop-url';
    u.textContent = target.url;
    el.appendChild(u);
    placePopup(el, link);
    return;
  }
  const t = await resolveTarget(target);
  if (!t || hoverLink !== link) return;
  let page: PDFPageProxy, vp: PageViewport;
  if (t.page && t.vp) { page = t.page; vp = t.vp; } else { page = await viewer()!.pdfDoc!.getPage(t.pageNum); vp = page.getViewport({ scale: 1 }); }
  if (hoverLink !== link) return;
  // A figure or table: fit the whole of it, with its caption, in the popup. Anything else: the
  // destination near the top-left of a fixed window, on its column.
  const fig = t.y == null ? null : await figureAt(page, vp, t);
  if (hoverLink !== link) return;
  let scale = PREVIEW_SCALE, w: number, h: number, ox: number, oy: number;
  if (fig?.region) {
    const r = fig.region;
    const f = fitRegion(r, Math.min(640, window.innerWidth * 0.5), Math.min(620, window.innerHeight * 0.72));
    scale = f.s; w = f.w; h = f.h;
    ox = r.x * scale - (w - r.w * scale) / 2;
    oy = r.y * scale - (h - r.h * scale) / 2;
  } else {
    const pageW = vp.width * scale, pageH = vp.height * scale;
    const float = !!fig || isFloatDest(target.dest);
    w = Math.min(PREVIEW_W, Math.round(pageW));
    h = Math.min(float ? PREVIEW_H_FLOAT : PREVIEW_H, Math.round(pageH));
    const ty = t.y ?? 0;   // (t.y null: a page without a spot, top of the page)
    const top = fig?.table ? fig.top * scale : ty * scale;
    const bottom = fig?.table ? null : fig ? fig.bottom * scale : float ? ty * scale + 16 : null;
    ({ x: ox, y: oy } = previewOrigin(t.x == null ? null : t.x * scale, top, w, h, pageW, pageH, 16, bottom));
  }
  // The whole page, in a window onto it that can be scrolled and zoomed.
  const canvas = document.createElement('canvas');
  const scroller = document.createElement('div');
  scroller.className = 'zc-pop-page';
  scroller.style.width = `${w}px`;
  scroller.style.height = `${h}px`;
  scroller.appendChild(canvas);
  const head = document.createElement('div');
  head.className = 'zc-pop-head';
  head.innerHTML = `<span>Page ${t.pageNum}</span><span class="zc-pop-zoom"><button data-z="-1" title="Zoom out">−</button>
    <span class="zc-pop-pct"></span><button data-z="1" title="Zoom in (or pinch, ${navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+scroll)">+</button></span>
    <span>Click to go there</span>`;
  el.classList.add('preview');
  el.append(head, scroller);
  el.addEventListener('click', (e) => {
    const z = (e.target as Element).closest<HTMLElement>('[data-z]');
    if (z) { e.stopPropagation(); zoomTo(scale * (z.dataset.z === '1' ? 1.25 : 0.8)); return; }
    if ((e.target as Element).closest('.zc-pop-head')) return;
    hidePopup();
    followInternal(target);
  });
  placePopup(el, link);

  let renderTask: RenderTask | null = null, renderTimer: ReturnType<typeof setTimeout> | undefined;
  const paint = () => {
    const dpr = window.devicePixelRatio || 1;
    const pw = vp.width * scale, ph = vp.height * scale;
    canvas.style.width = `${Math.round(pw)}px`;
    canvas.style.height = `${Math.round(ph)}px`;
    head.querySelector('.zc-pop-pct')!.textContent = `${Math.round((scale / PREVIEW_SCALE) * 100)}%`;
    clearTimeout(renderTimer);
    renderTimer = setTimeout(async () => {   // the canvas is stretched until the new rendering is ready
      renderTask?.cancel();
      const fresh = document.createElement('canvas');
      fresh.width = Math.round(pw * dpr);
      fresh.height = Math.round(ph * dpr);
      const task = page.render({ canvasContext: fresh.getContext('2d')!, viewport: page.getViewport({ scale }),
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined });
      renderTask = task;
      try {
        await task.promise;
        canvas.width = fresh.width; canvas.height = fresh.height;
        canvas.getContext('2d')!.drawImage(fresh, 0, 0);
      } catch (e) { if ((e as Error | null)?.name !== 'RenderingCancelledException') console.warn('Link preview failed', e); }
    }, canvas.width ? 120 : 0);
  };
  // Zoom around a point of the window (its centre by default), keeping that point in place.
  const zoomTo = (next: number, px = scroller.clientWidth / 2, py = scroller.clientHeight / 2) => {
    next = Math.max(0.3, Math.min(4, next));
    if (Math.abs(next - scale) < 1e-3) return;
    const k = next / scale;
    const sx = (scroller.scrollLeft + px) * k - px, sy = (scroller.scrollTop + py) * k - py;
    scale = next;
    paint();
    scroller.scrollLeft = sx;
    scroller.scrollTop = sy;
  };
  // Wheel / touchpad scroll the page inside the popup; pinch and Ctrl/Cmd+wheel zoom it
  // (never the document behind it).
  scroller.addEventListener('wheel', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? scroller.clientHeight : 1;
    if (e.ctrlKey || e.metaKey) {
      const r = scroller.getBoundingClientRect();
      zoomTo(scale * Math.exp(-e.deltaY * unit * 0.01), e.clientX - r.left, e.clientY - r.top);
      return;
    }
    scroller.scrollBy({ left: (e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX) * unit, top: (e.shiftKey && !e.deltaX ? 0 : e.deltaY) * unit });
  }, { passive: false });
  paint();
  scroller.scrollLeft = ox;
  scroller.scrollTop = oy;
  (el as HTMLElement & { zcPreview?: unknown }).zcPreview = { get scale() { return scale; }, zoomTo, region: fig?.region || null };   // for tests
}

/**
 * The figure or table at a destination (page CSS px at scale 1): {top, bottom, table} of its
 * caption, and region {x, y, w, h}: the drawing above the caption (below it for a table) together
 * with the caption — or null if the destination is not a caption.
 */
const pageText = new Map<number, Promise<PageText[]>>(), pageGraphics = new Map<number, Promise<Box[]>>();
async function figureAt(page: PDFPageProxy, vp: PageViewport, t: Resolved):
  Promise<{ top: number; bottom: number; table: boolean; region: { x: number; y: number; w: number; h: number } | null } | null> {
  const n = page.pageNumber;
  if (!pageText.has(n)) {
    pageText.set(n, page.getTextContent().then((tc) => tc.items.filter((i): i is TextItem => 'str' in i && !!i.str?.trim()).map((i) => (
      { str: i.str, x: i.transform[4], y: i.transform[5], w: i.width, h: i.height || Math.hypot(i.transform[2], i.transform[3]) }))).catch(() => []));
  }
  const items = await pageText.get(n)!;
  // back to PDF coordinates (the target is in page CSS px)
  const [px, py] = xy(vp.convertToPdfPoint(t.x ?? 0, t.y!));   // only called with a y
  const width = page.view[0] + page.view[2];   // half of it: the middle of the page
  const c = findCaption(items, t.x == null ? null : px, py, width);
  if (!c) return null;
  const out: { top: number; bottom: number; table: boolean; region: { x: number; y: number; w: number; h: number } | null } = {
    top: xy(vp.convertToViewportPoint(0, c.top))[1], bottom: xy(vp.convertToViewportPoint(0, c.bottom))[1], table: c.table, region: null,
  };
  if (!pageGraphics.has(n)) {
    pageGraphics.set(n, page.getOperatorList().then((ol) => graphicsBoxes(ol.fnArray, ol.argsArray, window.pdfjsLib!.OPS, page.view as Box)).catch(() => []));
  }
  const box = figureRegion(await pageGraphics.get(n)!, c, width, page.view[1] + page.view[3], items);
  if (box) {
    const r = vp.convertToViewportRectangle(box);
    const x = Math.min(r[0], r[2]), y = Math.min(r[1], r[3]);
    out.region = { x, y, w: Math.abs(r[2] - r[0]), h: Math.abs(r[3] - r[1]) };
  }
  return out;
}

const linkAt = (e: Event) => (e.target as Element).closest?.<LinkEl>('.zc-link') ?? null;

container.addEventListener('mouseover', (e) => {
  const link = linkAt(e);
  if (!link || link === hoverLink) return;
  hoverLink = link;
  clearTimeout(hideTimer);
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(() => { if (hoverLink === link) showPopup(link); }, HOVER_DELAY);
});

container.addEventListener('mouseout', (e) => {
  const link = linkAt(e);
  if (!link || link.contains(e.relatedTarget as Node | null)) return;
  hoverLink = null;
  clearTimeout(hoverTimer);
  scheduleHide();
});

container.addEventListener('click', (e) => {
  const link = linkAt(e);
  if (!link || e.button !== 0) return;
  const target = link.zcTarget;
  if (target.url) { hidePopup(); return; }   // the <a> opens it in a new tab
  e.preventDefault();
  hidePopup();
  followInternal(target);
});

container.addEventListener('scroll', () => { if (popup && !popup.matches(':hover')) hidePopup(); }, { passive: true });
// Touch screens: a long press on a link shows its preview (a tap follows it); a tap
// outside closes the preview.
installLongPress();
container.addEventListener('zc:longpress', (e) => {
  const link = linkAt(e);
  if (!link) return;
  e.preventDefault();
  clearTimeout(hoverTimer);
  clearTimeout(hideTimer);
  hoverLink = link;
  showPopup(link);
});
document.addEventListener('pointerdown', (e) => {
  if (popup && e.pointerType !== 'mouse' && !popup.contains(e.target as Node)) hidePopup();
}, true);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hidePopup(); });

/* ---------------------------------------------------------------------- setup */
document.addEventListener('zc:pagecreated', (e) => addLinks(e.detail.wrapper, e.detail.page));
for (const w of container.querySelectorAll<PageWrapper>('.page-wrapper')) if (w.zcPage) addLinks(w, w.zcPage);
addNavButtons();
window.zcLinks = { currentPosition, followInternal, jumpRecorded, go, ready: true };   // jumpRecorded: notesBridge.ts; the rest for tests
