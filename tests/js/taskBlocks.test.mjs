import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as lib from '../../frontend/vendor/md/md.js';
import { tasksMarkdown } from '../../frontend/js/plugins/core/extended-markdown/tasks/markdown.js';
import { parseTaskBlockInfo, taskBlockIds, taskBlocksToRefs } from '../../frontend/js/plugins/core/extended-markdown/tasks/syntax.js';
import { createRenderer, renderDefaults } from '../../frontend/js/views/notes/render.js';

test('task block options and ids', () => {
  assert.deepEqual(parseTaskBlockInfo('tasks table group=deadline completed=hide'), { view: 'table', group: 'deadline', completed: 'hide' });
  assert.deepEqual(taskBlockIds('k7f3q2mx\n- m4n8p1rs\ninvalid!\nk7f3q2mx'), ['k7f3q2mx', 'm4n8p1rs']);
});

test('task fences render through the Tasks provider', () => {
  const before = renderDefaults.taskBlockHtml;
  renderDefaults.taskBlockHtml = (ids, options) => `<section data-view="${options.view}">${ids.join(',')}</section>`;
  try {
    const renderer = createRenderer(lib, {});
    tasksMarkdown(renderer.md);
    const html = renderer.render('```tasks table group=deadline\nk7f3q2mx\nm4n8p1rs\n```');
    assert.match(html, /class="task-block-wrap"[^>]*data-task-block[^>]*><section data-view="table">k7f3q2mx,m4n8p1rs<\/section>/);
  } finally { renderDefaults.taskBlockHtml = before; }
});

test('portable Markdown expands a task block to ordinary task references', () => {
  assert.equal(taskBlocksToRefs('Before\n```tasks\nk7f3q2mx\n- m4n8p1rs\n```\nAfter'),
    'Before\n{{task:k7f3q2mx}}\n{{task:m4n8p1rs}}\nAfter');
});
