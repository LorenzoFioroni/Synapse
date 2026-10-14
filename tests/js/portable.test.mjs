// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatAuthors, portableBody, portableMarkdown, referenceMarkdown } from '../../frontend/js/views/notes/portable.js';

const LIB = {
  smith2021: { title: 'Quantum things.', authors: 'Smith, Anna Maria; Doe, John; Roe, R.', year: 2021, journal: 'Phys. Rev. X' },
  lee2019: { title: 'Tensor networks', authors: 'Lee, Ji-Hoon', year: 2019, journal: '' },
};
const citeInfo = (k) => LIB[k] || null;

test('authors and reference entries', () => {
  assert.equal(formatAuthors('Smith, Anna Maria; Doe, John; Roe, R.'), 'Smith, A. M., Doe, J. & Roe, R.');
  assert.equal(formatAuthors('Lee, Ji-Hoon'), 'Lee, J.-H.');
  assert.equal(formatAuthors('CERN Collaboration'), 'CERN Collaboration');
  assert.equal(formatAuthors(''), '');
  assert.equal(referenceMarkdown('smith2021', LIB.smith2021), 'Smith, A. M., Doe, J. & Roe, R. (2021). Quantum things. *Phys. Rev. X*.');
  assert.equal(referenceMarkdown('x', { title: '', authors: '', year: null }), '(n.d.). @x.');
});

test('citations, links and images become plain Markdown', () => {
  const text = '---\ntitle: A\ntags: [x]\n---\nAs shown [@smith2021, p. 4] and [see @lee2019; @ghost].\n'
    + 'See [[Other note|the other one]], [[B#Part]] and [[#Intro]].\n\n![Plot|300](attachments/plot.png)\n\n'
    + '`[@smith2021]` stays code, $[[x]]$ stays math.\n';
  const { markdown, keys } = portableBody(text, { citeInfo });
  assert.equal(markdown, 'As shown (Smith et al. 2021, p. 4) and (see Lee 2019; @ghost).\n'
    + 'See the other one, B › Part and Intro.\n\n![Plot](attachments/plot.png)\n\n'
    + '`[@smith2021]` stays code, $[[x]]$ stays math.\n');
  assert.deepEqual(keys, ['smith2021', 'lee2019', 'ghost']);
});

test('embeds are replaced by what they embed, with its citations', () => {
  const embed = (target, heading) => (target === 'C' && !heading ? { markdown: '\nInner (Lee 2019).\n', keys: ['lee2019'] } : null);
  const { markdown, keys } = portableBody('Before.\n\n![[C]]\n\n![[Missing]]\n', { citeInfo, embed });
  assert.equal(markdown, 'Before.\n\nInner (Lee 2019).\n\n*Missing*\n');
  assert.deepEqual(keys, ['lee2019']);
});

test('the whole export: a title, the body and the references in order of first citation', () => {
  const md = portableMarkdown('My note', 'Text [@lee2019] then [@smith2021; @lee2019] and [@ghost].', { citeInfo });
  assert.equal(md, '# My note\n\nText (Lee 2019) then (Smith et al. 2021; Lee 2019) and (@ghost).\n\n## References\n\n'
    + '1. Lee, J.-H. (2019). Tensor networks.\n2. Smith, A. M., Doe, J. & Roe, R. (2021). Quantum things. *Phys. Rev. X*.\n');
  assert.equal(portableMarkdown('Ignored', '# Own title\n\nNo citations.', { citeInfo }), '# Own title\n\nNo citations.\n');
});
