import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { layoutTag, numberEquations, resolveRefs, splitRows, textRefs } from '../../frontend/js/views/notes/equations.js';
import { clickOffset, texTokens } from '../../frontend/js/views/notes/mathClick.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';

const SRC = [
  'a=b \\label{plain}',
  '\\begin{align} a&=b \\label{r1}\\\\ c&=d \\notag \\\\ e&=f \\end{align}',
  '\\begin{equation}\\begin{split} x&=1\\\\y&=2\\end{split}\\label{sp}\\end{equation}',
  'x \\tag{A.1}\\label{t}',
  'y \\notag',
  '\\begin{gather*} q \\end{gather*}',
];

test('equations: numbered like LaTeX by default', () => {
  const r = numberEquations(SRC);
  assert.deepEqual(r.numbers, [[], ['1', '2'], ['3'], ['A.1'], [], []]);
  assert.deepEqual([...r.labels], [['plain', null], ['r1', '1'], ['sp', '3'], ['t', 'A.1']]);
  assert.deepEqual(r.names, [['plain'], ['r1'], ['sp'], ['t'], [], []]);
  assert.ok(!r.formulas.join('').includes('\\label'));
  assert.match(r.formulas[1], /a&=b\s*\\tag\{1\}\s*\\\\ c&=d \\notag \\\\ e&=f \\tag\{2\}/);
  assert.match(r.formulas[2], /\\end\{split\} \\tag\{3\}\\end\{equation\}/);   // a split inside equation: one number
  assert.equal(r.formulas[4].trim(), 'y');                                     // \notag outside an environment: dropped
});

test('equations: mode "all" numbers every display equation', () => {
  const r = numberEquations(SRC, { mode: 'all' });
  assert.deepEqual(r.numbers, [['1'], ['2', '3'], ['4'], ['A.1'], [], []]);
  assert.equal(r.labels.get('plain'), '1');
  assert.equal(r.labels.get('sp'), '4');
});

test('equations: rows, references and the number column', () => {
  assert.deepEqual(splitRows('a \\\\ \\begin{x} b \\\\ c \\end{x} \\\\ {d \\\\ e} \\\\ f'), ['a ', ' \\begin{x} b \\\\ c \\end{x} ', ' {d \\\\ e} ', ' f']);
  const labels = new Map([['a', '3'], ['n', null]]);
  assert.equal(resolveRefs('x \\eqref{a} y \\ref{ a }', labels), 'x \\text{(3)} y \\text{3}');
  assert.match(resolveRefs('\\eqref{zz} \\eqref{n}', labels), /^\\textcolor\{#cc0000\}\{\\text\{\(\?\?\)\}\} \\textcolor/);
  assert.deepEqual(textRefs('see \\eqref{eq:a} and \\ref{b}'), [{ from: 4, to: 16, kind: 'eqref', name: 'eq:a' }, { from: 21, to: 28, kind: 'ref', name: 'b' }]);
  const html = lib.katex.renderToString('E=mc^2 \\tag{2}', { displayMode: true, throwOnError: false, output: 'htmlAndMathml' });
  const out = layoutTag(html);
  assert.match(out, /<span class="katex-html eq-tagged" aria-hidden="true"><span class="katex-tag eq-ghost" aria-hidden="true">.*<\/span><span class="eq-main">.*<\/span><span class="katex-tag">.*\(.*2.*\).*<\/span><\/span><\/span><\/span>$/);
  assert.equal(layoutTag(lib.katex.renderToString('x', { displayMode: true })), lib.katex.renderToString('x', { displayMode: true }));
});

test('equations: numbered through a rendered note, \\eqref in text and math', () => {
  const r = createRenderer(lib, {});
  const html = r.render('See \\eqref{b} and $\\eqref{a}$, `\\eqref{a}` and \\ref{nope}.\n\n$$\nx \\label{a}\\tag{7}\n$$\n\n$$\\begin{align} y \\label{b}\\\\ z \\end{align}$$\n');
  assert.match(html, /<a class="eqref" href="#eq-b" data-label="b">\(1\)<\/a>/);
  assert.match(html, /<span class="eqref missing"[^>]*>\(\?\?\)<\/span>/);
  assert.match(html, /<code>\\eqref\{a\}<\/code>/);                 // code is left alone
  assert.ok(html.includes('id="eq-a"') && html.includes('id="eq-b"'));
  assert.ok(!/katex-html[^]*\\label/.test(html.replace(/title="[^"]*"/g, '')) && !html.includes('class="eqn-num"'));   // labels removed; KaTeX's own counter never used
  const all = createRenderer(lib, { equations: () => 'all' }).render('$$\na\n$$\n\n$$\nb \\notag\n$$\n\n$$\nc\n$$\n');
  assert.equal((all.match(/class="katex-tag"/g) || []).length, 2);
});

test('clicks into formulas find the typed token', () => {
  const tex = '\\frac{2\\Delta_{ad}}{\\Omega}+\\kappa^2 - \\sin x';
  const drawn = ['Ω', '2', 'Δ', 'a', 'd', '+', 'κ', '2', '−', 's', 'i', 'n', 'x'];   // KaTeX draws the denominator first
  assert.deepEqual(texTokens('a_{b}^2').map((t) => t.ch), ['a', 'b', '2']);
  assert.deepEqual(texTokens('\\mathrm{Re}\\,\\alpha \\label{x}').map((t) => t.ch), ['R', 'e', 'α']);
  const at = (i, after) => { const o = clickOffset(tex, drawn, i, after); return tex.slice(0, o) + '|' + tex.slice(o); };
  assert.equal(at(2), '\\frac{2|\\Delta_{ad}}{\\Omega}+\\kappa^2 - \\sin x');
  assert.equal(at(0), '\\frac{2\\Delta_{ad}}{|\\Omega}+\\kappa^2 - \\sin x');
  assert.equal(at(7, true), '\\frac{2\\Delta_{ad}}{\\Omega}+\\kappa^2| - \\sin x');
  assert.equal(at(8), '\\frac{2\\Delta_{ad}}{\\Omega}+\\kappa^2 |- \\sin x');
  assert.equal(at(12), '\\frac{2\\Delta_{ad}}{\\Omega}+\\kappa^2 - \\sin |x');
  assert.equal(clickOffset('', [], 0), 0);
});
