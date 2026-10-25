import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';
import { setNoteHook } from '../../frontend/js/views/notes/hooks.js';
import { installSpoilers } from '../../frontend/js/plugins/core/extended-markdown/spoilers/markdown.js';
import { escaped, spoilerSpans } from '../../frontend/js/plugins/core/extended-markdown/spoilers/syntax.js';
import { scan } from '../../frontend/js/views/notes/mdscan.js';

const hook = { markdown: installSpoilers, spoilers: spoilerSpans, exportStatic() {} };
function renderer(t) { setNoteHook('extended', hook); t.after(() => setNoteHook('extended', null)); return createRenderer(lib); }

test('inline spoilers preserve formatting and hide content behind a native button', (t) => {
  const r = renderer(t);
  const html = r.render('before ||**secret** and `code`|| after');
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /class="spoiler-content" hidden=""><strong>secret<\/strong> and <code>code<\/code>/);
  assert.match(html, /before .* after/);
});

test('spoilers ignore code, escaped pipes, unmatched markers and multiline inline content', (t) => {
  const r = renderer(t);
  for (const source of ['`||code||`', '|||triple||', '\\||escaped||', '||unmatched', '||line\nbreak||', '```text\n||code||\n```']) {
    assert.ok(!r.render(source).includes('spoiler-toggle'), source);
    assert.equal(spoilerSpans(source).filter(s => !s.block).length, 0, source);
  }
  assert.match(r.render('||`a||b` **secret**||'), /<code>a\|\|b<\/code> <strong>secret<\/strong>/);
});

test('collapsible blocks support nested Markdown and ignore closing markers inside fences', (t) => {
  const r = renderer(t);
  const source = ':::spoiler **Answer**\n\nSecret $x$.\n\n```text\n:::\n```\n\n:::spoiler Inner\nNested\n:::\n:::';
  const html = r.render(source);
  assert.equal((html.match(/<details /g) || []).length, 2);
  assert.match(html, /<summary><strong>Answer<\/strong><\/summary>/);
  assert.match(html, /class="katex"/);
  assert.match(html, /<code[^>]*class="language-text">:::/);
  assert.equal(spoilerSpans(source).filter(s => s.block).length, 1);
  assert.ok(!r.render(':::spoiler Unclosed\ntext').includes('<details'));
});

test('spoiler HTML stays escaped and disabling restores literal Markdown in retained renderers', (t) => {
  const r = renderer(t);
  assert.match(r.render('||<img src=x onerror=alert(1)>||'), /&lt;img/);
  assert.ok(!r.render(':::spoiler <script>\n<script>evil</script>\n:::').includes('<script>'));
  setNoteHook('extended', null);
  assert.ok(!r.render('||secret||').includes('spoiler-toggle'));
  assert.ok(!r.render(':::spoiler Title\ntext\n:::').includes('<details'));
});

// The rules spelled out position by position: what spoilerSpans must agree with.
function reference(src) {
  const spans = scan(src).spans;
  const inside = (at, list) => list.some((sp) => at >= sp.from && at < sp.to);
  const end = (from) => {
    if (!src.startsWith('||', from) || src[from + 2] === '|' || src[from - 1] === '|' || escaped(src, from)) return -1;
    const lineEnd = src.indexOf('\n', from);
    const max = lineEnd < 0 ? src.length : lineEnd;
    for (let at = from + 2; at < max - 1; at++) {
      if (src.startsWith('||', at) && src[at + 2] !== '|' && src[at - 1] !== '|' && !escaped(src, at) && !inside(at, spans)) {
        return src.slice(from + 2, at).trim() ? at + 2 : -1;
      }
    }
    return -1;
  };
  const out = [];
  const lines = [...src.matchAll(/[^\n]*(?:\n|$)/g)].filter((m) => m[0]);
  for (let i = 0; i < lines.length; i++) {
    const at = lines[i].index;
    if (!/^ {0,3}:::spoiler(?:\s+.*)?$/.test(lines[i][0].trimEnd()) || inside(at, spans)) continue;
    let depth = 1;
    for (let j = i + 1; j < lines.length; j++) {
      if (inside(lines[j].index, spans)) continue;
      const text = lines[j][0].trimEnd();
      if (/^ {0,3}:::spoiler(?:\s+.*)?$/.test(text)) depth++;
      if (/^ {0,3}:::\s*$/.test(text) && --depth === 0) {
        out.push({ from: at, to: lines[j].index + lines[j][0].replace(/\n$/, '').length, block: true });
        i = j; break;
      }
    }
  }
  const blocks = out.slice();
  for (let at = 0; at < src.length - 1; at++) {
    if (inside(at, spans) || inside(at, blocks)) continue;
    const e = end(at);
    if (e > at) { out.push({ from: at, to: e, block: false }); at = e - 1; }
  }
  return out.sort((a, b) => a.from - b.from);
}

test('spoiler ranges match the position-by-position rules on random notes', () => {
  const pieces = ['||', '|', '|||', ' ', 'a', '\\', '\n', '\n\n', '`', '```\n', '$', '$$\n', ':::spoiler', ':::spoiler T',
    ':::', '   ', '<!--', '-->', '**', '[[x]]', 'word '];
  let seed = 7;
  const rand = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
  for (let i = 0; i < 3000; i++) {
    let src = '';
    for (let k = rand(40); k > 0; k--) src += pieces[rand(pieces.length)];
    assert.deepEqual(spoilerSpans(src), reference(src), JSON.stringify(src));
  }
});

test('finding spoilers stays fast in a long note full of formulas', () => {
  const para = 'The field $\\phi(r)$ couples to $\\psi$ via `g` and $E_k = \\hbar\\omega_k$ ||hidden|| [@smith2021].\n\n';
  const src = (para.repeat(3) + '$$\nH = \\sum_k \\epsilon_k a_k^\\dagger a_k\n$$\n\n').repeat(1000);
  const scanned = scan(src);
  assert.ok(src.length > 300_000 && scanned.spans.length > 10_000);
  const t = performance.now();
  const found = spoilerSpans(src, scanned);
  const ms = performance.now() - t;
  assert.equal(found.length, 3000);
  assert.ok(ms < 250, `${ms.toFixed(0)} ms for ${(src.length / 1024).toFixed(0)} KB`);
});
