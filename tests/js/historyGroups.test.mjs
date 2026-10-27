// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { groupEdits, kindIcon, span, whatHappened, whoMade } from '../../frontend/js/views/notes/historyGroups.js';

const at = (min) => new Date(Date.UTC(2026, 9, 26, 12, 0) + min * 60000).toISOString();
const e = (id, min, extra = {}) => ({ id, at: at(min), by: 'human', kind: 'edit', device: 'This computer', summary: 'Edited A', ...extra });

test('the user’s edits close together become one row; everything else stays apart', () => {
  const entries = [   // newest first, as the history lists them
    e('7', 100),
    e('6', 90),
    e('5', 80, { device: 'iPad' }),            // another device
    e('4', 70),
    e('3', 60, { by: 'ai', kind: 'ai', agent: 'Claude', summary: 'Tidied' }),
    e('2', 50),
    e('1', 5),                                 // 45 minutes before: a new session
  ];
  assert.deepEqual(groupEdits(entries).map((g) => g.entries.map((x) => x.id)), [['7', '6'], ['5'], ['4'], ['3'], ['2'], ['1']]);
});

test('a milestone keeps its version a row of its own', () => {
  const entries = [e('3', 20), e('2', 10, { milestones: [{ id: 'm-1', label: 'Draft' }] }), e('1', 5)];
  assert.deepEqual(groupEdits(entries).map((g) => g.entries.map((x) => x.id)), [['3'], ['2', '1']]);
});

test('who and what', () => {
  assert.equal(whoMade({ by: 'human', device: 'This computer' }), 'You');
  assert.equal(whoMade({ by: 'human', device: 'iPad' }), 'You · iPad');
  assert.equal(whoMade({ by: 'ai', agent: 'Codex' }), 'Codex');
  assert.equal(whoMade({ by: 'ai' }), 'An AI app');
  assert.equal(whoMade({ by: 'outside' }), 'Outside Synapse');
  assert.equal(whatHappened(e('1', 0, { status: 'A' })), 'Created');
  assert.equal(whatHappened(e('1', 0, { status: 'M' })), 'Edited');
  assert.equal(whatHappened(e('1', 0, { kind: 'outside', by: 'outside', status: 'M' })), 'Changed outside Synapse');
  assert.equal(whatHappened(e('1', 0, { kind: 'ai', summary: 'Fixed the units' })), 'Fixed the units');
  assert.equal(kindIcon('ai'), 'sparkle');
  assert.equal(kindIcon(undefined), 'edit');
});

test('a group shows its time span', () => {
  const now = new Date(at(200));
  const g = { entries: [e('2', 90), e('1', 60)] };
  assert.match(span(g, now), /–/);
  assert.doesNotMatch(span({ entries: [e('1', 60)] }, now), /–/);
});

test('edits that end where they began are left out (older detours)', () => {
  // a note's versions: hashes
  const v = (id, min, before, after, extra = {}) => e(id, min, { before_hash: before, after_hash: after, ...extra });
  const created = [v('3', 20, 'b', 'a'), v('2', 10, 'a', 'b'), v('1', 0, null, 'a', { status: 'A' })];
  assert.equal(groupEdits(created).length, 1);   // made from nothing: it still created the note
  const real = [v('4', 50, 'b', 'a'), v('3', 40, 'a', 'b'), v('2', -100, 'x', 'a')];
  assert.deepEqual(groupEdits(real).map((g) => g.entries.map((x) => x.id)), [['2']]);
  const withAi = [v('3', 20, 'b', 'a'), v('2', 10, 'a', 'b', { by: 'ai', kind: 'ai' })];
  assert.equal(groupEdits(withAi).length, 2);   // an AI change and its undo stay
  const marked = [v('3', 20, 'b', 'a'), v('2', 10, 'a', 'b', { milestones: [{ id: 'm', label: 'M' }] })];
  assert.equal(groupEdits(marked).length, 2);   // a milestone's version is never hidden (nor grouped away)
  // all notes: trees
  const t = (id, min, parent_tree, tree) => e(id, min, { parent_tree, tree });
  assert.deepEqual(groupEdits([t('2', 10, 'T1', 'T0'), t('1', 0, 'T0', 'T1')]), []);
  assert.equal(groupEdits([t('2', 10, 'T1', 'T2'), t('1', 0, 'T0', 'T1')]).length, 1);
});
