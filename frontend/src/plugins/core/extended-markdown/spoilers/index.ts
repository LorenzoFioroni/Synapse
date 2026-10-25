// Spoilers: inline ||hidden text|| and collapsible :::spoiler blocks.
import type { MarkdownExtension } from '../extension.js';
import { installSpoilers } from './markdown.js';

export const spoilers: MarkdownExtension = {
  id: 'spoilers',
  markdown: installSpoilers,
  exportStatic(root) {
    root.querySelectorAll<HTMLDetailsElement>('.spoiler-block').forEach((el) => { el.open = true; });
    root.querySelectorAll<HTMLElement>('.spoiler-content').forEach((el) => { el.hidden = false; });
    root.querySelectorAll('.spoiler-toggle').forEach((el) => el.remove());
  },
  attach() {
    const click = (event: MouseEvent) => {
      const button = (event.target as Element).closest<HTMLButtonElement>('.spoiler-toggle');
      const content = button?.nextElementSibling as HTMLElement | null;
      if (!button || !content?.classList.contains('spoiler-content')) return;
      content.hidden = !content.hidden;
      button.setAttribute('aria-expanded', String(!content.hidden));
      button.textContent = content.hidden ? 'Show spoiler' : 'Hide spoiler';
    };
    document.addEventListener('click', click);
    return () => document.removeEventListener('click', click);
  },
};
