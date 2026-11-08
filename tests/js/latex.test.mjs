import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';
import { splitFrontmatter } from '../../frontend/js/views/notes/mdscan.js';
import { section } from '../../frontend/js/views/notes/embed.js';
import { setNoteHook } from '../../frontend/js/views/notes/hooks.js';
import { pluginModule } from './pluginModule.mjs';
const { latexProject, noteResolver, texEscape } = await pluginModule('export-formats/frontend/latex.ts');
const { figuresHook } = await pluginModule('extended-markdown/frontend/figures/hook.ts');
setNoteHook('figures', figuresHook);

function inputs(notes, extra = {}) {
  const renderer = createRenderer(lib);
  renderer.md.core.ruler.disable('equations');
  return {
    parse: text => renderer.md.parse(text, {}),
    body: text => text.slice(splitFrontmatter(text).bodyStart),
    section, resolve: noteResolver(Object.keys(notes)),
    read: async path => { assert.ok(notes[path], path); return { path, title: path, text: notes[path] }; },
    image: async () => ({ bytes: new Uint8Array([137, 80, 78, 71]), extension: 'png' }),
    bibliography: async keys => ({ text: keys.map(key => `@article{${key}, title={Title}}`).join('\n'), missing: [] }),
    ...extra,
  };
}

test('single-file project keeps math/citations and embeds while packaging figures and bibliography', async () => {
  const notes = { 'part.md': '# Part\n\nEmbedded [@other]. ![x](attachments/x.png)\n', 'skip.md': 'Do not export.' };
  const project = await latexProject({ path: 'root.md', title: 'Müller & 100%', text: '---\ntitle: Private metadata\n---\n# Root\n\n**Bold** and ++under++ [see @key, p. 4]. [[skip]]\n\n![[part]]\n\n$$\nx_1 = 2 \\label{eq:x}\n$$\n\nSee \\eqref{eq:x}.\n\n`[@code]`\n\n```python\nprint("x")\n```' }, false, inputs(notes));
  const main = project.files.get('main.tex');
  assert.equal([...project.files.keys()].filter(n => n.endsWith('.tex')).length, 1);
  assert.match(main, /Müller \\& 100\\%/);
  assert.match(main, /\\textbf\{Bold\}/);
  assert.match(main, /\\autocite\[see\]\[p\. 4\]\{key\}/);
  assert.match(main, /Embedded \\autocite\{other\}/);
  assert.match(main, /\\includegraphics[^\n]+\{figures\/figure-1.png\}/);
  assert.match(main, /x_1 = 2 \\label\{([^}]+)\}/);
  const label = /x_1 = 2 \\label\{([^}]+)\}/.exec(main)[1];
  assert.ok(main.includes(`\\eqref{${label}}`));
  assert.ok(!main.includes('Private metadata') && !main.includes('Do not export'));
  assert.match(project.files.get('references.bib'), /@article\{key/);
  assert.match(project.files.get('references.bib'), /@article\{other/);
  assert.ok(!project.files.get('references.bib').includes('code'));
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
  assert.ok(!main.includes('Never exported'));
  const labels = tex.slice(0, 2).map(([,text]) => /\\label\{([^}]+)\}/.exec(text)[1]);
  assert.notEqual(labels[0], labels[1]);
  const destinations = new Set(tex.flatMap(([, text]) => [...text.matchAll(/\\label\{([^}]+)\}/g)].map(m => m[1])));
  for (const [, text] of tex) for (const ref of text.matchAll(/\\hyperref\[([^\]]+)\]/g)) assert.ok(destinations.has(ref[1]), ref[1]);
  assert.match(project.files.get('references.bib'), /@article\{a/);
  assert.match(project.files.get('references.bib'), /@article\{b/);
});

test('figures, galleries, tables, code and missing content remain portable', async () => {
  const text = '# Heading\n\n![Alt](attachments/a.png)\n*Caption [@key]. \\label{fig:a}*\n\n```gallery\n![](attachments/b.png)\nPanel.\n\nOverall. \\label{fig:g}\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```text\n\\end{verbatim}\n```\n\n[[missing]]\n\n![Remote](https://example.org/image.png)';
  const project = await latexProject({ path: 'a.md', title: 'Title', text }, false, inputs({}, {
    image: async source => source.startsWith('https:') ? null : ({ bytes: new Uint8Array([1]), extension: 'png' }),
    bibliography: async () => ({ text: '', missing: ['key'] }),
  }));
  const main = project.files.get('main.tex');
  assert.match(main, /\\begin\{figure\}/);
  assert.match(main, /\\caption\{Caption/);
  assert.match(main, /\\begin\{subfigure\}/);
  assert.match(main, /\\caption\{Overall/);
  assert.match(main, /\\begin\{tabular\}\{ll\}/);
  assert.match(main, /1 & 2 \\\\/);
  assert.ok(!main.includes('https://example.org/image.png'));
  assert.equal(project.warnings.length, 3);
  assert.ok(project.files.get('README.txt').includes('Reference not in the library: key'));
  assert.equal(texEscape('a\\b{c}_$%&#^~'), 'a\\textbackslash{}b\\{c\\}\\_\\$\\%\\&\\#\\textasciicircum{}\\textasciitilde{}');
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
  assert.match(main, /\\footnote\{Footnote \\autocite\{key\}/);
  assert.match(main, /\\uline\{under\}/);
  assert.match(main, /\\item \[x\] Done/);
  assert.match(main, /\\item \[ \] Open/);
  assert.match(main, /\\usepackage\{physics\}/);
  assert.ok(main.indexOf('renewcommand{\\custom}') < main.indexOf('$\\custom{x}$'));
  assert.ok(main.indexOf('renewcommand{\\setting}') < main.indexOf('\\begin{document}'));
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
  const project = await latexProject({ path: 'root.md', title: 'Title', text: '[Source](https://example.org/paper#results) and [Readable](https://example.org/%5Cinput%7Bsecret%7D).' }, false, inputs({}));
  const main = project.files.get('main.tex');
  assert.ok(main.includes('\\href{https://example.org/paper\\#results}{Source}'));
  assert.equal((main.match(/\\href\{/g) || []).length, 1);
  assert.ok(main.includes('and Readable.'));
  assert.ok(!main.includes('input%7Bsecret'));
});
