import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AFTER, BEFORE, canSuggestAt, consumeTyped, contextAround, nextWord } from '../../frontend/js/views/notes/ghostLogic.js';

test('suggestions appear at the end of a line or before closing marks only', () => {
  assert.ok(canSuggestAt('', 'We compute the'));
  assert.ok(canSuggestAt('   ', 'We compute the'));
  assert.ok(canSuggestAt('$', 'Energy $E = '));
  assert.ok(canSuggestAt(')]', 'f(g[x'));
  for (const after of ['$.', '$,', ').', '**.', '++.', '~~!', '.', '$.")', '); ', '…']) {
    assert.ok(canSuggestAt(after, 'A sufficiently long prefix'), after);
    assert.ok(canSuggestAt(after, 'ab', true), `explicit: ${after}`);
  }
  for (const after of ['$. More text', '). next', '**.word', '3.14', '.next', '=>']) {
    assert.ok(!canSuggestAt(after, 'A sufficiently long prefix'), after);
    assert.ok(!canSuggestAt(after, 'ab', true), `explicit: ${after}`);
  }
  assert.ok(!canSuggestAt(' more words', 'We compute'));
  assert.ok(!canSuggestAt('', 'ab'));               // too little to go on
  assert.ok(canSuggestAt('', 'ab', true));          // unless asked for
  assert.ok(!canSuggestAt('word', 'ab', true));     // never in the middle of text
});

test('the text around the cursor is bounded', () => {
  const doc = 'a'.repeat(BEFORE + 10) + '|' + 'b'.repeat(AFTER + 10);
  const pos = BEFORE + 10;
  const { prefix, suffix } = contextAround(doc, pos);
  assert.equal(prefix.length, BEFORE);
  assert.equal(suffix.length, AFTER);
  assert.ok(suffix.startsWith('|'));
  assert.deepEqual(contextAround('abc', 1), { prefix: 'a', suffix: 'bc' });
});

test('typing the suggestion keeps the rest of it', () => {
  const g = { pos: 10, text: ' the energy' };
  assert.deepEqual(consumeTyped(g, 10, 10, ' '), { pos: 11, text: 'the energy' });
  assert.deepEqual(consumeTyped(g, 10, 10, ' the'), { pos: 14, text: ' energy' });
  assert.equal(consumeTyped(g, 10, 10, ' x'), null);            // something else
  assert.equal(consumeTyped(g, 10, 10, ' the energy'), null);   // all of it: nothing left
  assert.equal(consumeTyped(g, 9, 10, ' '), null);              // a replacement
  assert.equal(consumeTyped(g, 3, 3, ' '), null);               // elsewhere
});

test('accepting word by word', () => {
  assert.equal(nextWord(' the energy'), ' the');
  assert.equal(nextWord('\\vb{r} = 0'), '\\vb');
  assert.equal(nextWord('{r} = 0'), '{');
  assert.equal(nextWord('  énergie libre'), '  énergie');
  assert.equal(nextWord('   '), '   ');
});
