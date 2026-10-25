import type { MarkdownExtension } from '../extension.js';
import { tasksMarkdown } from './markdown.js';
import { TASKS_INFO, taskBlocksToRefs } from './syntax.js';

export const taskBlocks: MarkdownExtension = {
  id: 'task_blocks',
  markdown: tasksMarkdown,
  fence: (info) => TASKS_INFO.test(info.trim()),
  portable: taskBlocksToRefs,
};
