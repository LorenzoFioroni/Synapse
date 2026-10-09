import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { citeLabel, createRenderer, imageSrc, parseCites } from '../../frontend/js/views/notes/render.js';

const r = createRenderer(lib, {
  citeInfo: (k) => (k === 'smith2021' ? { authors: 'Smith, Anna; Doe, J; Roe, K', year: 2021, title: 'Quantum' } : null),
  resolveNote: (t) => (t.toLowerCase() === 'other' ? 'dir/Other.md' : null),
});
const html = (s) => r.render(s);

test('raw HTML is never rendered', () => {
  const out = html('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[x](javascript:alert(1))');
  assert.ok(!out.includes('<script'));
  assert.ok(!out.includes('<img src="x"'));
  assert.ok(!out.includes('href="javascript:'));
  assert.ok(out.includes('&lt;script&gt;'));
});

test('math: inline, display, blocks, currency', () => {
  assert.match(html('a $x^2$ b'), /class="math"><span class="katex"/);
  assert.match(html('$$\\int f$$'), /class="math display"/);
  assert.match(html('$$\nE = mc^2\n$$'), /class="math-block"/);
  const cur = html('costs $5 and $10');
  assert.ok(!cur.includes('katex'), cur);
  assert.ok(!html('a $x\ny$ b').includes('katex'));              // must close on the same line
  assert.ok(!html('`$x$`').includes('katex'));                    // code wins
  assert.ok(html('\\$x$').includes('$x$'));                       // escaped
  assert.match(html('$\\frac{1}{0$'), /katex|math-error/);        // bad TeX never throws
});

test('wiki links, embeds and citations', () => {
  const out = html('See [[Other|that one]] and [[Missing]] and ![[Other]].');
  assert.match(out, /<a class="wikilink" data-note="Other" data-heading="" data-path="dir\/Other.md" href="#">that one<\/a>/);
  assert.match(out, /class="wikilink unresolved" data-note="Missing"/);
  assert.match(out, /class="note-embed" data-note="Other"/);
  const c = html('As shown [@smith2021, p. 4; @ghost] and [see -@smith2021].');
  assert.match(c, /<a class="cite" href="#" data-key="smith2021" data-page="4"[^>]*>Smith et al\. 2021, p\. 4<\/a>/);
  assert.match(c, /class="cite missing" href="#" data-key="ghost"/);
  assert.match(c, />see 2021<\/a>/);
  assert.ok(!html('[link](http://x.org) and [@user](http://x.org)').includes('class="cite'));
  assert.ok(!html('mail [me@x.org]').includes('class="cite'));
});

test('callouts, tasks, images, fences', () => {
  const co = html('> [!warning] Careful\n> body text');
  assert.match(co, /<blockquote class="callout callout-warning"[^>]*>\s*<div class="callout-title">Careful<\/div>/);
  assert.equal((co.match(/body text/g) || []).length, 1);          // not rendered twice
  assert.equal((html('> [!note]\n> one\n> two').match(/one/g) || []).length, 1);
  const tk = html('- [ ] todo\n- [x] done');
  assert.equal((tk.match(/type="checkbox"/g) || []).length, 2);
  assert.match(tk, /checked data-task-line="1"/);
  assert.ok(!tk.includes('[ ]'));
  assert.match(html('![plot|300](attachments/a-b.png)'), /src="\/api\/notes\/attachments\/a-b.png" alt="plot"[^>]* width="300"/);
  assert.match(html('```mermaid\ngraph TD\nA-->B\n```'), /class="mermaid-block"/);
  assert.match(html('```python\nx=1\n```'), /class="code-block"[^>]*data-lang="python"/);
  assert.ok(!html('    indented').includes('<pre'));    // indented code is not a code block
});

test('tags', () => {
  assert.match(html('a #tag and #nested/tag.'), /<span class="tag">#tag<\/span> and <span class="tag">#nested\/tag<\/span>\./);
  assert.ok(!html('x#nottag, [l](#anchor), #123, `#code`').includes('class="tag"'));
});

test('helpers', () => {
  assert.equal(imageSrc('https://x.org/a.png'), 'https://x.org/a.png');
  assert.equal(imageSrc('attachments/x.png'), '/api/notes/attachments/x.png');
  assert.deepEqual(parseCites('see @a, p. 3; @b'), [
    { key: 'a', prefix: 'see', suppress: false, locator: 'p. 3', page: 3 },
    { key: 'b', prefix: '', suppress: false, locator: '', page: null }]);
  assert.equal(citeLabel({ key: 'k', prefix: '', suppress: false, locator: '' }, null), '@k');
});
