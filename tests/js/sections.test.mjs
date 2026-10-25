import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { lookupIn, refNames, refParts, refPlain } from '../../frontend/js/views/notes/crossref.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';
import { sectionsOf, takeSectionLabels } from '../../frontend/js/views/notes/sections.js';

test('section labels are outside code and math and headings are numbered hierarchically', () => {
  assert.deepEqual(takeSectionLabels('Methods `\\label{code}` $\\label{math}$ \\label{sec:m}').labels.map((x) => x.name), ['sec:m']);
  assert.deepEqual(takeSectionLabels('Literal \\\\label{escaped}').labels, []);
  const got = sectionsOf('# Intro\n\n## Methods \\label{sec:m}\n\n### Sample \\label{sec:s}');
  assert.deepEqual([...got.labels], [['sec:m', '1.1'], ['sec:s', '1.1.1']]);
});

test('rendered section labels are internal links and cref names sections', () => {
  const html = createRenderer(lib, {}).render('# Intro\n\n## Methods \\label{sec:m}\n\nSee \\ref{sec:m} and \\cref{sec:m}.');
  assert.match(html, /<h2[^>]*><span class="eq-anchor" id="eq-sec-m"><\/span><span class="section-num">1\.1<\/span> Methods<\/h2>/);
  assert.match(html, /href="#eq-sec-m"[^>]*>1\.1<\/a> and <span class="cref"[^>]*>Sec\. <a[^>]*>1\.1<\/a>/);
});

test('cross-reference lookup includes sections after equations and figures', () => {
  const lookup = lookupIn(new Map(), new Map(), new Map([['sec:a', '2.1']]));
  assert.equal(refPlain(refParts('cref', refNames('cref', 'sec:a'), lookup)).replace(/ /g, ' '), 'Sec. 2.1');
  assert.equal(refPlain(refParts('Cref', refNames('Cref', 'sec:a'), lookup)).replace(/ /g, ' '), 'Section 2.1');
});
