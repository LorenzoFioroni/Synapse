// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import {
  endsSentence, figureCaption, figureColumns, galleryInfo, galleryMarkdown, panelLabel, parseGallery,
} from '../../frontend/js/views/notes/gallery.js';
import { portableBody } from '../../frontend/js/views/notes/portable.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';

test('info string: gallery and its width', () => {
  assert.deepEqual(galleryInfo('gallery'), { width: null });
  assert.deepEqual(galleryInfo('gallery|600'), { width: 600 });
  assert.deepEqual(galleryInfo(' Gallery 450px '), { width: 450 });
  assert.deepEqual(galleryInfo('gallery width=300'), { width: 300 });
  assert.deepEqual(galleryInfo('gallery|0'), { width: null });
  assert.equal(galleryInfo('gallery2'), null);
  assert.equal(galleryInfo('python'), null);
  assert.equal(galleryInfo(''), null);
});

test('panels, their captions and the gallery caption', () => {
  const g = parseGallery([
    'Shown first.',
    '![first|300](attachments/a.png)',
    '*Clean system, $x$.*',
    '![](b.png) With speckle',
    'and a second line.',
    '',
    'The gallery caption [@k].',
    '![c](<c.png>)',
    '![](e.png) ![](f.png) Two at once.',
  ].join('\n'), 'gallery|600');
  assert.equal(g.width, 600);
  assert.equal(g.caption, 'Shown first.\nThe gallery caption [@k].');
  assert.equal(g.captionLine, 0);
  assert.deepEqual(g.panels.map((p) => [p.src, p.alt, p.width, p.caption, p.line]), [
    ['attachments/a.png', 'first', 300, 'Clean system, $x$.', 1],
    ['b.png', '', null, 'With speckle\nand a second line.', 3],
    ['c.png', 'c', null, '', 7],
    ['e.png', '', null, '', 8],
    ['f.png', '', null, 'Two at once.', 8],
  ]);
});

test('only a caption wrapped as a whole is unwrapped', () => {
  const cap = (c) => parseGallery(`![](a.png)\n${c}`).panels[0].caption;
  assert.equal(cap('*Whole.*'), 'Whole.');
  assert.equal(cap('_Whole._'), 'Whole.');
  assert.equal(cap('*one* and *two*'), '*one* and *two*');
  assert.equal(cap('**Bold.**'), '**Bold.**');
  assert.equal(parseGallery('').panels.length, 0);
});

test('labels, columns and the figure caption', () => {
  assert.deepEqual([0, 1, 25, 26].map(panelLabel), ['a', 'b', 'z', '27']);
  assert.deepEqual([1, 2, 3, 4, 5, 6, 7].map(figureColumns), [1, 2, 3, 2, 3, 3, 3]);
  assert.ok(endsSentence('Done.') && endsSentence('Really?)') && endsSentence('“Quoted.”'));
  assert.ok(!endsSentence('Dotted: $N^{3/2}$') && !endsSentence('e.g'));
  assert.equal(figureCaption('Photon number', ['Clean.', '', 'Speckle, $\\xi=1$']),
    'Photon number. (a) Clean. (c) Speckle, $\\xi=1$.');
  assert.equal(figureCaption('', ['Only one']), 'Only one.');
  assert.equal(figureCaption('', ['', '']), '');
});

test('portable Markdown: images on one line, the caption below', () => {
  assert.equal(galleryMarkdown(parseGallery('![x|300](a.png)\n*A*\n![](b.png) B\n\nMain')),
    '![x](a.png) ![](b.png)\n\n*Main. (a) A. (b) B.*');
  assert.equal(galleryMarkdown(parseGallery('![](a.png)')), '![](a.png)');
  const out = portableBody('Text.\n\n```gallery|500\n![](a.png)\n*Cited [@k, p. 2]*\n![](b.png)\n```\nAfter.\n', {
    citeInfo: (k) => (k === 'k' ? { authors: 'Smith, A', year: 2021 } : null),
  });
  assert.equal(out.markdown, 'Text.\n\n![](a.png) ![](b.png)\n\n**Fig. 1.** *(a) Cited (Smith 2021, p. 2).*\nAfter.\n');
  assert.deepEqual(out.keys, ['k']);
});

test('rendered as a carousel, captions parsed as Markdown', () => {
  const r = createRenderer(lib, { citeInfo: (k) => (k === 'k' ? { authors: 'Smith, A', year: 2021 } : null) });
  const html = r.render('```gallery|600\n![a|300](attachments/a.png)\n*Panel $x$ [@k]*\n![](b.png)\n\nMain \\eqref{eq:1}.\n```\n\n$$y \\label{eq:1}$$\n');
  assert.match(html, /<figure class="gallery" data-line="0" data-count="2" data-fig="1" style="max-width:600px">/);
  assert.match(html, /<div class="gallery-panel" data-line="1" data-index="0"><div class="gallery-image"><img src="\/api\/notes\/attachments\/a.png" alt="a" loading="lazy" width="300">/);
  assert.match(html, /<span class="gallery-label">\(a\)<\/span> Panel <span class="math">/);
  assert.match(html, /class="cite"[^>]*data-key="k"[^>]*>Smith 2021</);
  assert.match(html, /<figcaption class="gallery-caption" data-line="5"><span class="fig-num">Fig. 1.<\/span> Main <a class="eqref"[^>]*>\(1\)<\/a>\.<\/figcaption><\/figure>/);
  assert.equal((html.match(/class="gallery-dot[ "]/g) || []).length, 2);
  assert.match(html, /gallery-prev" aria-label="Previous image" disabled/);
  assert.ok(!html.includes('<pre'), 'not a code block');
  // one image: no labels or controls; none: a hint
  const one = r.render('```gallery\n![](a.png) Only\n```');
  assert.ok(!one.includes('gallery-label') && !one.includes('gallery-btn'), one);
  assert.match(r.render('```gallery\n```'), /gallery-empty/);
  // raw HTML in a caption stays text
  assert.ok(!r.render('```gallery\n![](a.png) <img src=x onerror=alert(1)>\n```').includes('<img src="x"'));
});
