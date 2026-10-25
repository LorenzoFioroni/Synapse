// Run with: node --test tests/js   (also run by tests/test_frontend_js.py)
// The app's extension points without a DOM (extend.ts): registries, keys and the command
// palette's ranking.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  Registry, keyLabel, keyMatches, keyProblem, matchScore, normalKey, parseKey, rankCommands,
} from '../../frontend/js/extend.js';

const ev = (key, o = {}) => ({ key, code: o.code ?? (key.length === 1 && /[a-z]/i.test(key) ? `Key${key.toUpperCase()}` : ''), metaKey: !!o.meta, ctrlKey: !!o.ctrl, altKey: !!o.alt, shiftKey: !!o.shift });

test('a registry adds, replaces and removes, and tells its listeners', () => {
  const r = new Registry();
  let heard = 0;
  const off = r.subscribe(() => { heard += 1; });
  const a = r.register({ id: 'a', v: 1 });
  r.register({ id: 'b', v: 2 });
  assert.deepEqual(r.all().map((x) => x.id), ['a', 'b']);
  const a2 = r.register({ id: 'a', v: 3 });
  assert.equal(r.get('a').v, 3);
  a();   // the replaced entry's remover does not remove its replacement
  assert.equal(r.get('a').v, 3);
  a2();
  assert.equal(r.get('a'), undefined);
  assert.equal(heard, 4);
  off();
  r.register({ id: 'c' });
  assert.equal(heard, 4);
  assert.throws(() => r.register({}), /id/);
});

test('keys: parsing and normal form', () => {
  assert.deepEqual(parseKey('Mod-Shift-r'), { mod: true, shift: true, alt: false, ctrl: false, key: 'r' });
  assert.equal(parseKey('Alt-K').key, 'k');
  assert.equal(parseKey('Mod--').key, '-');
  assert.equal(parseKey('Hyper-x'), null);
  assert.equal(parseKey(''), null);
  assert.equal(normalKey('shift-mod-R'), 'Mod-Shift-r');
  assert.equal(normalKey('Cmd-,'), 'Mod-,');
});

test('keys: matching on a Mac and elsewhere', () => {
  assert.ok(keyMatches('Mod-Shift-r', ev('R', { meta: true, shift: true }), true));
  assert.ok(!keyMatches('Mod-Shift-r', ev('R', { ctrl: true, shift: true }), true));
  assert.ok(keyMatches('Mod-Shift-r', ev('R', { ctrl: true, shift: true }), false));
  assert.ok(!keyMatches('Mod-r', ev('r', { ctrl: true, shift: true }), false));
  // Option types another character on a Mac: the physical key still matches
  assert.ok(keyMatches('Mod-Alt-r', ev('®', { meta: true, alt: true, code: 'KeyR' }), true));
  assert.ok(keyMatches('F2', ev('F2'), false));
});

test('keys: labels', () => {
  assert.equal(keyLabel('Mod-Shift-r', true), '⇧⌘R');
  assert.equal(keyLabel('Mod-Shift-r', false), 'Ctrl+Shift+R');
  assert.equal(keyLabel('Alt-k', true), '⌥K');
});

test("a plugin's key needs Mod or Alt and must not be Synapse's", () => {
  assert.equal(keyProblem('Mod-Alt-r'), null);
  assert.equal(keyProblem('Alt-Shift-j'), null);
  assert.equal(keyProblem('F7'), null);
  assert.match(keyProblem('r'), /needs Mod or Alt/);
  assert.match(keyProblem('Shift-r'), /needs Mod or Alt/);
  assert.match(keyProblem('Mod-f'), /Synapse's own/);
  assert.match(keyProblem('mod-shift-K'), /Synapse's own/);
  assert.match(keyProblem('Mod-Shift-p'), /Synapse's own/);
  assert.match(keyProblem('Banana-r'), /not a key/);
});

test('the palette ranks by how well the letters match', () => {
  assert.equal(matchScore('', 'anything'), 0);
  assert.equal(matchScore('xyz', 'New Item'), -1);
  assert.ok(matchScore('new', 'New Item') > matchScore('new', 'Show the Newest'));
  assert.ok(matchScore('ni', 'New Item') > 0);
  assert.ok(matchScore('item', 'New Item') > matchScore('ni', 'New Item'));
  const cmds = [
    { id: 'a', title: 'New Item', run() {} },
    { id: 'b', title: 'Show Notes', run() {} },
    { id: 'c', title: 'Show Recently Added', source: 'Recently Added', run() {} },
    { id: 'd', title: 'Hidden', when: () => false, run() {} },
    { id: 'e', title: 'Broken', when: () => { throw new Error('x'); }, run() {} },
  ];
  assert.deepEqual(rankCommands(cmds, '').map((c) => c.id), ['a', 'b', 'c']);
  assert.deepEqual(rankCommands(cmds, '', ['c']).map((c) => c.id), ['c', 'a', 'b']);   // recent first
  assert.deepEqual(rankCommands(cmds, 'notes').map((c) => c.id), ['b']);
  assert.deepEqual(rankCommands(cmds, 'recent').map((c) => c.id), ['c']);   // the plugin's name counts too
  assert.deepEqual(rankCommands(cmds, 'hidden'), []);
  const two = [
    { id: 'forget', title: 'Forget When It Was Added', source: 'Recently Added', run() {} },
    { id: 'show', title: 'Show Recently Added', source: 'Recently Added', run() {} },
  ];
  assert.deepEqual(rankCommands(two, 'recent').map((c) => c.id), ['show', 'forget']);   // its title matches
});
