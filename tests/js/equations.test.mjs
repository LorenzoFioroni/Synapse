import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { layoutTag, numberEquations, resolveRefs, splitRows, textRefs } from '../../frontend/js/views/notes/equations.js';
import { fitEquations } from '../../frontend/js/views/notes/mathFit.js';
import { clickOffset, texTokens } from '../../frontend/js/views/notes/mathClick.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';
import { PHYSICS, baseMacros, parseDefinitions, toMacro, withDefinitions } from '../../frontend/js/views/notes/macros.js';

const SRC = [
  'a=b \\label{plain}',
  '\\begin{align} a&=b \\label{r1}\\\\ c&=d \\notag \\\\ e&=f \\end{align}',
  '\\begin{equation}\\begin{split} x&=1\\\\y&=2\\end{split}\\label{sp}\\end{equation}',
  'x \\tag{A.1}\\label{t}',
  'y \\notag',
  '\\begin{gather*} q \\end{gather*}',
];

test('equations: a label gives a number, environments as in LaTeX', () => {
  const r = numberEquations(SRC);
  assert.deepEqual(r.numbers, [['1'], ['2', '3'], ['4'], ['A.1'], [], []]);
  assert.deepEqual([...r.labels], [['plain', '1'], ['r1', '2'], ['sp', '4'], ['t', 'A.1']]);
  assert.deepEqual(r.names, [['plain'], ['r1'], ['sp'], ['t'], [], []]);
  assert.ok(!r.formulas.join('').includes('\\label'));
  assert.match(r.formulas[0], /^a=b\s*\\tag\{1\}/);
  assert.match(r.formulas[1], /a&=b\s*\\tag\{2\}\s*\\\\ c&=d \\notag \\\\ e&=f \\tag\{3\}/);
  assert.match(r.formulas[2], /\\end\{split\} \\tag\{4\}\\end\{equation\}/);   // a split inside equation: one number
  assert.equal(r.formulas[4].trim(), 'y');                                     // \notag outside an environment: dropped
  // anonymous unless labelled; a labelled row of a starred environment is numbered too
  const s = numberEquations(['x', 'y \\label{y}', '\\begin{align*} p \\\\ q \\label{q} \\end{align*}', 'z \\notag \\label{n}', '  ']);
  assert.deepEqual(s.numbers, [[], ['1'], ['2'], [], []]);
  assert.deepEqual([...s.labels], [['y', '1'], ['q', '2'], ['n', null]]);
});

test('equations: mode "all" numbers every display equation', () => {
  const r = numberEquations(SRC, { mode: 'all' });
  assert.deepEqual(r.numbers, [['1'], ['2', '3'], ['4'], ['A.1'], [], []]);
  assert.equal(r.labels.get('plain'), '1');
  assert.equal(r.labels.get('sp'), '4');
  assert.deepEqual(numberEquations(['a', '', 'b'], { mode: 'all' }).numbers, [['1'], [], ['2']]);   // only definitions: no number
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
  assert.match(html, /<a class="eqref" href="#eq-b" data-kind="eqref" data-label="b">\(1\)<\/a>/);
  assert.match(html, /<span class="eqref missing"[^>]*>\(\?\?\)<\/span>/);
  assert.match(html, /<code>\\eqref\{a\}<\/code>/);                 // code is left alone
  assert.ok(html.includes('id="eq-a"') && html.includes('id="eq-b"'));
  assert.ok(!/katex-html[^]*\\label/.test(html.replace(/title="[^"]*"/g, '')) && !html.includes('class="eqn-num"'));   // labels removed; KaTeX's own counter never used
  const all = createRenderer(lib, { equations: () => 'all' }).render('$$\na\n$$\n\n$$\nb \\notag\n$$\n\n$$\nc\n$$\n');
  assert.equal((all.match(/class="katex-tag"/g) || []).length, 2);
  const lab = createRenderer(lib, {}).render('$$\na\n$$\n\n$$\nb \\label{b}\n$$\n\nSee \\eqref{b}.\n');
  assert.equal((lab.match(/class="katex-tag"/g) || []).length, 1);
  assert.match(lab, /<a class="eqref"[^>]*>\(1\)<\/a>/);
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

const math = (tex, macros) => lib.katex.renderToString(tex, { throwOnError: false, strict: 'ignore', output: 'mathml', macros: { ...macros } });
const ok = (html) => !/katex-error|#cc0000/.test(html);
const shown = (html) => html.replace(/<annotation[^]*?<\/annotation>/g, '').replace(/<[^>]+>/g, '').replace(/\s+/g, '');

test('macros: definitions are read and taken out of the formula', () => {
  const p = parseDefinitions('a \\newcommand{\\x}[2]{#2} b \\renewcommand*\\y{Y} \\providecommand{\\z}{Z} \\def\\w#1#2{#1#2} \\DeclareMathOperator*{\\argmax}{arg\\,max} \\newcommand{\\o}[2][1]{#1+#2} c \\newcommand{bad}');
  assert.deepEqual(p.defs.map((d) => [d.name, d.nargs, d.opt]), [['\\x', 2, null], ['\\y', 0, null], ['\\z', 0, null], ['\\w', 2, null], ['\\argmax', 0, null], ['\\o', 2, '1']]);
  assert.equal(p.rest.replace(/\s+/g, ' '), 'a b c \\newcommand{bad}');   // what is not understood stays (KaTeX shows it)
  assert.equal(parseDefinitions('x + y').rest, 'x + y');
  const m = withDefinitions({}, p.defs);
  assert.equal(shown(math('\\x{A}{B} \\w ab \\y', m)), 'BabY');                // an unused argument is still taken
  assert.equal(shown(math('\\o{x} \\o[2]{y}', m)), '1+x2+y');                   // optional first argument (KaTeX has none)
  assert.ok(ok(math('\\argmax_x f', m)));
  assert.equal(withDefinitions({ '\\z': 'old' }, p.defs)['\\z'], 'old');            // \providecommand keeps an existing one
  assert.equal(typeof toMacro({ name: '\\q', nargs: 1, opt: 'd', body: '#1' }), 'function');
});

test('macros: the physics package', () => {
  const m = baseMacros({ physics: true }).macros;
  for (const [tex, want] of [
    ['\\vb r', 'r'], ['\\qty(\\frac{a}{b}(c))', '(ab(c))'], ['\\qty|y|', '∣y∣'], ['\\abs{x}\\norm{v}', '∣x∣∥v∥'],
    ['\\dv{f}{x}', 'dfdx'], ['\\dv[2]{f}{x}', 'd2fdx2'], ['\\pdv{f}{x}{y}', '∂2f∂x∂y'], ['\\pdv{x}', '∂∂x'],
    ['\\braket{a}{b}', '⟨a|b⟩'], ['\\braket{a|b}', '⟨a∣b⟩'], ['\\expval{A}{\\psi}', '⟨ψ|A|ψ⟩'], ['\\mel{n}{H}{m}', '⟨n|H|m⟩'],
    ['\\comm{A}{B}', '[A,B]'], ['\\mqty(a & b \\\\ c & d)', '(abcd)'], ['\\Tr\\rho', 'Tr⁡ρ'], ['\\div', '÷'],
  ]) {
    const h = math(tex, m);
    assert.ok(ok(h), tex);
    assert.equal(shown(h), want, tex);
  }
  for (const name of Object.keys(PHYSICS)) assert.ok(!ok(math(`${name}{x}{y}{z}`, {})) || name === '\\braket', `${name} is new to KaTeX`);
  assert.ok(!('\\vb' in baseMacros({ physics: false }).macros));
  const own = baseMacros({ physics: false, text: '\\newcommand{\\R}{\\mathbb{R}}\n% a comment\nnot a definition' });
  assert.deepEqual([own.names, own.problems], [['\\R'], ['not a definition']]);
});

test('macros: a note\'s \\newcommand works in all its formulas, read view', () => {
  const r = createRenderer(lib, { macros: () => baseMacros({ physics: true }).macros });
  const html = r.render('Uses $\\E{x}$ before the definition.\n\n$$\n\\newcommand{\\E}[1]{\\langle #1 \\rangle}\n$$\n\n$$\n\\E{\\vb r} \\label{a}\n$$\n');
  assert.ok(ok(html));
  assert.equal((html.match(/class="math-block"/g) || []).length, 1);                // the definitions alone show nothing
  assert.equal((html.match(/class="katex-tag"/g) || []).length, 1);                 // and get no number
  assert.ok(!html.includes('newcommand'));
});

test('equations: fitting remeasures non-scaling padding and retains the scroll fallback', () => {
  function equation(formula, padding, room) {
    let scale = '';
    const main = { get scrollWidth() { return Math.round(formula * Number(scale || 1)); } };
    const tag = { offsetWidth: padding };
    const html = { querySelector: selector => selector.includes('eq-main') ? main : tag };
    const display = {
      style: {
        getPropertyValue: () => scale,
        removeProperty: () => { scale = ''; },
        setProperty: (_, value) => { scale = value; },
      },
      closest: () => ({ clientWidth: room }),
      querySelector: () => html,
      matches: () => true,
    };
    return { display, width: () => main.scrollWidth + tag.offsetWidth, scale: () => Number(scale || 1) };
  }
  const padded = equation(1000, 60, 900);
  assert.equal(fitEquations(padded.display), true);
  assert.ok(padded.width() <= 899, `fitted width: ${padded.width()}`);
  assert.ok(padded.scale() >= 0.75);
  assert.equal(fitEquations(padded.display), false, 'repeated fitting is stable');
  const huge = equation(2000, 60, 900);
  fitEquations(huge.display);
  assert.equal(huge.scale(), 0.75);
  assert.ok(huge.width() > 900, 'very wide equations keep scrolling');
  const small = equation(200, 60, 900);
  assert.equal(fitEquations(small.display), false);
  assert.equal(small.scale(), 1);
});
