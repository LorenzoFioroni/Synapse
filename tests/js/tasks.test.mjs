// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
// The Tasks page's pieces without a DOM: reading typed dates, and what the list shows
// (views, search, sort, groups) for a set of tasks.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pluginModule } from './pluginModule.mjs';
const { dueLabel, dueState, monthGrid, parseDue, parseQuick } = await pluginModule('tasks/frontend/dates.ts');
const { buildRows, effectiveDue, inView, parseQuery, viewCounts } = await pluginModule('tasks/frontend/taskQuery.ts');
import { portableBody } from '../../frontend/js/views/notes/portable.js';
import { searchTasks } from '../../frontend/js/views/notes/completion.js';

const NOW = new Date(2026, 9, 20, 15, 0);   // Tue 20 Oct 2026

test('typed dates', () => {
  const d = (s) => parseDue(s, NOW);
  assert.equal(d('today'), '2026-10-20');
  assert.equal(d('tomorrow'), '2026-10-21');
  assert.equal(d('tue'), '2026-10-20');           // a weekday: the next one, today included
  assert.equal(d('next tue'), '2026-10-27');
  assert.equal(d('fri'), '2026-10-23');
  assert.equal(d('friday'), '2026-10-23');
  assert.equal(d('next week'), '2026-10-26');     // Monday
  assert.equal(d('next month'), '2026-11-01');
  assert.equal(d('in 3 days'), '2026-10-23');
  assert.equal(d('2w'), '2026-11-03');
  assert.equal(d('27 oct'), '2026-10-27');
  assert.equal(d('oct 27'), '2026-10-27');
  assert.equal(d('3 jan'), '2027-01-03');         // without a year: the next time that day comes
  assert.equal(d('27/10'), '2026-10-27');         // day first
  assert.equal(d('27.10.2027'), '2027-10-27');
  assert.equal(d('2026-10-30'), '2026-10-30');
  assert.equal(d('31 feb'), null);
  assert.equal(d('2026-02-30'), null);
  assert.equal(d('soon'), null);
  assert.equal(d(''), null);
});

test('dates as people say them', () => {
  assert.equal(dueLabel('2026-10-20', NOW), 'Today');
  assert.equal(dueLabel('2026-10-21', NOW), 'Tomorrow');
  assert.equal(dueLabel('2026-10-19', NOW), 'Yesterday');
  assert.equal(dueLabel('2026-10-23', NOW), 'Fri');
  assert.equal(dueLabel('2026-10-27', NOW), 'Tue 27 Oct');
  assert.equal(dueLabel('2027-01-03', NOW), 'Sun 3 Jan 2027');
  assert.deepEqual(['2026-10-18', '2026-10-20', '2026-10-22', '2026-10-30'].map((x) => dueState(x, NOW)), ['overdue', 'today', 'soon', '']);
  const grid = monthGrid(2026, 9);
  assert.equal(grid.length, 42);
  assert.deepEqual(grid[0], { iso: '2026-09-28', day: 28, inMonth: false });   // weeks start on Monday
  assert.ok(grid.some((c) => c.iso === '2026-10-31' && c.inMonth));
});

test('quick add reads a date from the end of what was typed', () => {
  assert.deepEqual(parseQuick('Draft the outlook fri', NOW), { title: 'Draft the outlook', due: '2026-10-23' });
  assert.deepEqual(parseQuick('Send the plots by next week', NOW), { title: 'Send the plots', due: '2026-10-26' });
  assert.deepEqual(parseQuick('Call Ann about Friday’s talk', NOW), { title: 'Call Ann about Friday’s talk', due: null });
  assert.deepEqual(parseQuick('tomorrow', NOW), { title: 'tomorrow', due: null });   // the title keeps a word
  assert.deepEqual(parseQuick('Plot $\\chi$ 27 oct', NOW), { title: 'Plot $\\chi$', due: '2026-10-27' });
  assert.deepEqual(parseQuick('Draft fri', NOW, false), { title: 'Draft fri', due: null });
});

// a small tree: A (with B, C below; D below C), E, F (done), G (no note)
let n = 0;
const T = (id, o = {}) => ({ id, parent_id: null, position: ++n, title: id, body: '', status: 'open', due: null,
  created_at: `2026-10-0${n % 9 + 1}T10:00:00Z`, updated_at: '2026-10-10T10:00:00Z', done_at: null, created_by: 'human',
  updated_by: 'human', deleted_at: null, deleted_batch: null, ...o });
const TASKS = [
  T('A', { title: 'Write the paper', due: '2026-10-30' }),
  T('B', { parent_id: 'A', title: 'Figures', status: 'done', done_at: '2026-10-19T10:00:00Z' }),
  T('C', { parent_id: 'A', title: 'Intro', due: '2026-10-22', body: 'Cite [@smith2021] here' }),
  T('D', { parent_id: 'C', title: 'Read survey papers', due: '2026-10-18' }),
  T('E', { title: 'Book the room', due: '2026-10-20' }),
  T('F', { title: 'Old thing', status: 'done', done_at: '2026-10-01T10:00:00Z' }),
  T('G', { title: 'Referee report' }),
];
const REFS = {
  A: [{ path: 'Notes/Paper.md', title: 'Paper', line: 4, heading: 'TODOs' }],
  E: [{ path: 'Admin.md', title: 'Admin', line: 2, heading: null }],
  F: [{ path: 'Admin.md', title: 'Admin', line: 3, heading: null }],
};
const ids = (rows) => rows.filter((r) => r.type === 'task').map((r) => r.task.id);

test('views', () => {
  const v = (view) => TASKS.filter((t) => inView(view, t, REFS, NOW)).map((t) => t.id);
  assert.deepEqual(v('open'), ['A', 'C', 'D', 'E', 'G']);
  assert.deepEqual(v('today'), ['D', 'E']);           // overdue and due today
  assert.deepEqual(v('upcoming'), ['C', 'E']);   // today and the next seven days (A is ten days away)
  assert.deepEqual(v('nodate'), ['G']);
  assert.deepEqual(v('unfiled'), ['C', 'D', 'G']);    // without its parents known, a sub-task counts on its own
  const byId = new Map(TASKS.map((t) => [t.id, t]));
  assert.deepEqual(TASKS.filter((t) => inView('unfiled', t, REFS, NOW, byId)).map((t) => t.id), ['G']);   // C and D are in A's note
  assert.deepEqual(v('done'), ['B', 'F']);
  const c = viewCounts(TASKS, REFS, NOW);
  assert.equal(c.open, 5);
  assert.equal(c.overdue, 1);
  assert.equal(c.unfiled, 1);
  assert.equal(c.all, 7);
});

test('search filters', () => {
  const kids = new Map([['A', [TASKS[1], TASKS[2]]], ['C', [TASKS[3]]]]);
  const q = (s) => TASKS.filter(parseQuery(s, REFS, kids, NOW)).map((t) => t.id);
  assert.deepEqual(q('intro'), ['C']);
  assert.deepEqual(q('smith2021'), ['C']);              // the body is searched too
  assert.deepEqual(q('"the room"'), ['E']);
  assert.deepEqual(q('-paper is:open'), ['C', 'E', 'G']);   // "papers" contains "paper"
  assert.deepEqual(q('due:overdue'), ['D']);
  assert.deepEqual(q('due:today'), ['E']);
  assert.deepEqual(q('due:week'), ['C', 'D', 'E']);
  assert.deepEqual(q('due:none is:open'), ['G']);
  assert.deepEqual(q('due:<2026-10-21'), ['D', 'E']);
  assert.deepEqual(q('due:>2026-10-22'), ['A']);
  assert.deepEqual(q('note:admin'), ['E', 'F']);
  assert.deepEqual(q('has:body'), ['C']);
  assert.deepEqual(q('has:subtasks'), ['A', 'C']);
  assert.deepEqual(q('is:done'), ['B', 'F']);
});

test('a sub-task keeps its parents in the list, shown as context', () => {
  const { rows, matches } = buildRows(TASKS, REFS, { view: 'open', query: 'survey', group: 'none', now: NOW });
  assert.equal(matches, 1);
  assert.deepEqual(ids(rows), ['A', 'C', 'D']);
  assert.deepEqual(rows.map((r) => [r.task.id, r.depth, r.context]), [['A', 0, true], ['C', 1, true], ['D', 2, false]]);
  const flat = buildRows(TASKS, REFS, { view: 'open', query: 'survey', group: 'none', flat: true, now: NOW });
  assert.deepEqual(ids(flat.rows), ['D']);
});

test('a task ticked on the page stays in view until the view changes', () => {
  const ticked = TASKS.map((t) => (t.id === 'E' ? { ...t, status: 'done' } : t));
  assert.ok(!ids(buildRows(ticked, REFS, { view: 'open', group: 'none', now: NOW }).rows).includes('E'));
  assert.ok(ids(buildRows(ticked, REFS, { view: 'open', group: 'none', keep: new Set(['E']), now: NOW }).rows).includes('E'));
});

test('open views hide done sub-tasks unless asked; folded tasks hide their sub-tasks', () => {
  // "Order": as the notes show them (by note, then line), tasks in no note last
  assert.deepEqual(ids(buildRows(TASKS, REFS, { view: 'open', group: 'none', now: NOW }).rows), ['E', 'A', 'C', 'D', 'G']);
  assert.deepEqual(ids(buildRows(TASKS, REFS, { view: 'open', group: 'none', showDone: true, now: NOW }).rows), ['E', 'A', 'B', 'C', 'D', 'G']);
  const folded = buildRows(TASKS, REFS, { view: 'open', group: 'none', folded: new Set(['A']), now: NOW }).rows;
  assert.deepEqual(ids(folded), ['E', 'A', 'G']);
  assert.deepEqual([folded[1].kids, folded[1].kidsDone, folded[1].folded], [2, 1, true]);
});

test('sorting orders siblings; a parent sorts by its earliest open sub-task', () => {
  const kids = new Map([['A', [TASKS[1], TASKS[2]]], ['C', [TASKS[3]]]]);
  assert.equal(effectiveDue(TASKS[0], kids), '2026-10-18');
  const rows = buildRows(TASKS, REFS, { view: 'open', sort: 'due', group: 'none', now: NOW }).rows;
  assert.deepEqual(ids(rows), ['A', 'C', 'D', 'E', 'G']);   // A (via D, 18 Oct) before E (20 Oct); no date last
  const byTitle = buildRows(TASKS, REFS, { view: 'all', sort: 'title', group: 'none', now: NOW }).rows;
  assert.deepEqual(ids(byTitle), ['E', 'F', 'G', 'A', 'B', 'C', 'D']);
});

test('groups: by note and by due date', () => {
  const byNote = buildRows(TASKS, REFS, { view: 'open', group: 'note', now: NOW }).rows;
  assert.deepEqual(byNote.filter((r) => r.type === 'group').map((g) => [g.label, g.count]), [['Admin', 1], ['Paper', 3], ['Not in a note', 1]]);
  const byDue = buildRows(TASKS, REFS, { view: 'open', group: 'due', now: NOW }).rows;
  assert.deepEqual(byDue.filter((r) => r.type === 'group').map((g) => g.label), ['Overdue', 'Today', 'No date']);
  const note = buildRows(TASKS, REFS, { view: 'open', group: 'none', note: 'Notes/Paper.md', now: NOW });
  assert.deepEqual(ids(note.rows), ['A', 'C', 'D']);   // a note's tasks, with their sub-tasks
});

test('the Markdown export writes task lines as checkbox lists', () => {
  const tasks = {
    aaaa: { title: 'Write [@smith2021]', done: false, subtasks: [{ title: 'Figures', done: true, subtasks: [{ title: 'Fig. 1', done: false, subtasks: [] }] }] },
    bbbb: { title: 'Book the room', done: true, subtasks: [] },
  };
  const opts = { citeInfo: (k) => (k === 'smith2021' ? { title: 'T', authors: 'Smith, Anna', year: 2021 } : null), task: (id) => tasks[id] || null };
  const md = portableBody('# N\n\n{{task:aaaa}}\n- {{task:bbbb}}\n> {{task:bbbb}}\n\nSee {{task:bbbb}} today.\n\n{{task:zzzz}}\n', opts);
  assert.equal(md.markdown, '# N\n\n- [ ] Write (Smith 2021)\n  - [x] Figures\n    - [ ] Fig. 1\n- [x] Book the room\n> - [x] Book the room\n\n'
    + 'See Book the room today.\n\n- [ ] *(deleted task)*\n');
  assert.deepEqual(md.keys, ['smith2021']);
});

test('"{{task" autocomplete: every word must match; titles starting with it first, open before done', () => {
  const C = (id, title, done = false, updated = '2026-10-10') => ({ id, title, detail: '', info: '', done, updated });
  const all = [C('a', 'Write the outlook section'), C('b', 'Discuss the Ising model', true), C('c', 'Discuss Potts at finite coupling'),
    C('d', 'Re-read the discussion of Prosen', false, '2026-10-19'), C('e', 'Plot chi vs epsilon')];
  assert.deepEqual(searchTasks(all, 'disc').map((t) => t.id), ['c', 'b', 'd']);
  assert.deepEqual(searchTasks(all, 'ising model').map((t) => t.id), ['b']);
  assert.deepEqual(searchTasks(all, 'outlook write').map((t) => t.id), ['a']);
  assert.deepEqual(searchTasks(all, 'nothing like this'), []);
  assert.equal(searchTasks(all, '').length, 5);
  assert.equal(searchTasks(all, '', 2).length, 2);
});

test('a phone shows open tasks due today, in the next week, or all, by day', async () => {
  const { phoneGroups } = await pluginModule('tasks/frontend/phoneGroups.ts');
  const now = new Date(2026, 10, 5, 12);
  const t = (id, due, o = {}) => ({ id, title: id, body: '', status: 'open', due, parent_id: null, position: 0, ...o });
  const tasks = [t('late', '2026-11-03'), t('today', '2026-11-05'), t('fri', '2026-11-06'), t('later', '2026-12-01'),
    t('none', null), t('done', '2026-11-05', { status: 'done' }), t('kid', '2026-11-05', { parent_id: 'today' })];
  const show = (seg) => phoneGroups(tasks, {}, seg, now).map((g) => [g.label, g.tasks.map((x) => x.id)]);
  assert.deepEqual(show('today'), [['Overdue', ['late']], ['Today', ['today']]]);   // the sub-task goes with its parent
  assert.deepEqual(show('upcoming'), [['Tomorrow', ['fri']]]);
  assert.deepEqual(show('all'), [['Overdue', ['late']], ['Today', ['today']], ['Tomorrow', ['fri']], ['Later', ['later']], ['No date', ['none']]]);
});
