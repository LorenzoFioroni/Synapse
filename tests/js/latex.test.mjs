import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';
import { splitFrontmatter } from '../../frontend/js/views/notes/mdscan.js';
import { section } from '../../frontend/js/views/notes/embed.js';
import { setNoteHook } from '../../frontend/js/views/notes/hooks.js';
import { pluginModule } from './pluginModule.mjs';
const { latexProject, noteResolver, texEscape } = await pluginModule('export-formats/frontend/latex.ts');
const { displayMath } = await pluginModule('export-formats/frontend/texMath.ts');
const { mathUnicode, unicodePreamble } = await pluginModule('export-formats/frontend/texText.ts');
const { figuresHook } = await pluginModule('extended-markdown/frontend/figures/hook.ts');
setNoteHook('figures', figuresHook);

const PNG = readFileSync(new URL('../e2e/fixtures/wide.png', import.meta.url));

function inputs(notes, extra = {}) {
  const renderer = createRenderer(lib);
  renderer.md.core.ruler.disable('equations');
  return {
    parse: text => renderer.md.parse(text, {}),
    body: text => text.slice(splitFrontmatter(text).bodyStart),
    section, resolve: noteResolver(Object.keys(notes)),
    read: async path => { assert.ok(notes[path], path); return { path, title: path, text: notes[path] }; },
    image: async () => ({ bytes: PNG, extension: 'png' }),
    bibliography: async keys => ({ text: keys.map(key => `@article{${key}, title={Title}, author={Žnidarič, Marko}, journal={J}, year={2020}}`).join('\n'), missing: [] }),
    ...extra,
  };
}
const exportMain = async (text, extra = {}, notes = {}) => (await latexProject({ path: 'root.md', title: 'Title', text }, false, inputs(notes, extra))).files.get('main.tex');
const documentBody = main => main.slice(main.indexOf('\\begin{document}'));

test('single-file project keeps math/citations and embeds while packaging figures and bibliography', async () => {
  const notes = { 'part.md': '# Part\n\nEmbedded [@other]. ![x](attachments/x.png)\n', 'skip.md': 'Do not export.' };
  const project = await latexProject({ path: 'root.md', title: 'Müller & 100%', text: '---\ntitle: Private metadata\n---\n# Root\n\n**Bold** and ++under++ [see @key, p. 4]. [[skip]]\n\n![[part]]\n\n$$\nx_1 = 2 \\label{eq:x}\n$$\n\nSee \\eqref{eq:x}.\n\n`[@code]`\n\n```python\nprint("x")\n```' }, false, inputs(notes));
  const main = project.files.get('main.tex');
  assert.equal([...project.files.keys()].filter(n => n.endsWith('.tex')).length, 1);
  assert.equal([...project.files.keys()][0], 'main.tex');
  assert.match(main, /\\title\{Müller \\& 100\\%\}/);
  assert.match(main, /\\textbf\{Bold\}/);
  assert.match(main, /\\uline\{under\}~\\cite\[see\]\[p\.~4\]\{key\}/);
  assert.match(main, /Embedded~\\cite\{other\}/);
  assert.match(main, /\\includegraphics[^\n]+\{figures\/figure-1.png\}/);
  assert.match(main, /\\begin\{equation\}\nx_1 = 2\n\\label\{eq:x\}\n\\end\{equation\}/);
  assert.ok(main.includes('See~\\eqref{eq:x}'));
  assert.match(main, /\\begin\{verbatim\}\nprint\("x"\)\n\\end\{verbatim\}/);
  assert.ok(!main.includes('Private metadata') && !main.includes('Do not export'));
  assert.ok(!main.includes('autocite') && !main.includes('fontspec'));
  assert.match(main, /\\usepackage\[T1\]\{fontenc\}/);
  assert.match(main, /style=numeric-comp/);
  assert.match(project.files.get('references.bib'), /@article\{key/);
  assert.match(project.files.get('references.bib'), /@article\{other/);
  assert.ok(!project.files.get('references.bib').includes('code'));
  assert.match(project.files.get('README.txt'), /latexmk -pdf main\.tex/);
});

test('linked project recursively exports each reachable note once and namespaces repeated labels', async () => {
  const notes = {
    'root.md': '[[a]] [[a#Method|again]]',
    'a.md': '## Method \\label{sec:m}\n\n![[b]] [[root]] [@a]',
    'b.md': '## Method \\label{sec:m}\n\n[[a]] [@b]',
    'unrelated.md': 'Never exported.',
  };
  const reads = [];
  const deps = inputs(notes);
  const project = await latexProject({ path: 'root.md', title: 'Root', text: notes['root.md'] }, true, { ...deps, read: async path => { reads.push(path); return deps.read(path); } });
  assert.deepEqual(reads, ['a.md', 'b.md']);
  const tex = [...project.files].filter(([path]) => path.endsWith('.tex'));
  assert.equal(tex.length, 3);
  const main = project.files.get('main.tex');
  assert.equal((main.match(/\\input\{/g) || []).length, 2);
  assert.match(main, /\\appendix\n\\input/);
  assert.ok(!main.includes('Never exported'));
  const files = tex.filter(([path]) => path.startsWith('notes/'));
  assert.match(files[0][1], /^\\section\{a\.md\}/);
  assert.match(files[0][1], /\\subsection\{Method\}/);   // headings sit below the note's own section
  const labels = files.map(([, text]) => /\\label\{([^}]*sec:m)\}/.exec(text)[1]);
  assert.notEqual(labels[0], labels[1]);
  const destinations = new Set(tex.flatMap(([, text]) => [...text.matchAll(/\\label\{([^}]+)\}/g)].map(m => m[1])));
  for (const [, text] of tex) for (const ref of text.matchAll(/\\hyperref\[([^\]]+)\]/g)) assert.ok(destinations.has(ref[1]), ref[1]);
  assert.match(project.files.get('references.bib'), /@article\{a/);
  assert.match(project.files.get('references.bib'), /@article\{b/);
});

test('the title heading becomes \\title and the levels below it the sections', async () => {
  const main = await exportMain('# My note\n\nIntro.\n\n## First\n\n### Inner $\\hat{\\mathbf 1}$?\n\n#### Deep\n\n## Second');
  assert.match(main, /\\title\{My note\}/);
  assert.equal((main.match(/My note/g) || []).length, 1);
  assert.match(main, /\\section\{First\}/);
  assert.match(main, /\\subsection\{Inner \\texorpdfstring\{\$\\hat\{\\mathbf 1\}\$\}\{1\}\?\}/);
  assert.match(main, /\\subsubsection\{Deep\}/);
  assert.match(main, /\\section\{Second\}/);
  // without a title heading, the note's title is used and its headings start at \section
  const plain = await exportMain('### Only');
  assert.match(plain, /\\title\{Title\}/);
  assert.match(plain, /\\section\{Only\}/);
});

test('display math: align nests as aligned, numbers follow the note, line breaks get an environment', async () => {
  // the note's own example: align inside $$ with the label after it
  const nested = displayMath('\\begin{align}\na = b + \\\\\nc.\n\\end{align}\n\\label{eq:at}', false);
  assert.equal(nested.tex, '\\begin{equation}\n\\begin{aligned}\na = b + \\\\\nc.\n\\end{aligned}\n\\label{eq:at}\n\\end{equation}');
  assert.equal(displayMath('x = 1', false).tex, '\\[\nx = 1\n\\]');
  assert.equal(displayMath('x = 1', true).tex, '\\begin{equation}\nx = 1\n\\end{equation}');
  assert.equal(displayMath('x = 1 \\notag', true).tex, '\\[\nx = 1\n\\]');
  assert.equal(displayMath('x = 1 \\tag{A}', false).tex, '\\begin{equation}\nx = 1\n\\tag{A}\n\\end{equation}');
  // numbered environments stay at the top, numbered row by row as in LaTeX
  assert.equal(displayMath('\\begin{align*}a &= b \\label{r}\\\\ c &= d\\end{align*}', false).tex, '\\begin{align*}a &= b \\label{r}\\\\ c &= d\\end{align*}');
  assert.equal(displayMath('\\begin{equation*}\\begin{gather}a\\\\b\\end{gather}\\end{equation*}', true).tex, '\\[\n\\begin{gathered}a\\\\b\\end{gathered}\n\\]');
  // KaTeX breaks lines anywhere; LaTeX needs an environment
  assert.equal(displayMath('a &= b \\\\ c &= d', false).tex, '\\[\n\\begin{aligned}\na &= b \\\\ c &= d\n\\end{aligned}\n\\]');
  assert.equal(displayMath('a \\\\ b', false).tex, '\\[\n\\begin{gathered}\na \\\\ b\n\\end{gathered}\n\\]');
  // one number, one label: the others refer to it
  const two = displayMath('x \\label{a} \\label{b}', false);
  assert.equal(two.tex, '\\begin{equation}\nx\n\\label{a}\n\\end{equation}');
  assert.deepEqual(two.aliases, [['b', 'a']]);
  const main = await exportMain('See \\eqref{b}.\n\n$$\nx \\label{a} \\label{b}\n$$', { math: { physics: false, macros: '', numbering: 'labelled' } });
  assert.ok(main.includes('See~\\eqref{a}.'));
  assert.equal(mathUnicode('α ≤ β² \\text{für α}'), '\\alpha \\leq \\beta^{2} \\text{für α}');
});

test('a display formula after a line of text belongs to its paragraph', async () => {
  const main = documentBody(await exportMain('Text reads\n$$\nx = 1\n$$\nand goes on.\n\nNew paragraph.\n\n$$\ny\n$$\n\nAfter a blank line.'));
  assert.ok(main.includes('Text reads\n\\[\nx = 1\n\\]\nand goes on.\n\nNew paragraph.\n\\[\ny\n\\]\n\nAfter a blank line.'), main);
  // a block that only defines macros shows nothing and joins nothing
  const macros = documentBody(await exportMain('First.\n$$\n\\newcommand{\\x}{y}\n$$\n\nSecond.'));
  assert.ok(macros.includes('First.\n\nSecond.'), macros);
});

test('citation groups become one \\cite; locators and prefixes keep theirs', async () => {
  const main = documentBody(await exportMain('Spin chains [@a; @b; -@c] and [@d, p. 2; @e; see @f, pp. 3-4, att:7].'));
  assert.ok(main.includes('Spin chains~\\cite{a,b,c} and~\\cite[p.~2]{d}, \\cite{e}, \\cite[see][pp.~3-4]{f}.'), main);
});

test('galleries: one caption with the panels\' in order, centred panels, chosen columns', async () => {
  const text = 'Before.\n\n```gallery\n![](attachments/a.png)\nFirst panel $x$\n![](attachments/b.png)\n*Second. \\label{fig:b}*\n![](attachments/c.png)\n\nMain caption [@k]\n\\label{fig:g}\n```\n\nSee \\cref{fig:b}.';
  const main = await exportMain(text);
  assert.equal((main.match(/\\caption\{/g) || []).length, 4);   // three bare panel letters and the figure's
  assert.match(main, /\\caption\{Main caption~\\cite\{k\}\. \(a\) First panel \$x\$\. \(b\) Second\.\}\\label\{fig:g\}/);
  assert.match(main, /\\caption\{\}\\label\{fig:b\}/);
  assert.equal((main.match(/\\begin\{subfigure\}\[t\]\{0\.318\\linewidth\}\n\\centering\n/g) || []).length, 3);
  assert.equal((main.match(/\\par\\medskip/g) || []).length, 0);
  const two = await exportMain(text, { galleryColumns: 2 });
  assert.equal((two.match(/\\begin\{subfigure\}\[t\]\{0\.488\\linewidth\}/g) || []).length, 3);
  assert.equal((two.match(/\\par\\medskip/g) || []).length, 1);
  // four panels: a 2×2 grid, as the HTML export
  const four = await exportMain('```gallery\n![](a.png)\n![](b.png)\n![](c.png)\n![](d.png)\n\nFour.\n```');
  assert.equal((four.match(/0\.488\\linewidth/g) || []).length, 4);
  // one image: no letters; no caption: no float and no number
  const single = documentBody(await exportMain('```gallery\n![|350](a.png)\n\nOnly one \\label{fig:one}\n```'));
  assert.ok(!single.includes('subfigure') && single.includes('width=0.500\\linewidth') && single.includes('\\caption{Only one.}\\label{fig:one}'));
  const bare = documentBody(await exportMain('```gallery\n![](a.png)\n![](b.png)\n```'));
  assert.ok(bare.includes('\\begin{center}') && !bare.includes('\\caption') && !bare.includes('{figure}'));
});

test('tables are framed, captioned from the italic line below them, and end their paragraph', async () => {
  const text = 'Text.\n\n| A | B | C |\n|---|:-:|--:|\n| $x$ | 2 | 3 |\n\n*The caption. \\label{tab:t}*\n\nAfter the table.\n\n| P | Q |\n|---|---|\n| 1 | 2 |\n\nNot a *caption*.';
  const main = documentBody(await exportMain(text));
  assert.ok(main.includes('\\begin{table}[htbp]\n\\centering\n\\caption{The caption.}\\label{tab:t}\n'), main);
  assert.ok(main.includes('\\begin{tabular}{|l|c|r|}\n\\hline\n\\textbf{A} & \\textbf{B} & \\textbf{C} \\\\\n\\hline\n$x$ & 2 & 3 \\\\\n\\hline\n\\end{tabular}}'));
  assert.ok(main.includes('\\end{table}\n\nAfter the table.'));
  assert.ok(!main.includes('\\emph{The caption'));
  // a table without a caption stays in place, centred, and the text after it is a new paragraph
  assert.ok(main.includes('\\end{tabular}}\n\\end{center}\n\nNot a \\emph{caption}.'), main);
  assert.equal((main.match(/\\caption/g) || []).length, 1);
});

test('code keeps box drawing and tabs; Unicode pdfLaTeX lacks is declared', async () => {
  const project = await latexProject({ path: 'root.md', title: 'Title', text: 'Spin ↔ flip ≈ 2 😀 Žnidarič.\n\n```\n a1 ── XX ╭─\n │\tx\n```\n\n$α ≤ 1$' }, false, inputs({}));
  const main = project.files.get('main.tex');
  assert.match(main, /\\usepackage\{pmboxdraw\}/);
  assert.match(main, /\\newunicodechar\{↔\}\{\\ensuremath\{\\leftrightarrow\}\}/);
  assert.match(main, /\\newunicodechar\{╭\}\{┌\}/);
  assert.match(main, /\\newunicodechar\{😀\}\{\{\\ttfamily\[U\+1F600\]\}\}/);
  assert.ok(!main.includes('\\newunicodechar{Ž}') && !main.includes('\\newunicodechar{─}'));
  assert.ok(main.includes('\\begin{verbatim}\n a1 ── XX ╭─\n │  x\n\\end{verbatim}'));
  assert.ok(main.includes('$\\alpha \\leq 1$'));
  assert.ok(project.warnings.some(w => w.includes('😀')));
  assert.deepEqual(unicodePreamble(['plain ascii, café']), { preamble: '', missing: [] });
});

test('figures, lists, quotes and missing content remain portable', async () => {
  const text = '# Heading\n\n![Alt|350](attachments/a.png)\n*Caption [@key]. \\label{fig:a}*\n\n- [x] Done\n- [ ] Open\n- [link](https://example.org) item\n\n3. three\n4. four\n\n> [!warning]\n> Careful.\n\n```text\n\\end{verbatim}\n```\n\nline  \n[x] after a break\n\n[[missing]]\n\n![Remote](https://example.org/image.png)';
  const project = await latexProject({ path: 'a.md', title: 'Title', text }, false, inputs({}, {
    image: async source => source.startsWith('https:') ? null : ({ bytes: PNG, extension: 'png' }),
    bibliography: async () => ({ text: '', missing: ['key'] }),
  }));
  const main = project.files.get('main.tex');
  assert.match(main, /\\begin\{figure\}\[htbp\]\n\\centering\n\\includegraphics\[width=0\.500\\linewidth/);
  assert.match(main, /\\caption\{Caption~\\cite\{key\}\.\}\\label\{fig:a\}/);
  assert.match(main, /\\item\[\$\\boxtimes\$\] Done\n\\item\[\$\\square\$\] Open\n/);
  assert.match(main, /\\begin\{enumerate\}\n\\setcounter\{enumi\}\{2\}/);
  assert.match(main, /\\begin\{quote\}\n\\textbf\{Warning\}\\par\nCareful\./);
  assert.match(main, /line\\newline\n\[x\] after a break/);
  assert.ok(!main.includes('https://example.org/image.png'));
  assert.equal(project.warnings.length, 3);
  assert.ok(project.files.get('README.txt').includes('Reference not in the library: key'));
  assert.equal(texEscape('a\\b{c}_$%&#^~'), 'a\\textbackslash{}b\\{c\\}\\_\\$\\%\\&\\#\\textasciicircum{}\\textasciitilde{}');
  assert.equal(texEscape('x << y,, ?`'), 'x <{}< y,{}, ?{}`');
});

test('resolver follows path suffixes, case and deterministic shortest-path order', () => {
  const resolve = noteResolver(['deep/A.md', 'b/A.md', 'A.md', 'dir/B.md']);
  assert.equal(resolve('a'), 'A.md');
  assert.equal(resolve('b/A'), 'b/A.md');
  assert.equal(resolve('DIR/b.md'), 'dir/B.md');
  assert.equal(resolve('missing'), null);
});

test('footnotes, checkboxes, macros and repeated embeds retain their meaning', async () => {
  const notes = { 'part.md': '# Part\n\n$$x=1 \\label{eq:x}$$\n\nSee \\eqref{eq:x}.' };
  const project = await latexProject({ path: 'root.md', title: 'Title', text: 'Text[^n] ++under++.\n\n- [x] Done\n- [ ] Open\n\n$\\custom{x}$ before $\\newcommand{\\custom}[1]{#1^2}$.\n\n![[part]]\n\n![[part]]\n\n[^n]: Footnote [@key].' }, false, inputs(notes, { math: { physics: true, macros: '\\newcommand{\\setting}{S}' } }));
  const main = project.files.get('main.tex');
  assert.match(main, /\\footnote\{Footnote~\\cite\{key\}\.\}/);
  assert.match(main, /\\uline\{under\}/);
  assert.match(main, /\\usepackage\{physics\}/);
  // the note's macros go to the preamble, after the settings' ones
  assert.ok(main.indexOf('renewcommand{\\setting}') < main.indexOf('renewcommand{\\custom}'));
  assert.ok(main.indexOf('renewcommand{\\custom}') < main.indexOf('\\begin{document}'));
  const labels = [...main.matchAll(/\\label\{([^}]+)\}/g)].map(m => m[1]);
  assert.equal(new Set(labels).size, labels.length);
  for (const ref of main.matchAll(/\\eqref\{([^}]+)\}/g)) assert.ok(labels.includes(ref[1]));
});

test('providecommand preserves a configured macro and long paths use short export filenames', async () => {
  const longPath = 'folder/'.repeat(12) + 'n'.repeat(75) + '.md';
  const project = await latexProject({ path: 'root.md', title: 'Title', text: `[[${longPath}]]` }, true, inputs({ [longPath]: '$\\providecommand{\\foo}{B}$ then $\\foo$' }, { math: { physics: false, macros: '\\newcommand{\\foo}{A}' } }));
  const path = [...project.files.keys()].find(p => p.startsWith('notes/'));
  assert.ok(path.length < 50);
  const note = project.files.get(path);
  assert.match(note, /\\providecommand\{\\foo\}\{B\}/);
  assert.ok(!note.includes('renewcommand'));
});

test('external links retain anchors while unsafe TeX targets stay plain text', async () => {
  const main = await exportMain('[Source](https://example.org/paper#results) and [Readable](https://example.org/%5Cinput%7Bsecret%7D). https://example.org/a_b');
  assert.ok(main.includes('\\href{https://example.org/paper\\#results}{Source}'));
  assert.equal((main.match(/\\href\{/g) || []).length, 1);
  assert.ok(main.includes('and Readable.'));
  assert.ok(main.includes('\\url{https://example.org/a_b}'));
  assert.ok(!main.includes('input%7Bsecret'));
});

const tex = name => spawnSync(name, ['--version']).status === 0;
test('the project compiles with pdfLaTeX', { skip: !tex('pdflatex') && 'pdflatex is not installed', timeout: 60000 }, async () => {
  const text = `# Compiled note
$$\\newcommand{\\pauli}[1]{\\hat\\sigma_{#1}}$$

## Section with $\\hat{\\mathbf 1}$
Text [@key; @other, p. 2] with ünïcödé, ↔ and 😀. See \\cref{eq:a}, \\cref{fig:g} and \\cref{tab:t}.
$$
\\begin{align}
\\pauli{x} &= 1 + \\\\
2 &= α
\\end{align}
\\label{eq:a}
$$
continues.

\`\`\`gallery
![](attachments/a.png)
Panel.
![](attachments/b.png)
![](attachments/c.png)

Caption. \\label{fig:g}
\`\`\`

| A | $B$ |
|---|---|
| 1 | 2 |

*Table caption. \\label{tab:t}*

\`\`\`
 a1 ── XX ╭─
 │      │
\`\`\`

- [x] done[^n]

[^n]: A footnote.`;
  const dir = mkdtempSync(join(tmpdir(), 'synapse-latex-'));
  try {
    const project = await latexProject({ path: 'root.md', title: 'Compiled', text }, false, inputs({}, { math: { physics: true, macros: '' } }));
    for (const [name, data] of project.files) { mkdirSync(dirname(join(dir, name)), { recursive: true }); writeFileSync(join(dir, name), data); }
    const run = (command, args) => execFileSync(command, args, { cwd: dir, stdio: 'pipe', timeout: 30000 });
    run('pdflatex', ['-interaction=nonstopmode', '-halt-on-error', 'main.tex']);
    if (tex('biber')) { run('biber', ['main']); run('pdflatex', ['-interaction=nonstopmode', '-halt-on-error', 'main.tex']); }
    const log = readFileSync(join(dir, 'main.log'), 'latin1');
    assert.ok(!/^! /m.test(log), log.slice(-2000));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
