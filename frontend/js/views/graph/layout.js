// Force-directed layout for the Graph view (no DOM; unit-tested in tests/js/graph.test.mjs).
//
// The same model as d3-force, written out so the app stays dependency-free and offline:
// many-body repulsion (Barnes–Hut quadtree), springs along edges, a weak pull to the centre so
// separate clusters stay in view, and velocity decay. ``alpha`` cools from 1 towards 0; the
// caller steps until ``done``.

const THETA2 = 0.81;          // Barnes–Hut accuracy (θ = 0.9)
const CHARGE = -60;
const LINK_DISTANCE = 42;
const GRAVITY = 0.04;
const VELOCITY_DECAY = 0.6;
const ALPHA_MIN = 0.002;
const ALPHA_DECAY = 1 - Math.pow(ALPHA_MIN, 1 / 300);

export class ForceLayout {
  /**
   * @param nodes [{id}]   @param edges [{source, target}] (ids)
   * @param prev  Map id → {x, y}: positions to keep (e.g. from before the filters changed)
   */
  constructor(nodes, edges, prev = new Map()) {
    this.n = nodes.length;
    this.ids = nodes.map((n) => n.id);
    const index = new Map(this.ids.map((id, i) => [id, i]));
    this.index = index;
    this.x = new Float64Array(this.n);
    this.y = new Float64Array(this.n);
    this.vx = new Float64Array(this.n);
    this.vy = new Float64Array(this.n);
    this.fixed = new Uint8Array(this.n);
    this.links = [];
    const degree = new Uint32Array(this.n);
    for (const e of edges) {
      const s = index.get(e.source), t = index.get(e.target);
      if (s == null || t == null || s === t) continue;
      this.links.push([s, t]);
      degree[s]++; degree[t]++;
    }
    this.degree = degree;
    this.linkBias = this.links.map(([s, t]) => degree[s] / (degree[s] + degree[t]));
    this.linkStrength = this.links.map(([s, t]) => 1 / Math.min(degree[s], degree[t]));

    // Start: kept positions; new nodes next to a placed neighbour, else on a phyllotaxis spiral.
    const placed = new Uint8Array(this.n);
    let kept = 0;
    for (let i = 0; i < this.n; i++) {
      const p = prev.get(this.ids[i]);
      if (p) { this.x[i] = p.x; this.y[i] = p.y; placed[i] = 1; kept++; }
    }
    const neighbours = Array.from({ length: this.n }, () => []);
    for (const [s, t] of this.links) { neighbours[s].push(t); neighbours[t].push(s); }
    let spiral = 0;
    for (let i = 0; i < this.n; i++) {
      if (placed[i]) continue;
      const nb = neighbours[i].find((j) => placed[j]);
      const jitter = hash(this.ids[i]);
      if (nb != null && kept) {
        const a = jitter * 2 * Math.PI;
        this.x[i] = this.x[nb] + 18 * Math.cos(a);
        this.y[i] = this.y[nb] + 18 * Math.sin(a);
      } else {
        const r = 10 * Math.sqrt(0.5 + spiral), a = spiral * Math.PI * (3 - Math.sqrt(5));
        this.x[i] = r * Math.cos(a);
        this.y[i] = r * Math.sin(a);
        spiral++;
      }
      placed[i] = 1;
    }
    // Mostly kept positions: start cool so the picture doesn't jump.
    this.alpha = this.n && kept / this.n > 0.8 ? 0.3 : 1;
  }

  get done() { return this.alpha < ALPHA_MIN; }

  /** Warm up again (after dragging a node, or when nodes were added). */
  reheat(alpha = 0.3) { this.alpha = Math.max(this.alpha, alpha); }

  step() {
    if (!this.n) { this.alpha = 0; return; }
    this.alpha += (0 - this.alpha) * ALPHA_DECAY;
    const { x, y, vx, vy, alpha } = this;
    // springs
    for (let k = 0; k < this.links.length; k++) {
      const [s, t] = this.links[k];
      let dx = x[t] + vx[t] - x[s] - vx[s] || jiggle(k), dy = y[t] + vy[t] - y[s] - vy[s] || jiggle(k + 1);
      const l = Math.sqrt(dx * dx + dy * dy);
      const f = ((l - LINK_DISTANCE) / l) * alpha * this.linkStrength[k];
      dx *= f; dy *= f;
      const b = this.linkBias[k];
      vx[t] -= dx * b; vy[t] -= dy * b;
      vx[s] += dx * (1 - b); vy[s] += dy * (1 - b);
    }
    // repulsion
    const tree = buildTree(x, y, this.n);
    for (let i = 0; i < this.n; i++) applyCharge(tree, i, x, y, vx, vy, alpha);
    // centre pull, integrate
    for (let i = 0; i < this.n; i++) {
      vx[i] -= x[i] * GRAVITY * alpha;
      vy[i] -= y[i] * GRAVITY * alpha;
      if (this.fixed[i]) { vx[i] = vy[i] = 0; continue; }
      vx[i] *= VELOCITY_DECAY; vy[i] *= VELOCITY_DECAY;
      x[i] += vx[i]; y[i] += vy[i];
    }
  }

  /** Run until cool (or ``max`` steps) — for tests and for tiny graphs. */
  run(max = 400) { for (let k = 0; k < max && !this.done; k++) this.step(); return this; }

  position(id) {
    const i = this.index.get(id);
    return i == null ? null : { x: this.x[i], y: this.y[i] };
  }

  positions() {
    const m = new Map();
    for (let i = 0; i < this.n; i++) m.set(this.ids[i], { x: this.x[i], y: this.y[i] });
    return m;
  }

  /** Pin a node where the user drags it (null releases it). */
  pin(id, p) {
    const i = this.index.get(id);
    if (i == null) return;
    if (p) { this.x[i] = p.x; this.y[i] = p.y; this.vx[i] = this.vy[i] = 0; this.fixed[i] = 1; } else this.fixed[i] = 0;
  }
}

/* -------------------------------------------------------------- Barnes–Hut */
// A quadtree node: {x0, y0, size, mass, cx, cy, body (index | -1), kids [4] | null}
function buildTree(x, y, n) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i++) {
    if (x[i] < x0) x0 = x[i]; if (x[i] > x1) x1 = x[i];
    if (y[i] < y0) y0 = y[i]; if (y[i] > y1) y1 = y[i];
  }
  const size = Math.max(x1 - x0, y1 - y0, 1) * 1.0001;
  const root = cell(x0, y0, size);
  for (let i = 0; i < n; i++) insert(root, i, x, y, 0);
  summarise(root, x, y);
  return root;
}

const cell = (x0, y0, size) => ({ x0, y0, size, mass: 0, cx: 0, cy: 0, body: -1, extra: null, kids: null });

function insert(c, i, x, y, depth) {
  if (!c.kids) {
    if (c.body < 0 && !c.extra) { c.body = i; return; }
    if (depth > 40) { (c.extra ||= []).push(i); return; }   // coincident points: keep them together
    const b = c.body;
    c.body = -1;
    const h = c.size / 2;
    c.kids = [cell(c.x0, c.y0, h), cell(c.x0 + h, c.y0, h), cell(c.x0, c.y0 + h, h), cell(c.x0 + h, c.y0 + h, h)];
    if (b >= 0) insert(c, b, x, y, depth);
  }
  const h = c.size / 2;
  const q = (x[i] >= c.x0 + h ? 1 : 0) + (y[i] >= c.y0 + h ? 2 : 0);
  insert(c.kids[q], i, x, y, depth + 1);
}

function summarise(c, x, y) {
  if (!c.kids) {
    const bodies = c.body >= 0 ? [c.body, ...(c.extra || [])] : c.extra || [];
    for (const b of bodies) { c.mass++; c.cx += x[b]; c.cy += y[b]; }
  } else {
    for (const k of c.kids) {
      summarise(k, x, y);
      c.mass += k.mass; c.cx += k.cx * k.mass; c.cy += k.cy * k.mass;
    }
  }
  if (c.mass) { c.cx /= c.mass; c.cy /= c.mass; }
}

function applyCharge(c, i, x, y, vx, vy, alpha) {
  if (!c.mass) return;
  let dx = c.cx - x[i], dy = c.cy - y[i];
  const isSelf = !c.kids && (c.body === i || c.extra?.includes(i));
  if (!c.kids || (c.size * c.size) / (dx * dx + dy * dy) < THETA2) {
    let mass = c.mass;
    if (isSelf) {
      mass -= 1;
      if (!mass) return;
    }
    if (dx === 0) dx = jiggle(i);
    if (dy === 0) dy = jiggle(i + 7);
    let l2 = dx * dx + dy * dy;
    if (l2 < 1) l2 = Math.sqrt(l2);
    const f = (CHARGE * alpha * mass) / l2;
    vx[i] += dx * f; vy[i] += dy * f;
    return;
  }
  for (const k of c.kids) applyCharge(k, i, x, y, vx, vy, alpha);
}

const jiggle = (k) => ((((k * 9301 + 49297) % 233280) / 233280) - 0.5) * 1e-6;

/** A stable number in [0, 1) from a string. */
export function hash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0) / 4294967296;
}
