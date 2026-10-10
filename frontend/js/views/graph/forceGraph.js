// A graph canvas on top of force-graph (vendor/force-graph, d3-force physics — the same model
// Obsidian's graph uses), styled like the rest of the app. Used by the local graph next to a note
// and by the global graph in Notes (notesGraph.js).
//
//   const g = await createGraph(host, { compact, onNodeClick })
//   g.setData(view)        view = buildView(...) from model.js: {nodes, edges, degree}
//   g.setCurrent(id)       the node drawn as "you are here"
//   g.setMatches(ids)      search hits (the rest fades)
//   g.focus(id) · g.fit() · g.resize() · g.destroy()

import { escapeHtml as esc } from '../../util.js';
import { label } from './model.js';

let loading = null;
function loadForceGraph() {
  loading ||= new Promise((resolve, reject) => {
    if (window.ForceGraph) { resolve(window.ForceGraph); return; }
    const s = document.createElement('script');
    s.src = 'vendor/force-graph/force-graph.min.js';
    s.onload = () => resolve(window.ForceGraph);
    s.onerror = () => { loading = null; reject(new Error('Could not load the graph library')); };
    document.head.appendChild(s);
  });
  return loading;
}

function palette() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback;
  return {
    note: v('--graph-node', '#8a93a3'), paper: v('--accent', '#2f6fed'), work: v('--text-3', '#8a93a3'),
    current: v('--graph-current', '#8e5cd9'), link: v('--graph-link', '#cfd4dc'), linkHi: v('--accent', '#2f6fed'),
    text: v('--text', '#1c2330'), text2: v('--text-2', '#5b6576'), bg: v('--bg', '#fff'), match: v('--warning', '#b7791f'),
  };
}

const kindName = { paper: 'Paper', work: 'Cited work (not in your library)', note: 'Note' };

export async function createGraph(host, { compact = false, onNodeClick = () => {}, onBackgroundClick = () => {} } = {}) {
  const ForceGraph = await loadForceGraph();
  let colors = palette();
  let degree = new Map();
  let current = null, hover = null, matches = null, marked = null;
  let lit = new Set(), litLinks = new Set();      // hovered node + neighbours
  let neighbours = new Map();                      // id → Set of ids
  const positions = new Map();                     // id → {x, y}, kept across setData

  const radius = (n) => (compact ? 2.6 : 3.2) + (compact ? 0.9 : 1.25) * Math.sqrt(degree.get(n.id) || 0);
  const g = new ForceGraph(host)
    .backgroundColor('rgba(0,0,0,0)')
    .nodeId('id')
    .nodeRelSize(1)
    .nodeVal((n) => radius(n) ** 2)
    .nodeLabel((n) => `<div class="fg-tip"><b>${esc(n.title || label(n))}</b><span>${esc(kindName[n.kind] || '')}${
      n.kind !== 'note' && n.year ? ` · ${n.year}` : ''}</span></div>`)
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, radius(n) + 2, 0, 2 * Math.PI); ctx.fill(); })
    .linkColor((l) => (isLit(l) ? colors.linkHi : colors.link))
    .linkWidth((l) => (isLit(l) ? 1.4 : 0.7))
    .linkLineDash((l) => (l.kind === 'about' ? [2, 2] : null))
    .linkVisibility((l) => !dimmed() || isLit(l) || (!hover && matches && matches.has(id(l.source)) && matches.has(id(l.target))))
    .autoPauseRedraw(false)          // hover highlighting changes colours without changing data
    .maxZoom(compact ? 2.4 : 8)
    .d3VelocityDecay(0.35)
    .cooldownTime(compact ? 4000 : 8000)
    .onNodeHover((n) => { hover = n ? n.id : null; updateLit(); host.style.cursor = n ? 'pointer' : ''; })
    .onNodeClick((n, e) => onNodeClick(n, e))
    .onBackgroundClick(() => onBackgroundClick())
    .onNodeDragEnd((n) => { n.fx = undefined; n.fy = undefined; });
  // Obsidian-like physics: soft repulsion, short springs, and a pull to the centre so
  // separate clusters stay together on screen.
  g.d3Force('charge').strength(compact ? -45 : -70).distanceMax(compact ? 220 : 420);
  g.d3Force('link').distance(compact ? 28 : 38);
  g.d3Force('gravity', gravity(compact ? 0.09 : 0.05));

  const id = (end) => (typeof end === 'object' ? end.id : end);
  const isLit = (l) => litLinks.has(l);
  const dimmed = () => !!hover || !!matches;

  function updateLit() {
    lit = new Set(); litLinks = new Set();
    if (hover) {
      lit.add(hover);
      for (const m of neighbours.get(hover) || []) lit.add(m);
      for (const l of g.graphData().links) if (id(l.source) === hover || id(l.target) === hover) litLinks.add(l);
    }
  }

  function drawNode(n, ctx, scale) {
    const r = radius(n);
    const faded = hover ? !lit.has(n.id) : matches ? !matches.has(n.id) : false;
    ctx.globalAlpha = faded ? 0.15 : 1;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
    if (n.kind === 'work') {
      ctx.fillStyle = colors.bg; ctx.fill();
      ctx.lineWidth = 1 / scale; ctx.strokeStyle = colors.work; ctx.stroke();
    } else {
      ctx.fillStyle = n.id === current ? colors.current : n.kind === 'paper' ? colors.paper : colors.note;
      ctx.fill();
    }
    if (n.id === current || n.id === marked || (matches && matches.has(n.id))) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 2.5 / scale, 0, 2 * Math.PI);
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
      ctx.fillText(text, n.x, n.y + r + 2 / scale);
    }
    ctx.globalAlpha = 1;
  }

  const api = {
    graph: g,
    setData(view) {
      for (const n of g.graphData().nodes) if (Number.isFinite(n.x)) positions.set(n.id, { x: n.x, y: n.y });
      degree = view.degree;
      neighbours = new Map();
      for (const e of view.edges) {
        (neighbours.get(e.source) || neighbours.set(e.source, new Set()).get(e.source)).add(e.target);
        (neighbours.get(e.target) || neighbours.set(e.target, new Set()).get(e.target)).add(e.source);
      }
      const nodes = view.nodes.map((n) => ({ ...n, ...(positions.get(n.id) || {}) }));
      const links = view.edges.map((e) => ({ source: e.source, target: e.target, kind: e.kind }));
      if (hover && !nodes.some((n) => n.id === hover)) hover = null;
      g.graphData({ nodes, links });
      updateLit();
    },
    setCurrent(nodeId) { current = nodeId; },
    setMatches(ids) { matches = ids && ids.size ? ids : null; },
    setMarked(nodeId) { marked = nodeId; },     // a ring and a label, without fading the rest
    focus(nodeId, ms = 600) {
      const n = g.graphData().nodes.find((x) => x.id === nodeId);
      if (!n || !Number.isFinite(n.x)) return false;
      g.centerAt(n.x, n.y, ms);
      if (g.zoom() < 1.6) g.zoom(2, ms);
      return true;
    },
    fit(ms = 400) { g.zoomToFit(ms, compact ? 34 : 60); },
    resize() { g.width(host.clientWidth).height(host.clientHeight); },
    refreshColors() { colors = palette(); },
    nodes: () => g.graphData().nodes,
    screenOf(nodeId) {   // for tests
      const n = g.graphData().nodes.find((x) => x.id === nodeId);
      return n && Number.isFinite(n.x) ? g.graph2ScreenCoords(n.x, n.y) : null;
    },
    destroy() { g.pauseAnimation(); g._destructor?.(); host.innerHTML = ''; },
  };
  api.resize();
  return api;
}

/** A custom d3 force: pull every node a little towards (0, 0). */
function gravity(k) {
  let nodes = [];
  const force = (alpha) => {
    for (const n of nodes) { n.vx -= n.x * k * alpha; n.vy -= n.y * k * alpha; }
  };
  force.initialize = (ns) => { nodes = ns; };
  return force;
}
