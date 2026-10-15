// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  formatAuthors, portableBody, portableMarkdown, referenceHtml, referenceId, referenceLink, referenceMarkdown,
} from '../../frontend/js/views/notes/portable.js';

const LIB = {
  smith2021: { title: 'Quantum things.', authors: 'Smith, Anna Maria; Doe, John; Roe, R.', year: 2021, journal: 'Phys. Rev. X',
    item_type: 'journalArticle', volume: '11', issue: '2', pages: '021001-021010', doi: 'https://doi.org/10.1103/PhysRevX.11.021001' },
  lee2019: { title: 'Tensor networks', authors: 'Lee, Ji-Hoon', year: 2019, journal: '' },
};
const citeInfo = (k) => LIB[k] || null;

test('authors and reference entries', () => {
  assert.equal(formatAuthors('Smith, Anna Maria; Doe, John; Roe, R.'), 'Smith, A. M., Doe, J. & Roe, R.');
  assert.equal(formatAuthors('Lee, Ji-Hoon'), 'Lee, J.-H.');
  assert.equal(formatAuthors('CERN Collaboration'), 'CERN Collaboration');
  assert.equal(formatAuthors(''), '');
  assert.equal(referenceMarkdown('smith2021', LIB.smith2021),
    'Smith, A. M., Doe, J. & Roe, R. (2021). Quantum things. *Phys. Rev. X*, *11*(2), 021001–021010. <https://doi.org/10.1103/PhysRevX.11.021001>');
  assert.equal(referenceHtml('smith2021', LIB.smith2021),
    'Smith, A. M., Doe, J. &amp; Roe, R. (2021). Quantum things. <i>Phys. Rev. X</i>, <i>11</i>(2), 021001–021010. '
    + '<a class="ref-link" href="https://doi.org/10.1103/PhysRevX.11.021001">https://doi.org/10.1103/PhysRevX.11.021001</a>');
  assert.equal(referenceMarkdown('x', { title: '', authors: '', year: null }), '(n.d.). @x.');
});

test('reference entries by item type, with what is needed to find the work', () => {
  const ref = (info) => referenceMarkdown('k', { authors: 'Lee, Ji', year: 2019, title: 'A title', ...info });
  assert.equal(ref({ item_type: 'book', publisher: 'Springer', place: 'Berlin', edition: '2', series: 'Lecture Notes in Physics' }),
    'Lee, J. (2019). *A title* (2nd ed.). Lecture Notes in Physics. Springer, Berlin.');
  assert.equal(ref({ item_type: 'bookSection', journal: 'The big book', pages: '10 - 20', publisher: 'Wiley' }),
    'Lee, J. (2019). A title. In *The big book* (pp. 10–20). Wiley.');
  assert.equal(ref({ item_type: 'conferencePaper', journal: 'Proc. QIP', doi: '10.1145/3519935' }),
    'Lee, J. (2019). A title. In *Proc. QIP*. <https://doi.org/10.1145/3519935>');
  assert.equal(ref({ item_type: 'thesis', publisher: 'ETH Zürich' }), 'Lee, J. (2019). *A title* [Thesis]. ETH Zürich.');
  assert.equal(ref({ item_type: 'report', journal: 'CERN', series: 'TR-12' }), 'Lee, J. (2019). *A title* (TR-12). CERN.');
  assert.equal(ref({ item_type: 'preprint', journal: 'arXiv', archive_id: 'arXiv:2101.00001' }),
    'Lee, J. (2019). A title. *arXiv*, arXiv:2101.00001. <https://arxiv.org/abs/2101.00001>');
  assert.equal(ref({ item_type: 'preprint', archive_id: 'arXiv:2101.00001' }),
    'Lee, J. (2019). A title. arXiv:2101.00001. <https://arxiv.org/abs/2101.00001>');
  assert.equal(ref({ item_type: 'webpage', journal: 'Blog', url: 'https://example.org/post' }),
    'Lee, J. (2019). A title. *Blog*. <https://example.org/post>');
  assert.equal(ref({ title: 'Is it [x]_*?*' }), 'Lee, J. (2019). Is it [x]\\_\\*?\\*.');
  assert.equal(referenceLink({ doi: 'doi: 10.1000/x', url: 'https://example.org' }), 'https://doi.org/10.1000/x');
  assert.equal(referenceLink({ doi: 'not a doi', url: 'ftp://x' }), null);
  assert.equal(referenceId('smith:2021/a'), 'ref-smith_3a2021_2fa');
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
  const note = portableBody('Text [@lee2019] then [@smith2021; @lee2019] and [@ghost].', { citeInfo });
  assert.equal(portableMarkdown('My note', note, citeInfo), '# My note\n\nText (Lee 2019) then (Smith et al. 2021; Lee 2019) and (@ghost).\n\n## References\n\n'
    + '1. Lee, J.-H. (2019). Tensor networks.\n2. Smith, A. M., Doe, J. & Roe, R. (2021). Quantum things. *Phys. Rev. X*, *11*(2), 021001–021010. '
    + '<https://doi.org/10.1103/PhysRevX.11.021001>\n');
  assert.equal(portableMarkdown('Ignored', portableBody('# Own title\n\nNo citations.', { citeInfo }), citeInfo), '# Own title\n\nNo citations.\n');
});
