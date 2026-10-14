// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { embedHtml, section } from '../../frontend/js/views/notes/embed.js';
import { linkDataOf, normTarget } from '../../frontend/js/views/notes/linkData.js';

const link = (kind, target, extra = {}) => ({ kind, target, ...extra });
const NOTE = '---\ntitle: B\n---\n# B\nAs shown [@smith2021, p. 4].\n\n![[C]]\n\nSee [[D]] and [[#Part]].\n\n## Part\nInner [@ghost].\n';
// GET /api/notes/links for B.md: what B links to, resolved by the backend
const LINKS = { outgoing: [
  link('cite', 'smith2021', { item: { title: 'Quantum', authors: 'Smith, Anna', year: 2021 } }),
  link('embed', 'C', { resolved: 'dir/C.md' }),
  link('note', 'D', { resolved: null }),
  link('cite', 'ghost', { item: null }),
] };

test('an embedded note renders with its own links: citations, links and nested embeds', () => {
  const html = embedHtml(lib, 'B.md', NOTE, undefined, LINKS);
  assert.match(html, /<a class="cite" href="#" data-key="smith2021" data-page="4"[^>]*>Smith 2021, p\. 4<\/a>/);
  assert.match(html, /class="cite missing" href="#" data-key="ghost"/);
  assert.match(html, /class="note-embed" data-note="C" data-heading="" data-path="dir\/C.md"/);   // so the depth limit applies to it
  assert.match(html, /class="wikilink unresolved" data-note="D"/);
  assert.match(html, /class="wikilink" data-note="" data-heading="Part" data-path="B.md"/);         // [[#Part]] is B itself
  assert.ok(!html.includes('title: B'));                                                            // no front matter
});

test('what the embedded note\'s links do not say comes from the embedding note', () => {
  const opts = {
    citeInfo: (k) => (k === 'ghost' ? { title: 'Ghost', authors: 'Roe, K', year: 1999 } : null),
    resolveNote: (t) => (normTarget(t) === 'c' ? 'C.md' : undefined),
  };
  const html = embedHtml(lib, 'B.md', NOTE, undefined, null, opts);   // its links could not be loaded
  assert.match(html, /class="cite" href="#" data-key="ghost"[^>]*>Roe 1999<\/a>/);
  assert.match(html, /class="cite missing" href="#" data-key="smith2021"/);
  assert.match(html, /data-note="C" data-heading="" data-path="C.md"/);
  const own = embedHtml(lib, 'B.md', NOTE, undefined, LINKS, opts);
  assert.match(own, /data-key="smith2021"[^>]*>Smith 2021, p\. 4</);                                // its own data wins
  assert.match(own, /data-key="ghost"[^>]*>Roe 1999</);
});

test('a heading embeds just that section', () => {
  const html = embedHtml(lib, 'B.md', NOTE, 'Part', LINKS);
  assert.match(html, /Inner/);
  assert.ok(!html.includes('As shown'));
  assert.equal(section('# A\nx\n## B\ny\n# C\nz', 'B'), '## B\ny');
  assert.match(section('# A', 'Nope'), /not found/);
});

test('linkDataOf maps targets the way resolveNote looks them up', () => {
  const d = linkDataOf({ outgoing: [link('note', 'Dir\\Other.md', { resolved: 'Dir/Other.md' }), link('image', 'x.png')] });
  assert.deepEqual([...d.notes], [['dir/other', 'Dir/Other.md']]);
  assert.equal(d.cites.size, 0);
});
