// Optional advanced Markdown: inline spoilers and collapsible blocks, sharing the normal renderer.
import type { PluginContext } from '../../api.js';
import { noteHooks, setNoteHook, type AdvancedMarkdownHook } from '../../../views/notes/hooks.js';
import { installSpoilers } from './markdown.js';
import { spoilerSpans } from './syntax.js';

export function activate(p: PluginContext): void {
  const hook: AdvancedMarkdownHook = {
    markdown: installSpoilers, spans: spoilerSpans,
    exportStatic(root) {
      root.querySelectorAll<HTMLDetailsElement>('.spoiler-block').forEach((el) => { el.open = true; });
      root.querySelectorAll<HTMLElement>('.spoiler-content').forEach((el) => { el.hidden = false; });
      root.querySelectorAll('.spoiler-toggle').forEach((el) => el.remove());
    },
  };
  setNoteHook('advanced', hook);
  const click = (event: MouseEvent) => {
    const button = (event.target as Element).closest<HTMLButtonElement>('.spoiler-toggle');
    const content = button?.nextElementSibling as HTMLElement | null;
    if (!button || !content?.classList.contains('spoiler-content')) return;
    content.hidden = !content.hidden;
    button.setAttribute('aria-expanded', String(!content.hidden));
    button.textContent = content.hidden ? 'Show spoiler' : 'Hide spoiler';
  };
  document.addEventListener('click', click);
  p.onUnload(() => {
    document.removeEventListener('click', click);
    if (noteHooks.advanced === hook) setNoteHook('advanced', null);
  });
}
