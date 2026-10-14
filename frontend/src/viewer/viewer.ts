// The PDF viewer (viewer.html): lays out the pages, draws those near the viewport with pdf.js,
// zooms (buttons, Ctrl/⌘+wheel, pinch) around the spot under the pointer, shows thumbnails and
// switches the annotation tools. The add-ons in this folder (annotations, links, notesBridge)
// hook in through window.zcViewer and the zc:pagecreated / zc:pagerendered events.
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist';

/** A page's box in the viewer; pdf.js's page object is kept on it. */
export type PageWrapper = HTMLDivElement & { zcPage: PDFPageProxy };

/** Safari's pinch events (not in the DOM types). */
interface GestureEvent extends UIEvent { scale: number; clientX: number; clientY: number }

/** A page drawn on a canvas (or being drawn), and its text layer. */
interface LivePage {
  scale?: number;
  canvas?: HTMLCanvasElement;
  text?: HTMLElement;
  task: RenderTask | null;
  outputScale?: number;
  pending?: boolean;
}

const urlParams = new URLSearchParams(window.location.search);
const pdfUrl = urlParams.get('file');
const titleParam = urlParams.get('title');
if (titleParam) {
  document.title = titleParam;
  const t = document.getElementById('doc-title')!;
  t.textContent = titleParam;
  t.title = titleParam;
}

if (!pdfUrl) {
  document.getElementById('pdf-container')!.innerHTML = '<h2 style="color:white;">No PDF file specified.</h2>';
} else {
  initPdf(pdfUrl);   // annotations are loaded by annotations.ts
}

function initPdf(pdfUrl: string): void {
  const pdfjsLib = window.pdfjsLib!;
  pdfjsLib.GlobalWorkerOptions.workerSrc = 'vendor/pdfjs/pdf.worker.min.js';

  let pdfDoc: PDFDocumentProxy | null = null,
    scale = 1.5;
  const container = document.getElementById('pdf-container')!;

  const pageNumText = document.getElementById('page-num-text')!;

  // For the viewer's add-on modules (annotations.ts, links.ts, notesBridge.ts)
  window.zcViewer = { get pdfDoc() { return pdfDoc; }, get scale() { return scale; }, container };

  // Observer to update current page number
  const pageObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting && pdfDoc) {
        const pageNum = (entry.target as HTMLElement).dataset.pageNumber;
        pageNumText.textContent = `${pageNum} / ${pdfDoc.numPages}`;
      }
    });
  }, { threshold: 0.5 });

  let renderTimeout: ReturnType<typeof setTimeout> | null = null;
  function debouncedRenderAll() {   // after zooming: only the pages near the viewport (pump)
    if (renderTimeout) clearTimeout(renderTimeout);
    renderTimeout = setTimeout(() => pump(), 300);
  }

  /** The page under a point of the viewer (or the nearest one, when the point is in a gap). */
  function pageAt(clientY: number): PageWrapper | null {
    let best: PageWrapper | null = null, bestD = Infinity;
    for (const w of pageWrappers) {
      if (!w) continue;
      const r = w.getBoundingClientRect();
      const d = clientY < r.top ? r.top - clientY : clientY > r.bottom ? clientY - r.bottom : 0;
      if (d < bestD) { best = w; bestD = d; }
      if (d === 0 || r.top > clientY + 2000) break;   // pages are in order: no need to look further
    }
    return best;
  }

  /* Zoom keeps the spot under the pointer (or the middle of the view, for the buttons) where it
   is. The spot is remembered as a page and a fraction of that page, not as a scroll offset:
   pages are centred and the gaps between them do not grow with the zoom, so scaling the
   scroll offsets (as before) let the spot drift, more and more on later pages. */
  const MAX_ZOOM = 10;   // deep zoom stays sharp: the visible part gets its own canvas (renderDetails)
  let zoomGesture: { px: number; py: number; anchor: PageWrapper | null; fx: number; fy: number; t?: number } | null = null;   // the zoom in progress
  function applyZoom(newScale: number, pointerX = container.clientWidth / 2, pointerY = container.clientHeight / 2): void {
    const oldScale = scale;
    scale = Math.min(Math.max(0.2, newScale), MAX_ZOOM);
    if (scale === oldScale) return; // Don't do anything if hitting limits

    const box = container.getBoundingClientRect();
    const px = box.left + container.clientLeft + pointerX;
    const py = box.top + container.clientTop + pointerY;
    // One pinch (or several clicks on +/-) is one gesture: keep the spot it started on. While
    // a page is narrower than the view it stays centred and cannot follow the pointer
    // sideways; measuring again at every step would lose the spot by the time it can.
    const now = performance.now();
    let g = zoomGesture;
    if (!g || now - (g.t ?? 0) > 400 || Math.abs(g.px - px) > 4 || Math.abs(g.py - py) > 4 || !g.anchor?.isConnected) {
      const anchor = pageAt(py);
      g = { px, py, anchor, fx: 0.5, fy: 0 };
      if (anchor) {
        const r = anchor.getBoundingClientRect();
        g.fx = (px - r.left) / r.width;
        g.fy = (py - r.top) / r.height;
      }
    }
    g.t = now;
    zoomGesture = g;
    const { anchor, fx, fy } = g;

    pageWrappers.forEach(w => {
      const newW = parseFloat(w.dataset.baseWidth!) * scale;
      const newH = parseFloat(w.dataset.baseHeight!) * scale;
      w.style.width = newW + "px";
      w.style.height = newH + "px";

      const canvas = w.querySelector('canvas');
      if (canvas) {
        canvas.style.width = newW + "px";
        canvas.style.height = newH + "px";
      }

      const tl = w.querySelector<HTMLElement>('.textLayer');   // (text boxes and detail canvases follow by themselves)
      if (tl) {
        tl.style.width = newW + "px";
        tl.style.height = newH + "px";
      }
    });

    if (anchor) {   // scroll so the same spot of the same page is under the pointer again
      const r = anchor.getBoundingClientRect();
      container.scrollLeft += r.left + fx * r.width - px;
      container.scrollTop += r.top + fy * r.height - py;
    }

    container.style.setProperty('--zc-scale', String(scale));
    debouncedRenderAll();
  }
  container.style.setProperty('--zc-scale', String(scale));

  document.getElementById('zoom-in')!.addEventListener('click', () => applyZoom(scale + 0.25));
  document.getElementById('zoom-out')!.addEventListener('click', () => applyZoom(scale - 0.25));

  // Sidebar logic
  const sidebar = document.getElementById('sidebar')!;
  document.getElementById('sidebar-toggle')!.addEventListener('click', () => {
    if (sidebar.style.display === 'none' || sidebar.style.display === '') {
      sidebar.style.display = 'flex';
      if (!sidebar.dataset.loaded && pdfDoc) {
        renderThumbnails();
        sidebar.dataset.loaded = "true";
      }
    } else {
      sidebar.style.display = 'none';
    }
  });

  function renderThumbnails() {
    sidebar.innerHTML = '';
    const thumbObserver = new IntersectionObserver((entries) => {
      for (const e of entries) {
        const target = e.target as HTMLElement;
        if (!e.isIntersecting || target.dataset.done) continue;
        target.dataset.done = '1';
        thumbObserver.unobserve(target);
        const canvas = target.querySelector('canvas')!;
        const page = pageWrappers[Number(target.dataset.page) - 1]?.zcPage;
        if (!page) continue;
        const vp = page.getViewport({ scale: 120 / page.getViewport({ scale: 1 }).width });
        canvas.width = vp.width;
        canvas.height = vp.height;
        page.render({ canvasContext: canvas.getContext('2d')!, viewport: vp });
      }
    }, { root: sidebar, rootMargin: '200px 0px' });
    for (let i = 1; i <= pdfDoc!.numPages; i++) {
      const thumb = document.createElement('div');
      thumb.className = 'thumb-wrapper';
      thumb.dataset.page = String(i);
      const canvas = document.createElement('canvas');
      const w = pageWrappers[i - 1];
      if (w) {   // the right shape before it is drawn
        canvas.width = 120;
        canvas.height = Math.round(120 * parseFloat(w.dataset.baseHeight!) / parseFloat(w.dataset.baseWidth!));
      }
      const label = document.createElement('span');
      label.className = 'thumb-label';
      label.textContent = String(i);
      thumb.append(canvas, label);
      sidebar.appendChild(thumb);
      thumb.addEventListener('click', () => pageWrappers[i - 1]?.scrollIntoView({ behavior: 'smooth' }));
      thumbObserver.observe(thumb);
    }
  }

  let currentTool = 'select';
  const colorWrapper = document.getElementById('color-wrapper')!;
  const toolSize = document.getElementById('tool-size') as HTMLInputElement;
  const toolbarSep = document.querySelector<HTMLElement>('.toolbar-sep')!;
  // On touch screens the colour and size are hidden but keep their place (see the CSS)
  const KEEP_PLACE = matchMedia('(pointer: coarse)').matches;
  function showOpt(n: HTMLElement, on: boolean): void {
    const d = n === colorWrapper ? 'flex' : 'block';
    if (KEEP_PLACE) { n.style.display = d; n.style.visibility = on ? 'visible' : 'hidden'; }
    else n.style.display = on ? d : 'none';
  }
  for (const n of [toolbarSep, colorWrapper, toolSize]) showOpt(n, false);

  const DRAWING = ['box', 'area', 'draw', 'text'];   // tools that draw on the page rather than select text
  function updateToolMode() {
    document.body.dataset.zcTool = currentTool;
    document.querySelectorAll('.anno-layer').forEach(svg => {
      svg.classList.remove('mode-draw', 'mode-select', 'mode-highlight');
      if (DRAWING.includes(currentTool)) svg.classList.add('mode-draw');
      else if (currentTool === 'highlight' || currentTool === 'underline') svg.classList.add('mode-highlight');
      else svg.classList.add('mode-select');
    });
    document.querySelectorAll<HTMLElement>('.textLayer').forEach(tl => {
      tl.style.pointerEvents = DRAWING.includes(currentTool) ? 'none' : 'auto';
    });
  }

  // Which tools have a colour and a size; the annotations add-on sets their values (zc:toolchange).
  const HAS_COLOR = ['highlight', 'area', 'underline', 'text', 'box', 'draw'];
  const HAS_SIZE = ['underline', 'text', 'box', 'draw'];
  document.querySelectorAll<HTMLElement>('.tool-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tool = btn.dataset.tool!;
      // clicking the active tool again goes back to Select
      currentTool = currentTool === tool ? 'select' : tool;
      document.querySelectorAll('.tool-btn').forEach(b => b.classList.toggle('active', (b as HTMLElement).dataset.tool === currentTool));
      showOpt(toolbarSep, currentTool !== 'select');
      showOpt(colorWrapper, HAS_COLOR.includes(currentTool));
      showOpt(toolSize, HAS_SIZE.includes(currentTool));
      updateToolMode();
      document.dispatchEvent(new CustomEvent('zc:toolchange', { detail: { tool: currentTool } }));
    });
  });

  window.addEventListener('wheel', (e) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      let zoomFactor = Math.exp(-e.deltaY * 0.01);
      let rect = container.getBoundingClientRect();
      applyZoom(scale * zoomFactor, e.clientX - rect.left, e.clientY - rect.top);
    }
  }, { passive: false });

  /* Pinch to zoom on a touch screen: the pages are zoomed (and drawn sharp again),
   never the browser page, and the spot between the fingers stays where it is (applyZoom).
   Safari reports a pinch as gesture events; other touch browsers through two pointers. */
  let pinch: { start: number; x: number; y: number; target: number } | null = null;   // start scale, container coordinates of the first centre, target scale
  // The browser page itself is zoomed (it should not be, but iPadOS can still do it, e.g. on
  // focusing a small text field in an older version): pinching must be able to undo it.
  const pageZoomed = () => (window.visualViewport?.scale || 1) > 1.01;
  window.visualViewport?.addEventListener('resize', () => document.documentElement.classList.toggle('zc-page-zoomed', pageZoomed()));
  function pinchAt(clientX: number, clientY: number, start: number): void {
    const r = container.getBoundingClientRect();
    zoomGesture = null;
    pinch = { start, x: clientX - r.left - container.clientLeft, y: clientY - r.top - container.clientTop, target: start };
  }
  /* While the fingers move, the pages are only scaled on screen (a transform around the pinch
   centre: nothing is laid out or drawn again); the zoom itself is applied once, when the pinch
   ends, and keeps the same spot under the fingers. Laying the pages out again at every step
   made Safari show the drawings in stripes from several zoom levels at once. */
  function pinchTo(s: number): void {
    if (!pinch) return;
    pinch.target = Math.min(Math.max(0.2, s), MAX_ZOOM);
    container.style.transformOrigin = `${pinch.x + container.clientLeft}px ${pinch.y + container.clientTop}px`;
    container.style.transform = `scale(${pinch.target / scale})`;
  }
  function pinchEnd() {
    if (!pinch) return;
    const { target, x, y } = pinch;
    pinch = null;
    container.style.transform = '';
    applyZoom(target, x, y);
  }
  if ('ongesturechange' in window) {
    window.addEventListener('gesturestart', (ev) => {
      const e = ev as GestureEvent;
      if (pageZoomed()) return;   // let this pinch zoom the browser page back out
      e.preventDefault();
      pinchAt(e.clientX, e.clientY, scale);
    }, { passive: false });
    window.addEventListener('gesturechange', (ev) => {
      const e = ev as GestureEvent;
      if (!pinch) return;
      e.preventDefault();
      pinchTo(pinch.start * e.scale);
    }, { passive: false });
    window.addEventListener('gestureend', (e) => { if (pinch) { e.preventDefault(); pinchEnd(); } }, { passive: false });
  } else {
    const touches = new Map<number, { x: number; y: number }>();   // pointerId → {x, y}
    let startDist = 0;
    const spread = () => { const [a, b] = [...touches.values()]; return Math.hypot(a.x - b.x, a.y - b.y); };
    container.addEventListener('pointerdown', (e) => {
      if (e.pointerType !== 'touch') return;
      touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (touches.size === 2) {
        const [a, b] = [...touches.values()];
        startDist = spread();
        pinchAt((a.x + b.x) / 2, (a.y + b.y) / 2, scale);
      }
    }, true);   // capturing: a pinch may start on an annotation, which keeps its events to itself
    container.addEventListener('pointermove', (e) => {
      if (!touches.has(e.pointerId)) return;
      touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (touches.size === 2 && pinch && startDist > 0) pinchTo(pinch.start * spread() / startDist);
    }, true);
    const lift = (e: PointerEvent) => { touches.delete(e.pointerId); if (touches.size < 2) pinchEnd(); };
    container.addEventListener('pointerup', lift, true);
    container.addEventListener('pointercancel', lift, true);
  }

  const pageWrappers: PageWrapper[] = [];
  // Annotations (drawing, highlighting, text boxes, undo) live in annotations.ts.

  /* Pages are laid out at once (an empty wrapper of the right size per page, so scrolling,
   ?page=N and the add-ons work everywhere), but only the pages within about a screen of the
   viewport get a canvas and a text layer; pages farther away give theirs back. A 300-page
   book used to render every page up front, and again on every zoom. */
  const NEAR = '100% 0px';       // render pages this close to the viewport
  const FAR = '250% 0px';        // free pages farther than this
  // Tablets have far less memory for canvases than a computer (Safari on iPad also refuses
  // any canvas above 16.7 million pixels): fewer pages are kept drawn there, at most this big.
  const COARSE = matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 1;
  const MAX_LIVE = COARSE ? 8 : 14;   // never more canvases than this, whatever happens
  const MAX_AREA = COARSE ? 8e6 : Infinity;   // pixels per canvas
  const live = new Map<number, LivePage>();   // page number → what is drawn of it
  const wanted = new Set<number>();      // pages near the viewport
  const inFar = new Set<number>();       // pages within the far margin (the others must not keep a canvas)
  let pumping = false;

  const nearObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const num = Number((e.target as HTMLElement).dataset.pageNumber);
      if (e.isIntersecting) wanted.add(num); else wanted.delete(num);
    }
    pump();
  }, { root: container, rootMargin: NEAR });
  const farObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const num = Number((e.target as HTMLElement).dataset.pageNumber);
      if (e.isIntersecting) inFar.add(num); else { inFar.delete(num); release(num); }
    }
  }, { root: container, rootMargin: FAR });

  function drawingTool() { return ['box', 'draw', 'text'].includes(document.body.dataset.zcTool ?? ''); }

  function viewportCenterDistance(num: number): number {
    const w = pageWrappers[num - 1];
    if (!w) return Infinity;
    const mid = container.scrollTop + container.clientHeight / 2;
    const top = w.offsetTop, bottom = top + w.offsetHeight;
    return mid < top ? top - mid : mid > bottom ? mid - bottom : 0;
  }

  /** The nearest wanted page that is not rendered at the current zoom. */
  function nextToRender(): number | null {
    let best: number | null = null, bestD = Infinity;
    for (const num of wanted) {
      if (live.get(num)?.scale === scale) continue;
      const d = viewportCenterDistance(num);
      if (d < bestD) { best = num; bestD = d; }
    }
    return best;
  }

  async function pump() {
    if (pumping || !pdfDoc) return;
    pumping = true;
    try {
      // pages rendered at an old zoom and no longer near: free them rather than redo them
      for (const [num, l] of live) if (l.scale !== scale && !wanted.has(num)) release(num);
      for (let num = nextToRender(); num; num = nextToRender()) {
        await renderPage(num);
        trimLive();
      }
      // a page finished drawing just as the reader jumped away: give it back now
      for (const num of [...live.keys()]) if (!inFar.has(num) && !wanted.has(num)) release(num);
    } finally { pumping = false; }
    scheduleDetails(50);
  }

  /* Deep zoom stays sharp. A page's canvas is capped (MAX_AREA, MAX_DIMENSION), so
   beyond some zoom it is drawn at less than the screen's resolution and the text is blurry.
   The part of such a page that is on screen (and a margin) then gets a second, sharp canvas
   on top, placed in fractions of the page so that zooming carries it along until it is redone. */
  const DETAIL_AREA = Math.min(MAX_AREA, 16e6);
  // page number → its sharp canvas, the zoom it was drawn at and its box [x0, y0, x1, y1] in CSS px at that zoom
  const details = new Map<number, { canvas?: HTMLCanvasElement; scale: number; box: number[]; task?: RenderTask }>();
  let detailTimer: ReturnType<typeof setTimeout> | undefined, detailing = false, detailAgain = false;
  function scheduleDetails(delay = 150) { clearTimeout(detailTimer); detailTimer = setTimeout(renderDetails, delay); }
  container.addEventListener('scroll', () => scheduleDetails(), { passive: true });

  function dropDetail(num: number): void {
    const d = details.get(num);
    if (!d) return;
    try { d.task?.cancel(); } catch (e) {}
    if (d.canvas) { d.canvas.width = 0; d.canvas.height = 0; d.canvas.remove(); }
    details.delete(num);
  }

  async function renderDetails() {
    if (pumping) return;   // the pump calls again when it is done
    if (detailing) { detailAgain = true; return; }
    detailing = true;
    try {
      const dpr = window.devicePixelRatio || 1;
      const view = container.getBoundingClientRect();
      for (const num of [...details.keys()]) if (!live.get(num)?.canvas) dropDetail(num);
      for (const [num, l] of [...live]) {
        if (!l.canvas || l.scale !== scale) continue;   // not drawn at this zoom yet: the pump comes first
        const drawnAt = l.outputScale!;   // set together with the canvas
        if (drawnAt >= dpr * 0.99) { dropDetail(num); continue; }   // the page canvas is sharp
        const w = pageWrappers[num - 1];
        const r = w.getBoundingClientRect();
        const x0 = Math.max(view.left, r.left) - r.left, x1 = Math.min(view.right, r.right) - r.left;
        const y0 = Math.max(view.top, r.top) - r.top, y1 = Math.min(view.bottom, r.bottom) - r.top;
        if (x1 <= x0 || y1 <= y0) { dropDetail(num); continue; }   // off screen
        const d = details.get(num);
        if (d && d.scale === scale && d.box[0] <= x0 + 1 && d.box[1] <= y0 + 1 && d.box[2] >= x1 - 1 && d.box[3] >= y1 - 1) continue;
        // a margin around the visible part, as far as the pixel budget allows
        let box = [x0, y0, x1, y1];
        for (const m of [0.5, 0.25]) {
          const mx = (x1 - x0) * m, my = (y1 - y0) * m;
          const b = [Math.max(0, x0 - mx), Math.max(0, y0 - my), Math.min(r.width, x1 + mx), Math.min(r.height, y1 + my)];
          if ((b[2] - b[0]) * (b[3] - b[1]) * dpr * dpr <= DETAIL_AREA) { box = b; break; }
        }
        const [bx, by] = [Math.floor(box[0]), Math.floor(box[1])];
        const [bw, bh] = [Math.ceil(box[2]) - bx, Math.ceil(box[3]) - by];
        let os = dpr;
        if (bw * bh * os * os > DETAIL_AREA) os = Math.sqrt(DETAIL_AREA / (bw * bh));
        if (os <= drawnAt * 1.05) continue;   // would be no sharper
        const canvas = document.createElement('canvas');
        canvas.className = 'zc-detail';
        canvas.width = Math.ceil(bw * os);
        canvas.height = Math.ceil(bh * os);
        const pct = (v: number, of: number) => `${(v / of) * 100}%`;
        Object.assign(canvas.style, { left: pct(bx, r.width), top: pct(by, r.height), width: pct(bw, r.width), height: pct(bh, r.height) });
        const s = scale;
        const task = w.zcPage.render({ canvasContext: canvas.getContext('2d')!, viewport: w.zcPage.getViewport({ scale: s }),
                       transform: [os, 0, 0, os, -bx * os, -by * os] });
        try { await task.promise; } catch (e) { canvas.width = canvas.height = 0; continue; }
        const now = live.get(num);
        if (s !== scale || !now?.canvas) { canvas.width = canvas.height = 0; continue; }   // zoomed or freed meanwhile
        dropDetail(num);
        now.canvas.after(canvas);   // above the page's canvas, under its text and annotations
        details.set(num, { canvas, scale: s, box: [bx, by, bx + bw, by + bh] });
      }
    } finally {
      detailing = false;
      if (detailAgain) { detailAgain = false; scheduleDetails(50); }
    }
  }

  function trimLive() {
    if (live.size <= MAX_LIVE) return;
    const order = [...live.keys()].sort((x, y) => viewportCenterDistance(y) - viewportCenterDistance(x));
    for (const num of order.slice(0, live.size - MAX_LIVE)) release(num);
  }

  function release(num: number): void {
    dropDetail(num);
    const l = live.get(num);
    if (!l) return;
    try { l.task?.cancel(); } catch (e) {}
    if (l.canvas) { l.canvas.width = 0; l.canvas.height = 0; l.canvas.remove(); }
    l.text?.remove();
    live.delete(num);
  }

  async function renderPage(num: number): Promise<void> {
    const wrapper = pageWrappers[num - 1];
    const page = wrapper.zcPage;
    const s = scale;
    const viewport = page.getViewport({ scale: s });

    const canvas = document.createElement('canvas');
    let outputScale = window.devicePixelRatio || 1;
    const MAX_DIMENSION = 8192;
    if ((viewport.width * outputScale) > MAX_DIMENSION || (viewport.height * outputScale) > MAX_DIMENSION) {
      outputScale *= Math.min(MAX_DIMENSION / (viewport.width * outputScale), MAX_DIMENSION / (viewport.height * outputScale));
    }
    const area = viewport.width * viewport.height * outputScale * outputScale;
    if (area > MAX_AREA) outputScale *= Math.sqrt(MAX_AREA / area);
    canvas.width = Math.floor(viewport.width * outputScale);
    canvas.height = Math.floor(viewport.height * outputScale);
    canvas.style.width = Math.floor(viewport.width) + "px";
    canvas.style.height = Math.floor(viewport.height) + "px";
    const task = page.render({ canvasContext: canvas.getContext('2d')!, viewport,
      transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined });
    const prev = live.get(num);
    live.set(num, { ...(prev || {}), task, pending: true });
    try {
      await task.promise;
    } catch (e) {
      canvas.width = canvas.height = 0;
      if (live.get(num)?.task === task) { if (prev?.canvas) live.set(num, prev); else live.delete(num); }
      return;   // cancelled (page freed) or failed
    }
    if (s !== scale || !wanted.has(num)) {   // zoomed or scrolled away meanwhile
      canvas.width = canvas.height = 0;
      if (live.get(num)?.task === task) { if (prev?.canvas) live.set(num, prev); else live.delete(num); }
      return;
    }
    const textContent = await page.getTextContent();
    if (s !== scale || !wanted.has(num) || live.get(num)?.task !== task) {   // moved on while reading the text
      canvas.width = canvas.height = 0;
      if (live.get(num)?.task === task) { if (prev?.canvas) live.set(num, prev); else live.delete(num); }
      return;
    }
    const text = document.createElement('div');
    text.className = 'textLayer';
    Object.assign(text.style, { width: Math.floor(viewport.width) + 'px', height: Math.floor(viewport.height) + 'px',
                  position: 'absolute', left: '0', top: '0', opacity: '1',
                  pointerEvents: drawingTool() ? 'none' : 'auto' });
    text.style.setProperty('--scale-factor', String(viewport.scale));
    // swap in one go (no blank flash while zooming); canvas and text stay under the other layers
    if (prev?.canvas) { prev.canvas.width = 0; prev.canvas.height = 0; prev.canvas.remove(); }
    prev?.text?.remove();
    wrapper.insertBefore(canvas, wrapper.firstChild);
    canvas.after(text);
    pdfjsLib.renderTextLayer({ textContentSource: textContent, container: text, viewport, textDivs: [] });
    live.set(num, { scale: s, canvas, text, task: null, outputScale });
    wrapper.dataset.rendered = String(s);
    document.dispatchEvent(new CustomEvent('zc:pagerendered', { detail: { wrapper, page, num } }));
  }

  /** Every page as an empty wrapper of the right size; the add-ons hear about each one.
    The first page is laid out (and drawn) before the others are even read. */
  function layoutPage(page: PDFPageProxy): void {
    const num = page.pageNumber;
    const base = page.getViewport({ scale: 1 });
    const wrapper = document.createElement('div') as PageWrapper;
    wrapper.className = 'page-wrapper';
    wrapper.dataset.baseWidth = String(base.width);
    wrapper.dataset.baseHeight = String(base.height);
    wrapper.dataset.pageNumber = String(num);
    wrapper.style.width = Math.floor(base.width * scale) + "px";
    wrapper.style.height = Math.floor(base.height * scale) + "px";
    wrapper.zcPage = page;
    container.appendChild(wrapper);
    pageWrappers[num - 1] = wrapper;
    pageObserver.observe(wrapper);
    nearObserver.observe(wrapper);
    farObserver.observe(wrapper);
    // the annotation and link layers are added on this event (annotations.ts, links.ts)
    document.dispatchEvent(new CustomEvent('zc:pagecreated', { detail: { wrapper, page } }));
  }

  async function layoutPages(): Promise<void> {
    const doc = pdfDoc!;
    layoutPage(await doc.getPage(1));
    pageNumText.textContent = `1 / ${doc.numPages}`;
    // draw page 1 before asking the PDF worker about the other pages (it does one thing at a time)
    if (!new URLSearchParams(location.search).get('page')) {
      wanted.add(1);
      await pump();
    }
    for (let first = 2; first <= doc.numPages; first += 50) {   // the rest, 50 at a time, in order
      const last = Math.min(doc.numPages, first + 49);
      const pages = await Promise.all(Array.from({ length: last - first + 1 }, (_, i) => doc.getPage(first + i)));
      pages.forEach(layoutPage);
    }
  }

  // for the add-ons and the browser checklists
  Object.assign(window.zcViewer!, { renderedPages: () => [...live.entries()].filter(([, l]) => l.canvas).map(([n]) => n).sort((a, b) => a - b) });

  pdfjsLib.getDocument(pdfUrl).promise.then(async function(pdfDoc_) {
    pdfDoc = pdfDoc_;
    await layoutPages();
    pump();
  }).catch(err => {
    console.error(err);
    container.innerHTML = `<h2 style="color:white;">Error loading PDF: ${err.message}</h2>`;
  });
}
