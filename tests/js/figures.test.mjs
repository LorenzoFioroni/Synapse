// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { figuresOf, imageFigureAt } from '../../frontend/js/views/notes/figures.js';
import { takeLabels, wholeEmphasis } from '../../frontend/js/views/notes/gallery.js';
import { portableBody } from '../../frontend/js/views/notes/portable.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';

const NOTE = [
  'See \\cref{fig:a}, \\cref{fig:b} and \\eqref{eq:x}.',  // 0
  '',
  '![first|300](attachments/a.png)',                                // 2: Fig. 1
  '*Caption with $\\alpha^*$ math. \\label{fig:a}*',
  '',
  '![](attachments/nocap.png)',                                     // 5: no caption, no number
  '',
  'Text right above',
  '![](attachments/glued.png)',                                     // 8: not alone: not a figure
  '*Not a caption*',
  '',
  '```gallery',                                                     // 11: Fig. 2 (panel captions)
  '![](b.png) First \\label{fig:b}',
  '![](c.png) Second',
  '```',
  '',
  '```gallery',                                                     // 16: no caption, no number
  '![](d.png)',
  '```',
  '',
  '```gallery',                                                     // 20: Fig. 3 (main caption)
  '![](e.png) Panel',
  '![](f.png)',
  '',
  'The main caption.',
  '```',
  '',
  '$$x \\label{eq:x}$$',
  '',
  '```',
  '![](code.png)',
  '*In code*',
  '```',
].join('\n');

test('captions: whole emphasis, labels outside math', () => {
  assert.equal(wholeEmphasis('*A $a^*$ b.*'), 'A $a^*$ b.');
  assert.equal(wholeEmphasis('*a* and *b*'), null);
  assert.equal(wholeEmphasis('plain'), null);
  assert.deepEqual(takeLabels('Text \\label{fig:a} more $\\label{no}$'), { text: 'Text more $\\label{no}$', labels: ['fig:a'] });
});

test('an image is a figure only with a caption line and blank lines around', () => {
  const lines = NOTE.split('\n');
  assert.deepEqual(imageFigureAt(lines, 2), { src: 'attachments/a.png', alt: 'first', width: 300, caption: 'Caption with $\\alpha^*$ math.', labels: ['fig:a'] });
  assert.equal(imageFigureAt(lines, 5), null);
  assert.equal(imageFigureAt(lines, 8), null);
  assert.equal(imageFigureAt(['![](a.png)', '*Cap*', 'more text'], 0), null);
  assert.ok(imageFigureAt(['![](a.png)', '_Cap_'], 0));
});

test('figures are numbered in order, galleries only with a caption', () => {
  const { figures, labels } = figuresOf(`---\ntitle: x\n---\n${NOTE}`);
  assert.deepEqual(figures.map((f) => [f.kind, f.number]), [['image', 1], ['gallery', 2], ['gallery', null], ['gallery', 3]]);
  assert.deepEqual([...labels], [['fig:a', '1'], ['fig:b', '2(a)']]);   // fig:b names a panel
});

test('rendered: numbers before captions, \\ref resolved, same numbers as the text', () => {
  const html = createRenderer(lib, {}).render(NOTE);
  const nums = [...html.matchAll(/<span class="fig-num">([^<]*)<\/span>/g)].map((m) => m[1]);
  assert.deepEqual(nums, ['Fig. 1.', 'Fig. 2', 'Fig. 2', 'Fig. 3.']);
  assert.match(html, /<figure class="figure" data-line="2" data-fig="1"><span class="eq-anchor" id="eq-fig-a"><\/span><div class="figure-image"><img src="\/api\/notes\/attachments\/a.png" alt="first" loading="lazy" width="300"><\/div><figcaption class="figure-caption"><span class="fig-num">Fig. 1.<\/span> Caption with <span class="math">/);
  assert.ok(!html.includes('\\label'));
  assert.match(html, /See <span class="cref" data-kind="cref" data-names="fig:a">Fig.\u00a0<a class="eqref" href="#eq-fig-a" data-kind="cref" data-label="fig:a">1<\/a><\/span>, <span class="cref"[^>]*>Fig.\u00a0<a class="eqref" href="#eq-fig-b"[^>]*>2\(a\)<\/a><\/span> and <a class="eqref"[^>]*>\(1\)<\/a>/);
  // the panel's anchor is on the panel
  assert.match(html, /<div class="gallery-panel" data-line="12" data-index="0"><span class="eq-anchor" id="eq-fig-b"><\/span>/);
  // a gallery without its own caption: the panels carry the number, "Fig. 2(a)"
  assert.match(html, /<span class="gallery-label"><span class="fig-num">Fig. 2<\/span>\(a\)<\/span> First/);
  // with its own caption: the caption carries it, the panels only their letter
  assert.match(html, /<span class="gallery-label">\(a\)<\/span> Panel/);
  assert.match(html, /<figcaption class="gallery-caption" data-line="24"><span class="fig-num">Fig. 3.<\/span> The main caption.<\/figcaption>/);
  assert.equal((html.match(/data-fig="/g) || []).length, 3);
  assert.match(html, /<p data-line="5"><img src="\/api\/notes\/attachments\/nocap.png"/);
  assert.match(html, /<em>Not a caption<\/em>/);
  // a one-image gallery whose only caption is the panel's: "Fig. 1." before it
  assert.match(createRenderer(lib, {}).render('```gallery\n![](a.png) Alone\n```'), /<span class="gallery-caption-text"><span class="fig-num">Fig. 1.<\/span> Alone/);
});

test('Markdown export: numbered captions, \\ref as numbers', () => {
  const md = portableBody(NOTE, { citeInfo: () => null }).markdown;
  assert.ok(md.startsWith('See Fig.\u00a01, Fig.\u00a02(a) and \\eqref{eq:x}.'), md.slice(0, 80));
  assert.ok(md.includes('![first](attachments/a.png)\n**Fig. 1.** *Caption with $\\alpha^*$ math.*\n'), md);
  assert.ok(md.includes('![](b.png) ![](c.png)\n\n**Fig. 2.** *(a) First. (b) Second.*'));
  assert.ok(md.includes('![](e.png) ![](f.png)\n\n**Fig. 3.** *The main caption. (a) Panel.*'));
  assert.ok(md.includes('![](d.png)\n') && !md.includes('**Fig. 4'));
  assert.ok(md.includes('![](attachments/glued.png)\n*Not a caption*'));
  assert.ok(md.includes('```\n![](code.png)\n*In code*\n```'));
});
