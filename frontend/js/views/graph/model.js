// What the Graph view shows, from /api/graph and the options (no DOM; tests/js/graph.test.mjs).

export const EDGE_KINDS = ['cites', 'cite', 'link', 'about'];

export const DEFAULT_OPTIONS = {
  papers: true,          // library items
  works: true,           // cited works that are not in the library…
  minCited: 2,           // …cited by at least this many shown papers
  notes: true,
  edges: { cites: true, cite: true, link: true, about: true },
  folders: null,         // Set of folder ids (a folder and its subfolders) or null = whole library
  hideIsolated: true,
  focus: null,           // node id: show only its neighbourhood…
  depth: 1,              // …up to this many steps away
};

const fold = (s) => String(s ?? '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();

/** The nodes and edges to draw: {nodes, edges, degree: Map id → number of shown edges}. */
export function buildView(data, opts = DEFAULT_OPTIONS) {
  const o = { ...DEFAULT_OPTIONS, ...opts, edges: { ...DEFAULT_OPTIONS.edges, ...(opts.edges || {}) } };
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  const focused = o.focus && byId.has(o.focus);

  const papers = new Set();
  for (const n of data.nodes) {
    if (n.kind === 'paper' && o.papers && (!o.folders || (n.folder_ids || []).some((f) => o.folders.has(f)))) papers.add(n.id);
  }
  // Works: counted over the papers in scope. Around a focused node every reference counts.
  const minCited = focused ? 1 : Math.max(1, o.minCited | 0);
  const citedBy = new Map();
  if (o.works) {
    for (const e of data.edges) {
      if (e.kind === 'cites' && papers.has(e.source) && byId.get(e.target)?.kind === 'work') {
        citedBy.set(e.target, (citedBy.get(e.target) || 0) + 1);
      }
    }
  }
  const shown = new Set(papers);
  for (const [id, c] of citedBy) if (c >= minCited) shown.add(id);
  if (o.notes) {
    // With a folder scope, a note belongs if it cites or is about a paper in scope (or links to such a note).
    const notes = data.nodes.filter((n) => n.kind === 'note');
    if (!o.folders) notes.forEach((n) => shown.add(n.id));
    else {
      const touching = new Set(data.edges.filter((e) => (e.kind === 'cite' || e.kind === 'about') && papers.has(e.target)).map((e) => e.source));
      touching.forEach((id) => shown.add(id));
      for (const e of data.edges) if (e.kind === 'link' && touching.has(e.target)) shown.add(e.source);   // one step only
    }
  }
  if (focused) shown.add(o.focus);

  let edges = data.edges.filter((e) => o.edges[e.kind] && shown.has(e.source) && shown.has(e.target) && e.source !== e.target);
  let ids = shown;
  if (focused) {
    ids = neighbourhood(o.focus, edges, Math.max(1, o.depth | 0));
    edges = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  }
  const degree = new Map();
  for (const e of edges) {
    degree.set(e.source, (degree.get(e.source) || 0) + 1);
    degree.set(e.target, (degree.get(e.target) || 0) + 1);
  }
  const nodes = [...ids].map((id) => byId.get(id)).filter((n) => n && (!o.hideIsolated || degree.has(n.id) || n.id === o.focus));
  return { nodes, edges, degree };
}

/** Ids within ``depth`` steps of ``id``, edges taken in both directions. */
export function neighbourhood(id, edges, depth) {
  const adj = new Map();
  for (const e of edges) {
    (adj.get(e.source) || adj.set(e.source, []).get(e.source)).push(e.target);
    (adj.get(e.target) || adj.set(e.target, []).get(e.target)).push(e.source);
  }
  const seen = new Set([id]);
  let frontier = [id];
  for (let d = 0; d < depth && frontier.length; d++) {
    const next = [];
    for (const a of frontier) for (const b of adj.get(a) || []) if (!seen.has(b)) { seen.add(b); next.push(b); }
    frontier = next;
  }
  return seen;
}

/** Nodes matching a search (every word in title, authors, citation key, path or year; accents ignored). */
export function matchNodes(nodes, query) {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return nodes.filter((n) => {
    const hay = fold(`${n.title} ${n.authors || ''} ${n.citation_key || ''} ${n.path || ''} ${n.year || ''}`);
    return words.every((w) => hay.includes(w));
  });
}

/** Everything a node is connected to in the whole graph, grouped for the details panel. */
export function relations(data, id) {
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  const out = { cites: [], citedBy: [], notes: [], citedInNotes: [], linksTo: [], linkedFrom: [], about: [] };
  const push = (list, nid) => { const n = byId.get(nid); if (n && !list.includes(n)) list.push(n); };
  for (const e of data.edges) {
    if (e.source === id) {
      if (e.kind === 'cites' || e.kind === 'cite') push(out.cites, e.target);
      else if (e.kind === 'link') push(out.linksTo, e.target);
      else if (e.kind === 'about') push(out.about, e.target);
    } else if (e.target === id) {
      if (e.kind === 'cites') push(out.citedBy, e.source);
      else if (e.kind === 'cite') push(out.citedInNotes, e.source);
      else if (e.kind === 'link') push(out.linkedFrom, e.source);
      else if (e.kind === 'about') push(out.notes, e.source);
    }
  }
  return out;
}

/** Short label: "Author Year" for papers and works, the title for notes. */
export function label(node) {
  if (node.kind === 'note') return node.title || node.path;
  const first = String(node.authors || '').split(/;| and /)[0].split(',')[0].trim();
  const who = first || (node.title || '').split(/\s+/).slice(0, 3).join(' ');
  return `${who}${node.year ? ` ${node.year}` : ''}`;
}
