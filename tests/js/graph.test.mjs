import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const { transformSync } = createRequire(new URL('../../frontend/package.json', import.meta.url))('esbuild');
const source = readFileSync(new URL('../../plugins/graph/frontend/model.ts', import.meta.url), 'utf8');
const code = transformSync(source, { loader: 'ts', format: 'esm' }).code;
const { buildView, label, matchNodes, neighbourhood, relations } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));

const P = (id, extra = {}) => ({ id: `item:${id}`, kind: 'paper', title: `Paper ${id}`, authors: 'Doe, Jane', year: 2020, folder_ids: [], ...extra });
const W = (id) => ({ id: `doi:${id}`, kind: 'work', title: `Work ${id}`, authors: 'Ext, Ernie', year: 1999 });
const N = (path, extra = {}) => ({ id: `note:${path}`, kind: 'note', path, title: path.replace('.md', ''), ...extra });
const E = (source, target, kind) => ({ source, target, kind });

const data = {
  nodes: [P('a', { folder_ids: [1] }), P('b', { folder_ids: [2] }), P('c', { authors: 'Bañuls, Mari', citation_key: 'banuls2020' }),
    P('lonely'), W('w1'), W('w2'), N('Review.md'), N('papers/a.md', { paper: 'a2020' }), N('Other.md')],
  edges: [
    E('item:a', 'item:b', 'cites'), E('item:a', 'doi:w1', 'cites'), E('item:b', 'doi:w1', 'cites'), E('item:a', 'doi:w2', 'cites'),
    E('note:papers/a.md', 'item:a', 'about'), E('note:Review.md', 'item:c', 'cite'), E('note:Review.md', 'note:papers/a.md', 'link'),
    E('note:Other.md', 'note:Review.md', 'link'),
  ],
};
const ids = (v) => v.nodes.map((n) => n.id).sort();

test('default view: works cited twice, notes, no isolated nodes', () => {
  const v = buildView(data);
  assert.deepEqual(ids(v), ['doi:w1', 'item:a', 'item:b', 'item:c', 'note:Other.md', 'note:Review.md', 'note:papers/a.md']);
  assert.equal(v.degree.get('item:a'), 3);
  assert.ok(ids(buildView(data, { hideIsolated: false })).includes('item:lonely'));
  assert.ok(ids(buildView(data, { minCited: 1 })).includes('doi:w2'));
});

test('options turn kinds of nodes and edges off', () => {
  assert.deepEqual(ids(buildView(data, { notes: false })), ['doi:w1', 'item:a', 'item:b']);
  assert.ok(!ids(buildView(data, { works: false })).some((i) => i.startsWith('doi:')));
  const v = buildView(data, { edges: { link: false } });
  assert.ok(!v.edges.some((e) => e.kind === 'link'));
  assert.ok(!ids(v).includes('note:Other.md'));        // only connected by a link: now isolated
});

test('folder scope keeps its papers and the notes about them', () => {
  const v = buildView(data, { folders: new Set([1]), minCited: 1 });
  assert.deepEqual(ids(v), ['doi:w1', 'doi:w2', 'item:a', 'note:Review.md', 'note:papers/a.md']);
});

test('focus shows the neighbourhood, with every reference of the focused paper', () => {
  assert.deepEqual(ids(buildView(data, { focus: 'item:a', depth: 1 })), ['doi:w1', 'doi:w2', 'item:a', 'item:b', 'note:papers/a.md']);
  assert.ok(ids(buildView(data, { focus: 'item:a', depth: 2 })).includes('note:Review.md'));
  assert.deepEqual(ids(buildView(data, { focus: 'item:lonely' })), ['item:lonely']);
  assert.deepEqual([...neighbourhood('x', [], 3)], ['x']);
});

test('levels around a note: each step adds what the previous one connects to', () => {
  const opts = (depth) => ({ focus: 'note:Other.md', depth, edges: { cites: false } });
  assert.deepEqual(ids(buildView(data, opts(1))), ['note:Other.md', 'note:Review.md']);
  assert.deepEqual(ids(buildView(data, opts(2))), ['item:c', 'note:Other.md', 'note:Review.md', 'note:papers/a.md']);
  assert.deepEqual(ids(buildView(data, opts(3))), ['item:a', 'item:c', 'note:Other.md', 'note:Review.md', 'note:papers/a.md']);
  assert.equal(buildView(data, opts(3)).edges.length, 4);
});

test('search, relations and labels', () => {
  assert.deepEqual(matchNodes(data.nodes, 'banuls').map((n) => n.id), ['item:c']);
  assert.deepEqual(matchNodes(data.nodes, 'review').map((n) => n.id), ['note:Review.md']);
  assert.deepEqual(matchNodes(data.nodes, '  '), []);
  const r = relations(data, 'item:a');
  assert.deepEqual(r.cites.map((n) => n.id), ['item:b', 'doi:w1', 'doi:w2']);
  assert.deepEqual(r.notes.map((n) => n.id), ['note:papers/a.md']);
  assert.deepEqual(relations(data, 'note:Review.md').linkedFrom.map((n) => n.id), ['note:Other.md']);
  assert.equal(label(P('x', { authors: 'Cui, Jian; Cirac, J. I.', year: 2015 })), 'Cui 2015');
  assert.equal(label(N('ideas/Plan.md', { title: 'Plan' })), 'Plan');
});
