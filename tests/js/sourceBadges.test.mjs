import assert from 'node:assert/strict';
import { test } from 'node:test';
import { todaySourceBadges } from '../../frontend/js/ui/sourceBadges.js';

const following = {label: 'Following', text: 'Following', tone: 'gold', icon: 'star'};
const cites = {label: 'Cites you', text: 'Cites you', tone: 'red', icon: 'megaphone'};

test('primary-sidebar badges belong only to the current calendar day', () => {
  const variants = [
    {id: '2026-11-02', highlights: [following]},
    {id: '2026-11-03', highlights: [following, cites]},
    {id: '2026-11-04', highlights: [cites]},
  ];
  assert.deepEqual(todaySourceBadges(variants, new Date(2026, 10, 3, 23, 59)), [following, cites]);
  assert.deepEqual(todaySourceBadges(variants, new Date(2026, 10, 4, 0, 1)), [cites]);
  assert.deepEqual(todaySourceBadges(variants, new Date(2026, 10, 5)), []);
});

test('latest retained, undated, and empty days do not supply today badges', () => {
  const now = new Date(2026, 10, 3);
  assert.deepEqual(todaySourceBadges([{id: '2026-11-02', highlights: [following]}], now), []);
  assert.deepEqual(todaySourceBadges([{id: 'today', highlights: [following]}], now), []);
  assert.deepEqual(todaySourceBadges([{id: '2026-11-03'}], now), []);
  assert.deepEqual(todaySourceBadges(undefined, now), []);
});
