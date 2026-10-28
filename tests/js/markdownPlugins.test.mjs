import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { createRenderer } from '../../frontend/js/views/notes/render.js';
import { noteHooks } from '../../frontend/js/views/notes/hooks.js';
import { registerMarkdown, registerFigures } from '../../frontend/js/plugins/markdownHost.js';
const provider = tag => ({
  markdown(md) { const before = md.renderer.rules.code_inline; md.renderer.rules.code_inline = (...args) => `<${tag}>${before(...args)}</${tag}>`; },
  spoilers: () => [], exportStatic() {}, fence: () => false, portable: text => text + tag,
});
test('Markdown providers compose, and retained renderers rebuild on disposal', () => {
  const render = createRenderer(lib);
  const first = registerMarkdown(provider('first'));
  const second = registerMarkdown(provider('second'));
  try {
    assert.match(render.render('`code`'), /<second><first><code>code<\/code><\/first><\/second>/);
    assert.equal(noteHooks.extended.portable('x'), 'xfirstsecond');
    first();
    assert.match(render.render('`code`'), /<second><code>code<\/code><\/second>/);
    assert.doesNotMatch(render.render('`code`'), /first/);
  } finally { first(); second(); }
  assert.equal(noteHooks.extended, null);
  assert.doesNotMatch(render.render('`code`'), /first|second/);
});
test('figure ownership is explicit and disposal is idempotent', () => {
  const provider = { markdown() {}, figuresOf: () => ({ figures: [], labels: new Map() }), label: String,
    portable: text => text, exportStatic() {}, gallery: () => '' };
  const off = registerFigures(provider);
  assert.equal(noteHooks.figures, provider);
  assert.throws(() => registerFigures(provider), /already registered/);
  off(); off();
  assert.equal(noteHooks.figures, null);
});
