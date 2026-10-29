import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CONTEXT_CHARS, suggestionContext } from '../../frontend/js/views/notes/suggestionContext.js';

const filler = Array.from({length: 100}, (_, i) => `Run ${i} records the trial states and convergence diagnostics for later comparison.`).join('\n\n');
const definition = 'In this note, the reset parameter is denoted by $\\zeta_{\\mathrm{reset}}$.';

test('retains the title, active section path and a relevant definition beyond the nearby window', () => {
  const doc = '# Cooling\n\n## Conventions\n\n' + definition + '\n\n' + filler
    + '\n\n## Results\n\n### Local states\n\nWe denote the reset parameter by $';
  const c = suggestionContext(doc, doc.length);
  assert.equal(c.title, 'Cooling');
  assert.deepEqual(c.headings, ['## Results', '### Local states']);
  assert.ok(c.excerpts.includes(definition));
  assert.ok(!c.excerpts.some(s => s.includes('Run 99')));
  assert.ok(c.title.length + c.headings.join('').length + c.excerpts.join('').length <= CONTEXT_CHARS);
});

test('reads only a frontmatter title, without treating other metadata as note context', () => {
  const doc = '---\nid: private-id\nauthor: Person\ntitle: "Cooling study"\n---\n\n' + filler + '\n\nWe study ';
  const c = suggestionContext(doc, doc.length);
  assert.equal(c.title, 'Cooling study');
  assert.ok(!JSON.stringify(c).includes('private-id'));
  assert.ok(!JSON.stringify(c).includes('Person'));
});

test('does not use future definitions or headings, or leak another note', () => {
  const before = '# First note\n\n' + filler + '\n\nThe reset is ';
  const future = '\n\n## Future secret\n\nWe define $\\eta$ as the future reset.';
  const c = suggestionContext(before + future, before.length);
  assert.ok(!JSON.stringify(c).includes('Future'));
  assert.ok(!JSON.stringify(c).includes('eta'));
  const other = suggestionContext('# Second note\n\nWe study ', 24);
  assert.equal(other.title, 'Second note');
  assert.ok(!JSON.stringify(other).includes('First note'));
});

test('code fences, comments and galleries do not supply headings or definitions', () => {
  const hidden = '```python\n# False title\nWe define SECRET_CODE as the reset.\n```\n\n'
    + '<!-- We define SECRET_COMMENT as the reset. -->\n\n'
    + '```gallery\nWe define SECRET_GALLERY as the reset.\n```\n\n';
  const doc = '# Real title\n\n' + hidden + definition + '\n\n' + filler + '\n\nThe reset parameter is ';
  const c = suggestionContext(doc, doc.length);
  assert.equal(c.title, 'Real title');
  assert.ok(c.excerpts.includes(definition));
  assert.ok(!JSON.stringify(c).includes('SECRET'));
});

test('prefers a section-specific updated convention for the current subject', () => {
  const old = 'The reset is denoted by $a$.';
  const updated = 'The reset is denoted by $b$.';
  const doc = '# Models\n\n## First model\n\n' + old + '\n\n' + filler
    + '\n\n## Second model\n\n' + updated + '\n\n' + filler + '\n\nThe reset is $';
  const c = suggestionContext(doc, doc.length);
  assert.ok(c.excerpts.includes(updated));
  assert.ok(!c.excerpts.includes(old));
});

test('extracts a complete definition sentence without cutting through math', () => {
  const first = 'We define the reset vector $v=\\frac{a.b}{c.d}$ as the reference.';
  const long = first + ' ' + 'Numerical diagnostics are recorded. '.repeat(30);
  const doc = '# Study\n\n' + long + '\n\n' + filler + '\n\nThe reset vector is ';
  const c = suggestionContext(doc, doc.length);
  assert.ok(c.excerpts.includes(first));
  assert.ok(c.excerpts.every(s => s.length <= 420));
});

test('keeps complete macro definitions and omits oversized snippets', () => {
  const macro = '$\\newcommand{\\reset}{\\zeta_{\\mathrm{reset}}}$';
  const huge = 'We define the reset $' + 'x'.repeat(600) + '$.';
  const doc = '# Study\n\n' + macro + '\n\n' + huge + '\n\n' + filler + '\n\nWe use the reset ';
  const c = suggestionContext(doc, doc.length);
  assert.ok(c.excerpts.includes(macro));
  assert.ok(!c.excerpts.some(s => s.includes('xxx')));
});

test('nearby definitions stay in the ordinary prefix without being duplicated as excerpts', () => {
  const doc = '# Study\n\n' + definition + '\n\nWe use the reset ';
  const c = suggestionContext(doc, doc.length);
  assert.deepEqual(c.excerpts, []);
});

test('oversized titles are omitted without truncating an equation', () => {
  const doc = '# Study of $' + 'x'.repeat(200) + '$\n\n' + filler + '\n\nWe study ';
  const c = suggestionContext(doc, doc.length);
  assert.equal(c.title, '');
  assert.deepEqual(c.headings, []);
});
