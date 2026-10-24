import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';
import { setNoteHook } from '../../frontend/js/views/notes/hooks.js';
import { installSpoilers } from '../../frontend/js/plugins/core/advanced-markdown/markdown.js';
import { spoilerSpans } from '../../frontend/js/plugins/core/advanced-markdown/syntax.js';

const hook = { markdown: installSpoilers, spans: spoilerSpans, exportStatic() {} };
function renderer(t) { setNoteHook('advanced', hook); t.after(() => setNoteHook('advanced', null)); return createRenderer(lib); }

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
  setNoteHook('advanced', null);
  assert.ok(!r.render('||secret||').includes('spoiler-toggle'));
  assert.ok(!r.render(':::spoiler Title\ntext\n:::').includes('<details'));
});
