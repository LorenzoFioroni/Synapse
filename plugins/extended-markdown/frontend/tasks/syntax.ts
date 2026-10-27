import type { TaskBlockOptions } from '../../../../frontend/src/plugins/markdown.js';

export const TASKS_INFO = /^tasks(?:\s|$)/i;
const ID = /^[a-z0-9]{4,32}$/;

export function parseTaskBlockInfo(info: string): TaskBlockOptions | null {
  const parts = info.trim().split(/\s+/);
  if (!TASKS_INFO.test(info.trim())) return null;
  const out: TaskBlockOptions = { view: 'list', group: 'none', completed: 'bottom' };
  for (const p of parts.slice(1)) {
    if (p === 'list' || p === 'table') out.view = p;
    else if (p === 'group=deadline' || p === 'group=none') out.group = p.slice(6) as TaskBlockOptions['group'];
    else if (/^completed=(inline|bottom|hide)$/.test(p)) out.completed = p.slice(10) as TaskBlockOptions['completed'];
  }
  return out;
}

/** One task id per line; a Markdown list marker is accepted for easy conversion from a list. */
export function taskBlockIds(content: string): string[] {
  const out: string[] = [];
  for (const line of content.split('\n')) {
    const id = line.trim().replace(/^[-*+]\s+/, '');
    if (ID.test(id) && !out.includes(id)) out.push(id);
  }
  return out;
}

export function taskBlocksToRefs(markdown: string): string {
  const lines = markdown.split('\n');
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const m = /^([ \t]*)(`{3,}|~{3,})(.*)$/.exec(lines[i]);
    const options = m && parseTaskBlockInfo(m[3]);
    if (!m || !options) { out.push(lines[i]); continue; }
    const body: string[] = [];
    let j = i + 1;
    for (; j < lines.length; j++) {
      const close = /^([ \t]*)(`{3,}|~{3,})\s*$/.exec(lines[j]);
      if (close && close[2][0] === m[2][0] && close[2].length >= m[2].length) break;
      body.push(lines[j]);
    }
    out.push(...taskBlockIds(body.join('\n')).map((id) => `{{task:${id}}}`));
    i = j < lines.length ? j : lines.length - 1;
  }
  return out.join('\n');
}
