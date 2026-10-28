export { createRenderer } from '../../../frontend/src/plugins/task-runtime.js';
export type { Renderer, TaskBlockOptions } from '../../../frontend/src/plugins/task-runtime.js';
import { fallbackTaskHtml, fallbackTaskBlockHtml } from '../../../frontend/src/plugins/task-runtime.js';
export const renderDefaults = { taskHtml: fallbackTaskHtml, taskBlockHtml: fallbackTaskBlockHtml };
