// A graph canvas on top of force-graph (vendor/force-graph, d3-force physics — the same model
// Obsidian's graph uses), styled like the rest of the app. Used by the local graph next to a note
// and by the global graph in Notes (notesGraph.ts).
//
//   const g = await createGraph(host, { compact, onNodeClick })
//   g.setData(view)        view = buildView(...) from model.ts: {nodes, edges, degree}
//   g.setCurrent(id)       the node drawn as "you are here"
//   g.setMatches(ids)      search hits (the rest fades)
//   g.focus(id) · g.fit() · g.resize() · g.destroy()
//   g.onEngineStop(fn)     the layout has settled
//   g.wake() · g.sleep()   start / stop drawing (it also does both by itself, see below)
//
// force-graph redraws the whole canvas on every animation frame while its loop runs. Left
// running, an idle graph next to a note kept the page, the GPU process and the window server
// busy all day. So the loop runs only while something can change on screen — the layout
// moving, a zoom or pan animation, the pointer over the canvas (hover highlights) — and stops
// soon after.

import type ForceGraphClass from 'force-graph';
import type { LinkObject, NodeObject } from 'force-graph';
import type { EdgeKind, GraphNode } from './types.js';
import { escapeHtml as esc } from './ui.js';
import { type GraphView, label } from './model.js';

/** A node as force-graph keeps it: our node plus its position and velocity. */
export type GNode = GraphNode & NodeObject & { id: string };
type GLink = LinkObject<GNode> & { kind: EdgeKind };
/** A d3 force (see gravity below). */
type ForceFn<N> = ((alpha: number) => void) & { initialize?: (nodes: N[]) => void };

export interface GraphCanvas {
  graph: ForceGraphClass<GNode, GLink>;
  setData(view: GraphView): void;
  setCurrent(nodeId: string | null): void;
  setMatches(ids: Set<string> | null): void;
  setMarked(nodeId: string | null): void;
  focus(nodeId: string, ms?: number): boolean;
  fit(ms?: number): void;
  resize(): void;
  refreshColors(): void;
  nodes(): GNode[];
  screenOf(nodeId: string): { x: number; y: number } | null;
  onEngineStop(fn: () => void): void;
  /** Draw for at least ``ms`` (and for as long as something moves). */
  wake(ms?: number): void;
  /** Stop drawing now (the graph was hidden). */
  sleep(): void;
  /** Whether the drawing loop should run, and how many frames it has drawn (for tests). */
  awake(): boolean;
  frames(): number;
  destroy(): void;
}

let loading: Promise<typeof ForceGraphClass> | null = null;
let libraryUrl = '';
export function configureGraphLibrary(url: string): void { libraryUrl = url; }
const graphWindow = window as Window & { ForceGraph?: typeof ForceGraphClass };
function loadForceGraph(): Promise<typeof ForceGraphClass> {
  loading ||= new Promise((resolve, reject) => {
    if (graphWindow.ForceGraph) { resolve(graphWindow.ForceGraph); return; }
    const s = document.createElement('script');
    s.src = libraryUrl;
    s.onload = () => resolve(graphWindow.ForceGraph!);
    s.onerror = () => { loading = null; reject(new Error('Could not load the graph library')); };
    document.head.appendChild(s);
  });
  return loading;
}

function palette() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
  return {
    note: v('--graph-node', '#8a93a3'), paper: v('--accent', '#2f6fed'), work: v('--text-3', '#8a93a3'),
    current: v('--graph-current', '#8e5cd9'), link: v('--graph-link', '#cfd4dc'), linkHi: v('--accent', '#2f6fed'),
    text: v('--text', '#1c2330'), text2: v('--text-2', '#5b6576'), bg: v('--bg', '#fff'), match: v('--warning', '#b7791f'),
  };
}

const kindName: Record<string, string> = { paper: 'Paper', work: 'Cited work (not in your library)', note: 'Note' };

export async function createGraph(
  host: HTMLElement,
  { compact = false, onNodeClick = () => {}, onBackgroundClick = () => {} }: {
    compact?: boolean;
    onNodeClick?: (node: GNode, event: MouseEvent) => void;
    onBackgroundClick?: () => void;
  } = {},
): Promise<GraphCanvas> {
  const ForceGraph = await loadForceGraph();
  let colors = palette();
  let degree = new Map<string, number>();
  let current: string | null = null, hover: string | null = null, matches: Set<string> | null = null, marked: string | null = null;
  let lit = new Set<string>(), litLinks = new Set<GLink>();      // hovered node + neighbours
  let neighbours = new Map<string, Set<string>>();                 // id → Set of ids
  const positions = new Map<string, { x: number; y: number }>();   // kept across setData

  const radius = (n: GNode) => (compact ? 2.6 : 3.2) + (compact ? 0.9 : 1.25) * Math.sqrt(degree.get(n.id) || 0);
  const g = new ForceGraph<GNode, GLink>(host)
    .backgroundColor('rgba(0,0,0,0)')
    .nodeId('id')
    .nodeRelSize(1)
    .nodeVal((n) => radius(n) ** 2)
    .nodeLabel((n) => `<div class="fg-tip"><b>${esc(n.title || label(n))}</b><span>${esc(kindName[n.kind] || '')}${
      n.kind !== 'note' && n.year ? ` · ${n.year}` : ''}</span></div>`)
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x!, n.y!, radius(n) + 2, 0, 2 * Math.PI); ctx.fill(); })
    .linkColor((l) => (isLit(l) ? colors.linkHi : colors.link))
    .linkWidth((l) => (isLit(l) ? 1.4 : 0.7))
    .linkLineDash((l) => (l.kind === 'about' ? [2, 2] : null))
    .linkVisibility((l) => !dimmed() || isLit(l) || (!hover && !!matches && matches.has(id(l.source)) && matches.has(id(l.target))))
    .autoPauseRedraw(false)          // hover highlighting changes colours without changing data
    .maxZoom(compact ? 2.4 : 8)
    .d3VelocityDecay(0.35)
    .cooldownTime(compact ? 4000 : 8000)
    .onNodeHover((n) => { hover = n ? n.id : null; updateLit(); host.style.cursor = n ? 'pointer' : ''; })
    .onNodeClick((n, e) => onNodeClick(n, e))
    .onBackgroundClick(() => onBackgroundClick())
    .onNodeDragEnd((n) => { n.fx = undefined; n.fy = undefined; })
    // Called from inside force-graph's frame: settle after it, see stop().
    .onRenderFramePost(() => { drawn++; })
    .onEngineStop(() => setTimeout(() => { moving = false; settle(); engineStopped(); }));

  // The drawing loop (see the top of the file).
  let moving = false;                 // the layout is being simulated
  let pressed = false;                // a button or finger is down (dragging, panning, pinching)
  let pointerType = '';
  let until = 0;                      // draw at least until then (zoom animations, one last frame)
  let sleepTimer: ReturnType<typeof setTimeout> | undefined;
  let running = true;                 // force-graph starts its loop when created
  let drawn = 0;
  let engineStopped = () => {};
  const listeners = new AbortController();
  const busy = () => moving || pressed
    // a finger leaves :hover behind after a tap, so only a mouse or pen keeps it awake by hovering
    || (pointerType !== 'touch' && host.isConnected && host.matches(':hover'));
  function settle() {
    clearTimeout(sleepTimer);
    if (!running) return;
    const left = until - Date.now();
    if (left > 0) sleepTimer = setTimeout(settle, left + 20);
    else if (busy()) sleepTimer = setTimeout(settle, 1000);   // :hover has no event when the canvas is hidden under the pointer
    else stop();
  }
  /** Pausing from inside force-graph's own frame (its callbacks) cancels the frame that is
      already running, and the frame then books the next one: pause once more after it. */
  function stop() {
    clearTimeout(sleepTimer);
    running = false;
    g.pauseAnimation();
    setTimeout(() => { if (!running) g.pauseAnimation(); });
  }
  function wake(ms = 250) {
    until = Math.max(until, Date.now() + ms);
    if (!running) { running = true; g.resumeAnimation(); }
    settle();
  }
  const sig = { signal: listeners.signal };
  host.addEventListener('pointerenter', (e) => { pointerType = e.pointerType; wake(); }, sig);
  host.addEventListener('pointermove', (e) => { pointerType = e.pointerType; if (!running) wake(); }, sig);
  host.addEventListener('pointerdown', (e) => { pointerType = e.pointerType; pressed = true; wake(); }, sig);
  host.addEventListener('wheel', () => wake(400), { ...sig, passive: true });
  host.addEventListener('pointerleave', () => wake(300), sig);   // a frame or two to clear hover highlights
  const release = () => { if (pressed) { pressed = false; wake(400); } };
  window.addEventListener('pointerup', release, sig);
  window.addEventListener('pointercancel', release, sig);
  // Obsidian-like physics: soft repulsion, short springs, and a pull to the centre so
  // separate clusters stay together on screen.
  g.d3Force('charge')!.strength(compact ? -45 : -70).distanceMax(compact ? 220 : 420);
  g.d3Force('link')!.distance(compact ? 28 : 38);
  g.d3Force('gravity', gravity(compact ? 0.09 : 0.05));

  const id = (end: GLink['source']) => (typeof end === 'object' ? end.id : end) as string;   // force-graph swaps ids for nodes
  const isLit = (l: GLink) => litLinks.has(l);
  const dimmed = () => !!hover || !!matches;

  function updateLit() {
    lit = new Set(); litLinks = new Set();
    if (hover) {
      lit.add(hover);
      for (const m of neighbours.get(hover) || []) lit.add(m);
      for (const l of g.graphData().links) if (id(l.source) === hover || id(l.target) === hover) litLinks.add(l);
    }
  }

  function drawNode(n: GNode, ctx: CanvasRenderingContext2D, scale: number) {
    const x = n.x!, y = n.y!;
    const r = radius(n);
    const faded = hover ? !lit.has(n.id) : matches ? !matches.has(n.id) : false;
    ctx.globalAlpha = faded ? 0.15 : 1;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
    if (n.kind === 'work') {
      ctx.fillStyle = colors.bg; ctx.fill();
      ctx.lineWidth = 1 / scale; ctx.strokeStyle = colors.work; ctx.stroke();
    } else {
      ctx.fillStyle = n.id === current ? colors.current : n.kind === 'paper' ? colors.paper : colors.note;
      ctx.fill();
    }
    if (n.id === current || n.id === marked || (matches && matches.has(n.id))) {
      ctx.beginPath();
      ctx.arc(x, y, r + 2.5 / scale, 0, 2 * Math.PI);
      ctx.lineWidth = 1.5 / scale;
      ctx.strokeStyle = n.id === current ? colors.current : colors.match;
      ctx.stroke();
    }
    // Labels fade in as you zoom (always on for the hovered node, its neighbours and "you are here").
    const important = n.id === current || n.id === marked || n.id === hover || (hover && lit.has(n.id)) || (matches && matches.has(n.id));
    const zoomAlpha = Math.min(1, Math.max(0, (scale - (compact ? 0.9 : 1.15)) / 0.6));
    const a = important ? 1 : faded ? 0 : zoomAlpha;
    if (a > 0.02) {
      const size = (compact ? 10 : 11) / scale;
      ctx.font = `${size}px ${getComputedStyle(document.body).fontFamily}`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      let text = label(n);
      const max = compact ? 22 : 36;
      if (text.length > max) text = `${text.slice(0, max - 2)}…`;
      ctx.globalAlpha = a;
      ctx.fillStyle = important ? colors.text : colors.text2;
      ctx.fillText(text, x, y + r + 2 / scale);
    }
    ctx.globalAlpha = 1;
  }

  const api: GraphCanvas = {
    graph: g,
    setData(view) {
      for (const n of g.graphData().nodes) if (Number.isFinite(n.x)) positions.set(n.id, { x: n.x!, y: n.y! });
      degree = view.degree;
      neighbours = new Map();
      for (const e of view.edges) {
        (neighbours.get(e.source) || neighbours.set(e.source, new Set()).get(e.source)!).add(e.target);
        (neighbours.get(e.target) || neighbours.set(e.target, new Set()).get(e.target)!).add(e.source);
      }
      const nodes: GNode[] = view.nodes.map((n) => ({ ...n, ...(positions.get(n.id) || {}) }));
      const links: GLink[] = view.edges.map((e) => ({ source: e.source, target: e.target, kind: e.kind }));
      if (hover && !nodes.some((n) => n.id === hover)) hover = null;
      g.graphData({ nodes, links });   // reheats the layout
      updateLit();
      moving = true;
      wake();
    },
    setCurrent(nodeId) { current = nodeId; wake(); },
    setMatches(ids) { matches = ids && ids.size ? ids : null; wake(); },
    setMarked(nodeId) { marked = nodeId; wake(); },     // a ring and a label, without fading the rest
    focus(nodeId, ms = 600) {
      const n = g.graphData().nodes.find((x) => x.id === nodeId);
      if (!n || !Number.isFinite(n.x)) return false;
      wake(ms + 100);
      g.centerAt(n.x!, n.y!, ms);
      if (g.zoom() < 1.6) g.zoom(2, ms);
      return true;
    },
    fit(ms = 400) { wake(ms + 100); g.zoomToFit(ms, compact ? 34 : 60); },
    resize() { g.width(host.clientWidth).height(host.clientHeight); wake(); },
    refreshColors() { colors = palette(); wake(); },
    nodes: () => g.graphData().nodes,
    screenOf(nodeId) {   // for tests
      const n = g.graphData().nodes.find((x) => x.id === nodeId);
      return n && Number.isFinite(n.x) ? g.graph2ScreenCoords(n.x!, n.y!) : null;
    },
    onEngineStop(fn) { engineStopped = fn; },
    wake,
    sleep() { until = 0; pressed = false; stop(); },
    awake: () => running,
    frames: () => drawn,
    destroy() { api.sleep(); listeners.abort(); g._destructor?.(); host.innerHTML = ''; },
  };
  api.resize();
  return api;
}

/** A custom d3 force: pull every node a little towards (0, 0). */
function gravity(k: number): ForceFn<GNode> {
  let nodes: GNode[] = [];
  const force: ForceFn<GNode> = (alpha: number) => {
    for (const n of nodes) { n.vx! -= n.x! * k * alpha; n.vy! -= n.y! * k * alpha; }
  };
  force.initialize = (ns: GNode[]) => { nodes = ns; };
  return force;
}
