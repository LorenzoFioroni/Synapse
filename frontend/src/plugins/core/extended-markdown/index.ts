// Extended Markdown: syntax beyond Synapse's own Markdown, one folder per extension. Turning the
// package off renders notes as ordinary Markdown; their text never changes.
import type { PluginContext } from '../../api.js';
import { noteHooks, setNoteHook, type ExtendedMarkdownHook } from '../../../views/notes/hooks.js';
import type { MarkdownExtension } from './extension.js';
import { spoilers } from './spoilers/index.js';
import { spoilerSpans } from './spoilers/syntax.js';

const EXTENSIONS: MarkdownExtension[] = [spoilers];

export function activate(p: PluginContext): void {
  const hook: ExtendedMarkdownHook = {
    markdown(md) { for (const ext of EXTENSIONS) ext.markdown(md); },
    spoilers: spoilerSpans,
    exportStatic(root) { for (const ext of EXTENSIONS) ext.exportStatic?.(root); },
  };
  const detach = EXTENSIONS.map((ext) => ext.attach?.()).filter((fn) => !!fn);
  setNoteHook('extended', hook);
  p.onUnload(() => {
    for (const fn of detach) fn();
    if (noteHooks.extended === hook) setNoteHook('extended', null);
  });
}
