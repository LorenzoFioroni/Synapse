// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { lookupIn, refMissing, refNames, refParts, refPlain } from '../../frontend/js/views/notes/crossref.js';

const EQ = new Map([['eq:x', '1'], ['eq:y', '2'], ['eq:z', '3'], ['eq:w', '5'], ['eq:none', null], ['same', '4']]);
const FIG = new Map([['fig:a', '1'], ['fig:b', '2'], ['fig:c', '3'], ['fig:p', '2(b)'], ['same', '9']]);
const lookup = lookupIn(EQ, FIG);
const text = (kind, inner) => refPlain(refParts(kind, refNames(kind, inner), lookup)).replace(/ /g, ' ');

test('\\ref and \\eqref: the number', () => {
  assert.equal(text('ref', 'fig:b'), '2');
  assert.equal(text('eqref', 'eq:y'), '(2)');
  assert.equal(text('ref', 'fig:p'), '2(b)');
  assert.equal(text('ref', 'nope'), '(??)');
});

test('\\cref knows equations from figures', () => {
  assert.equal(text('cref', 'fig:b'), 'Fig. 2');
  assert.equal(text('cref', 'eq:y'), 'Eq. (2)');
  assert.equal(text('cref', 'fig:p'), 'Fig. 2(b)');
  assert.equal(text('Cref', 'fig:b'), 'Figure 2');
  assert.equal(text('Cref', 'eq:y'), 'Equation (2)');
  assert.equal(text('cref', 'same'), 'Eq. (4)');   // equations win a shared name
});

test('several names: plurals, sorting, ranges, mixed kinds', () => {
  assert.equal(text('cref', 'fig:b, fig:a'), 'Figs. 1 and 2');
  assert.equal(text('cref', 'eq:z,eq:x,eq:y'), 'Eqs. (1)–(3)');
  assert.equal(text('cref', 'eq:x,eq:y,eq:z,eq:w'), 'Eqs. (1)–(3) and (5)');
  assert.equal(text('cref', 'eq:x,eq:z,eq:w'), 'Eqs. (1), (3) and (5)');
  assert.equal(text('cref', 'fig:a,eq:x'), 'Fig. 1 and Eq. (1)');
  assert.equal(text('Cref', 'fig:a,fig:c,eq:x'), 'Figures 1 and 3 and Equation (1)');
  assert.equal(text('cref', 'fig:a,fig:a'), 'Fig. 1');
  assert.equal(text('cref', 'fig:b,fig:p'), 'Figs. 2 and 2(b)');
});

test('unknown names', () => {
  const parts = refParts('cref', ['fig:a', 'nope', 'eq:none'], lookup);
  assert.equal(refPlain(parts).replace(/ /g, ' '), 'Fig. 1 and ?? and ??');
  assert.ok(refMissing(parts));
  assert.ok(!refMissing(refParts('cref', ['fig:a'], lookup)));
  // each number is its own part, so it can link to its label
  assert.deepEqual(refParts('cref', ['eq:x', 'eq:z'], lookup), ['Eqs. ', { name: 'eq:x', text: '(1)' }, ' and ', { name: 'eq:z', text: '(3)' }]);
});
