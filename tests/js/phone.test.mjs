// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
// The phone layout's decisions without a DOM (phone/model.ts): the destinations plugins offer,
// the tab bar, the Plugins tab's badge, groups of a list, a row's second line and swipes.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  destinations, familyNames, groupRuns, hubBadge, itemMeta, pinned, shortWhen, swipeAction, tabSlots,
} from '../../frontend/js/phone/model.js';

const sources = [
  { id: 'arxiv-today.feed', title: 'arXiv Feed', icon: 'today', description: '', plugin: 'arxiv-today', plugin_name: 'arXiv Today', badge: '9' },
];
const pages = [
  { id: 'tasks.tasks', title: 'Tasks', icon: 'tasks', owner: 'tasks', source: 'Tasks', phone: () => {} },
  { id: 'graph.board', title: 'Board', owner: 'graph', source: 'Graph', mount: () => {} },
];

test('plugin lists and pages become destinations, lists first', () => {
  const d = destinations(sources, pages, new Map([['tasks.tasks', '3']]));
  assert.deepEqual(d.map((x) => [x.id, x.kind, x.phone, x.badge]), [
    ['source:arxiv-today.feed', 'source', true, '9'],
    ['page:tasks.tasks', 'page', true, '3'],
    ['page:graph.board', 'page', false, null],
  ]);
  assert.equal(d[2].icon, 'plugin');
  assert.equal(d[1].pluginName, 'Tasks');
});

test('the pinned destination: the chosen one if usable, else the first usable, or none', () => {
  const d = destinations(sources, pages, new Map());
  assert.equal(pinned(d, null).id, 'source:arxiv-today.feed');
  assert.equal(pinned(d, 'page:tasks.tasks').id, 'page:tasks.tasks');
  assert.equal(pinned(d, 'page:graph.board').id, 'source:arxiv-today.feed');   // needs a larger screen
  assert.equal(pinned(d, 'page:gone.away').id, 'source:arxiv-today.feed');     // its plugin stopped
  assert.equal(pinned(d, 'none'), null);
  assert.equal(pinned([], null), null);
});

test('the tab bar keeps Library and Notes; plugins bring a pinned tab and Plugins', () => {
  const d = destinations(sources, pages, new Map());
  assert.deepEqual(tabSlots(d, pinned(d, null)), ['library', 'notes', 'add', 'pin', 'plugins']);
  assert.deepEqual(tabSlots(d, null), ['library', 'notes', 'add', 'plugins']);
  assert.deepEqual(tabSlots([], null), ['library', 'add', 'notes']);
  // a page only for larger screens brings no Plugins tab of its own
  assert.deepEqual(tabSlots(destinations([], [pages[1]], new Map()), null), ['library', 'add', 'notes']);
  assert.deepEqual(tabSlots([], null, { notes: false }), ['library', 'add']);
});

test('the Plugins tab adds up the badges of what is not pinned', () => {
  const d = destinations(sources, pages, new Map([['tasks.tasks', '3'], ['graph.board', '7']]));
  assert.equal(hubBadge(d, d[0]), '3');            // the feed is pinned; the board is not usable here
  assert.equal(hubBadge(d, null), '12');
  assert.equal(hubBadge(d, d[1]), '9');
  const text = destinations([{ ...sources[0], badge: 'new' }], [], new Map());
  assert.equal(hubBadge(text, null), '•');
  assert.equal(hubBadge(destinations([], [], new Map()), null), null);
});

test('a list falls into runs of its groups, in order', () => {
  const runs = groupRuns([{ id: 1, _group: 'Read' }, { id: 2, _group: 'Read' }, { id: 3, _group: 'Skim' }, { id: 4 }]);
  assert.deepEqual(runs.map((r) => [r.group, r.items.map((i) => i.id)]), [['Read', [1, 2]], ['Skim', [3]], [null, [4]]]);
  assert.deepEqual(groupRuns([]), []);
});

test('a row says who, when and where', () => {
  assert.equal(familyNames('Cui, Jian; Cirac, J. Ignacio'), 'Cui and Cirac');
  assert.equal(familyNames('Cui, Jian; Cirac, J.; Bañuls, M. C.'), 'Cui et al.');
  assert.equal(itemMeta({ authors: 'Cui, Jian', year: 2015, journal: 'Physical Review Letters' }), 'Cui · 2015 · Physical Review Letters');
  assert.equal(itemMeta({ authors: 'Lee, Ann', year: 2026, arxiv: '2611.00001' }), 'Lee · 2026 · arXiv');
  assert.equal(itemMeta({}), '');
});

test('a swipe counts past about a third of the row', () => {
  assert.equal(swipeAction(150, 390), 'add');
  assert.equal(swipeAction(-150, 390), 'dismiss');
  assert.equal(swipeAction(60, 390), null);
  assert.equal(swipeAction(-85, 200), 'dismiss');   // at least 80 px on a narrow row
});

test('short times for lists of notes', () => {
  const now = new Date(2026, 10, 5, 12, 0, 0);
  const at = (d) => d.getTime() / 1000;
  assert.equal(shortWhen(at(new Date(2026, 10, 5, 11, 59, 40)), now), 'now');
  assert.equal(shortWhen(at(new Date(2026, 10, 5, 11, 30)), now), '30 min');
  assert.equal(shortWhen(at(new Date(2026, 10, 5, 8, 0)), now), '4 h');
  assert.equal(shortWhen(at(new Date(2026, 10, 3, 9, 0)), now), 'Tue');
  assert.equal(shortWhen(at(new Date(2026, 9, 20, 9, 0)), now), '20 Oct');
  assert.equal(shortWhen(at(new Date(2025, 9, 20, 9, 0)), now), '20 Oct 2025');
});
