import type MarkdownIt from 'markdown-it';
import { esc, lineAttr, renderDefaults } from '../../../../views/notes/render.js';
import { parseTaskBlockInfo, taskBlockIds } from './syntax.js';

/** Turn a ```tasks fence into a semantic task collection. */
export function tasksMarkdown(md: MarkdownIt): void {
  const fallback = md.renderer.rules.fence!;
  md.renderer.rules.fence = (tokens, i, options, env, self) => {
    const tok = tokens[i];
    const parsed = parseTaskBlockInfo(tok.info || '');
    if (!parsed) return fallback(tokens, i, options, env, self);
    const ids = taskBlockIds(tok.content);
    return `<div class="task-block-wrap"${lineAttr(tok)} data-task-block data-task-ids="${esc(ids.join(','))}" data-task-view="${parsed.view}" data-task-group="${parsed.group}" data-task-completed="${parsed.completed}">${renderDefaults.taskBlockHtml(ids, parsed)}</div>\n`;
  };
}
