// PDF viewer: clickable links, a preview of the target on hover, and Back/Forward between the
// places you jumped to.
//
// Loaded by viewer.html as a separate module, like notesBridge.js. The viewer exposes
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

import { destTarget, findCaption, isFloatDest, popupPlacement, positionAt, previewOrigin, rectPercent, scrollFor } from './linkNav.js';

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
  .zc-link-pop .zc-pop-head { display: flex; justify-content: space-between; gap: 12px; padding: 4px 9px;
    color: var(--text-3, #888); border-bottom: 1px solid var(--border, #eee); font-size: 11px; }
  .zc-link-pop .zc-pop-url { padding: 6px 10px; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #zc-nav { display: flex; gap: 2px; margin-right: 6px; }
  #zc-nav .icon-btn:disabled { opacity: .35; cursor: default; background: transparent; }
`;
document.head.appendChild(css);

const viewer = () => window.zcViewer;
const container = document.getElementById('pdf-container');

/* ---------------------------------------------------------------- link layer */
async function addLinks(wrapper, page) {
  if (wrapper.querySelector('.zc-links')) return;
  const layer = document.createElement('div');
  layer.className = 'zc-links';
  wrapper.appendChild(layer);
  let annots;
  try { annots = await page.getAnnotations({ intent: 'display' }); } catch { return; }
  const vp = page.getViewport({ scale: 1 });
  for (const a of annots) {
    if (a.subtype !== 'Link' || !a.rect) continue;
    const target = linkTarget(a);
    if (!target) continue;
    const el = document.createElement('a');
    el.className = 'zc-link';
    Object.assign(el.style, rectPercent(vp.convertToViewportRectangle(a.rect), vp.width, vp.height));
    if (target.url) { el.href = target.url; el.target = '_blank'; el.rel = 'noopener noreferrer'; }
    el.zcTarget = target;
    layer.appendChild(el);
  }
}

function linkTarget(a) {
  if (a.dest) return { dest: a.dest };
  if (a.url) return { url: a.url };
  if (['NextPage', 'PrevPage', 'FirstPage', 'LastPage'].includes(a.action)) return { action: a.action };
  return null;
}

/** {pageNum, x, y} for a link target, with x/y in page CSS pixels at scale 1 (null = free). */
const resolved = new Map();
async function resolveTarget(target) {
  const doc = viewer()?.pdfDoc;
  if (!doc) return null;
  if (target.action) {
    const cur = currentPosition()?.page || 1;
    const n = { NextPage: cur + 1, PrevPage: cur - 1, FirstPage: 1, LastPage: doc.numPages }[target.action];
    return n >= 1 && n <= doc.numPages ? { pageNum: n, x: null, y: null } : null;
  }
  const key = JSON.stringify(target.dest);
  if (resolved.has(key)) return resolved.get(key);
  let out = null;
  try {
    const explicit = typeof target.dest === 'string' ? await doc.getDestination(target.dest) : target.dest;
    const t = destTarget(explicit);
    if (t) {
      const index = typeof t.ref === 'object' && t.ref ? await doc.getPageIndex(t.ref) : Number.isInteger(t.ref) ? t.ref : null;
      if (index != null && index >= 0 && index < doc.numPages) {
        const page = await doc.getPage(index + 1);
        const vp = page.getViewport({ scale: 1 });
        // a free coordinate is converted as the page's top-left corner, then reported as null
        const [cx, cy] = vp.convertToViewportPoint(t.x ?? page.view[0], t.y ?? page.view[3]);
        out = { pageNum: index + 1, x: t.x == null ? null : cx, y: t.y == null ? null : cy, page, vp };
      }
    }
  } catch (e) { console.warn('Could not resolve link destination', e); }
  resolved.set(key, out);
  return out;
}

/* ------------------------------------------------------------------ positions */
function pageBoxes() {
  const cTop = container.getBoundingClientRect().top - container.scrollTop + container.clientTop;
  return [...container.querySelectorAll('.page-wrapper')].map((w) => {
    const r = w.getBoundingClientRect();
    return { num: Number(w.dataset.pageNumber), top: r.top - cTop, height: r.height, el: w };
  }).sort((a, b) => a.num - b.num);
}

function currentPosition() {
  const pos = positionAt(pageBoxes(), container.scrollTop);
  if (pos && container.scrollWidth > container.clientWidth) pos.sx = Math.round(1000 * container.scrollLeft / container.scrollWidth) / 1000;
  return pos;
}

/** Wait (up to 20 s) for a page to be laid out — the viewer creates them one after another. */
function waitForPage(num) {
  const has = () => container.querySelector(`.page-wrapper[data-page-number="${num}"]`);
  if (has()) return Promise.resolve(true);
  return new Promise((resolve) => {
    const obs = new MutationObserver(() => { if (has()) { obs.disconnect(); resolve(true); } });
    obs.observe(container, { childList: true });
    setTimeout(() => { obs.disconnect(); resolve(!!has()); }, 20000);
  });
}

async function scrollToPosition(pos) {
  if (!pos?.page || !(await waitForPage(pos.page))) return;
  const top = scrollFor(pageBoxes(), pos);
  if (top != null) container.scrollTop = top;
  if (pos.sx != null) container.scrollLeft = pos.sx * container.scrollWidth;
}

async function scrollToTarget(t) {
  if (!(await waitForPage(t.pageNum))) return;
  const box = pageBoxes().find((b) => b.num === t.pageNum);
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
// while scrolling, so Back/Forward return exactly to where you left each place.
let navIdx = history.state?.zcIdx ?? 0;
let navMax = navIdx;
history.replaceState({ ...(history.state || {}), zcIdx: navIdx }, '');

function recordPosition() {
  const pos = currentPosition();
  if (pos) history.replaceState({ ...(history.state || {}), zcIdx: navIdx, zcPos: pos }, '');
}

let scrollTimer = null;
container.addEventListener('scroll', () => {
  clearTimeout(scrollTimer);
  scrollTimer = setTimeout(recordPosition, 200);
}, { passive: true });

async function followInternal(target) {
  const t = await resolveTarget(target);
  if (!t) return;
  clearTimeout(scrollTimer);
  recordPosition();                         // where to come back to
  await scrollToTarget(t);
  navIdx += 1;
  navMax = navIdx;
  history.pushState({ zcIdx: navIdx, zcPos: currentPosition() }, '');
  updateNavButtons();
}

window.addEventListener('popstate', (e) => {
  if (e.state?.zcIdx == null) return;
  navIdx = e.state.zcIdx;
  navMax = Math.max(navMax, navIdx);
  hidePopup();
  if (e.state.zcPos) scrollToPosition(e.state.zcPos);
  updateNavButtons();
});

let backBtn = null, fwdBtn = null;
function addNavButtons() {
  const center = document.querySelector('.toolbar-section.center');
  if (!center || document.getElementById('zc-nav')) return;
  const nav = document.createElement('span');
  nav.id = 'zc-nav';
  nav.innerHTML = `
    <button class="icon-btn" id="zc-back" title="Back (Alt+←)"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3L5 8l5 5"/></svg></button>
    <button class="icon-btn" id="zc-forward" title="Forward (Alt+→)"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l5 5-5 5"/></svg></button>`;
  center.prepend(nav);
  backBtn = nav.querySelector('#zc-back');
  fwdBtn = nav.querySelector('#zc-forward');
  backBtn.addEventListener('click', () => history.back());
  fwdBtn.addEventListener('click', () => history.forward());
  updateNavButtons();
}

function updateNavButtons() {
  if (backBtn) backBtn.disabled = navIdx <= 0;
  if (fwdBtn) fwdBtn.disabled = navIdx >= navMax;
}

/* ---------------------------------------------------------------------- popup */
let popup = null, hoverTimer = null, hideTimer = null, hoverLink = null;

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

function placePopup(el, link) {
  const a = link.getBoundingClientRect();
  document.body.appendChild(el);
  const { left, top } = popupPlacement(a, el.offsetWidth, el.offsetHeight, window.innerWidth, window.innerHeight);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
  el.addEventListener('mouseenter', () => clearTimeout(hideTimer));
  el.addEventListener('mouseleave', scheduleHide);
  popup = el;
}

async function showPopup(link) {
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
  let page = t.page, vp = t.vp;
  if (!page) { page = await viewer().pdfDoc.getPage(t.pageNum); vp = page.getViewport({ scale: 1 }); }
  if (hoverLink !== link) return;
  const pageW = vp.width * PREVIEW_SCALE, pageH = vp.height * PREVIEW_SCALE;
  const w = Math.min(PREVIEW_W, Math.round(pageW));
  // A figure: end the window with its caption (the figure is above it). A table: start with its
  // caption (the table is below it).
  const caption = t.y == null ? null : await captionAt(page, vp, t);
  if (hoverLink !== link) return;
  const float = !!caption || isFloatDest(target.dest);
  const h = Math.min(float ? PREVIEW_H_FLOAT : PREVIEW_H, Math.round(pageH));
  const S = PREVIEW_SCALE;
  const top = caption?.table ? caption.top * S : t.y == null ? null : t.y * S;
  const bottom = caption?.table ? null : caption ? caption.bottom * S : float ? t.y * S + 16 : null;
  const o = previewOrigin(t.x == null ? null : t.x * S, top, w, h, pageW, pageH, 16, bottom);
  // The whole page, in a window that starts at the destination and can be scrolled.
  const dpr = window.devicePixelRatio || 1;
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(pageW * dpr);
  canvas.height = Math.round(pageH * dpr);
  canvas.style.width = `${Math.round(pageW)}px`;
  canvas.style.height = `${Math.round(pageH)}px`;
  const scroller = document.createElement('div');
  scroller.className = 'zc-pop-page';
  scroller.style.width = `${w}px`;
  scroller.style.height = `${h}px`;
  scroller.appendChild(canvas);
  // Wheel and touchpad scroll the page inside the popup (never the document behind it).
  scroller.addEventListener('wheel', (e) => {
    if (e.ctrlKey || e.metaKey) return;   // pinch / Cmd+wheel: the viewer's zoom
    e.preventDefault();
    e.stopPropagation();
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? scroller.clientHeight : 1;
    scroller.scrollBy({ left: (e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX) * unit, top: (e.shiftKey && !e.deltaX ? 0 : e.deltaY) * unit });
  }, { passive: false });
  const head = document.createElement('div');
  head.className = 'zc-pop-head';
  head.innerHTML = `<span>Page ${t.pageNum}</span><span>Scroll to look around · click to go there</span>`;
  el.classList.add('preview');
  el.append(head, scroller);
  el.addEventListener('click', () => { hidePopup(); followInternal(target); });
  placePopup(el, link);
  scroller.scrollLeft = o.x;
  scroller.scrollTop = o.y;
  try {
    await page.render({
      canvasContext: canvas.getContext('2d'),
      viewport: page.getViewport({ scale: PREVIEW_SCALE }),
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
    }).promise;
  } catch (e) { console.warn('Link preview failed', e); }
}

/** The caption at a destination, in page CSS px at scale 1 ({top, bottom}), or null. */
const pageText = new Map();
async function captionAt(page, vp, t) {
  if (!pageText.has(page.pageNumber)) {
    pageText.set(page.pageNumber, page.getTextContent().then((tc) => tc.items.filter((i) => i.str?.trim()).map((i) => (
      { str: i.str, x: i.transform[4], y: i.transform[5], h: i.height || Math.hypot(i.transform[2], i.transform[3]) }))).catch(() => []));
  }
  const items = await pageText.get(page.pageNumber);
  // back to PDF coordinates (the target is in page CSS px)
  const [px, py] = vp.convertToPdfPoint(t.x ?? 0, t.y);
  const c = findCaption(items, t.x == null ? null : px, py, page.view[0] + page.view[2]);   // half of it: the middle of the page
  if (!c) return null;
  return { top: vp.convertToViewportPoint(0, c.top)[1], bottom: vp.convertToViewportPoint(0, c.bottom)[1], table: c.table };
}

container.addEventListener('mouseover', (e) => {
  const link = e.target.closest?.('.zc-link');
  if (!link || link === hoverLink) return;
  hoverLink = link;
  clearTimeout(hideTimer);
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(() => { if (hoverLink === link) showPopup(link); }, HOVER_DELAY);
});

container.addEventListener('mouseout', (e) => {
  const link = e.target.closest?.('.zc-link');
  if (!link || link.contains(e.relatedTarget)) return;
  hoverLink = null;
  clearTimeout(hoverTimer);
  scheduleHide();
});

container.addEventListener('click', (e) => {
  const link = e.target.closest?.('.zc-link');
  if (!link || e.button !== 0) return;
  const target = link.zcTarget;
  if (target.url) { hidePopup(); return; }   // the <a> opens it in a new tab
  e.preventDefault();
  hidePopup();
  followInternal(target);
});

container.addEventListener('scroll', () => { if (popup && !popup.matches(':hover')) hidePopup(); }, { passive: true });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hidePopup(); });

/* ---------------------------------------------------------------------- setup */
document.addEventListener('zc:pagecreated', (e) => addLinks(e.detail.wrapper, e.detail.page));
for (const w of container.querySelectorAll('.page-wrapper')) if (w.zcPage) addLinks(w, w.zcPage);
addNavButtons();
window.zcLinks = { currentPosition, followInternal, ready: true };   // for tests
