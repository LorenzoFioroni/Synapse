// Extended Markdown: syntax beyond Synapse's own Markdown, one folder per extension, each with a
// switch among the package's settings. Turning the package or an extension off renders notes as
// ordinary Markdown; their text never changes.
import type { PluginContext } from '../../api.js';
import { noteHooks, setNoteHook, type ExtendedMarkdownHook } from '../../../views/notes/hooks.js';
import type { MarkdownExtension } from './extension.js';
import { figures } from './figures/index.js';
import { spoilers } from './spoilers/index.js';
import { spoilerSpans } from './spoilers/syntax.js';

export const EXTENSIONS: MarkdownExtension[] = [figures, spoilers];

const noSpoilers = () => [];

export async function activate(p: PluginContext): Promise<void> {
  const running = new Map<string, () => void>();   // extension id → what turns it off
  let hook: ExtendedMarkdownHook | null = null;
  let stopped = false;

  const apply = (values: Record<string, unknown>) => {
    if (stopped) return;
    const wanted = EXTENSIONS.filter((ext) => values[ext.id] !== false);
    if (hook && wanted.length === running.size && wanted.every((ext) => running.has(ext.id))) return;
    for (const ext of EXTENSIONS) {
      const on = wanted.includes(ext);
      if (on && !running.has(ext.id)) running.set(ext.id, ext.attach?.() ?? (() => {}));
      else if (!on && running.has(ext.id)) { running.get(ext.id)!(); running.delete(ext.id); }
    }
    // a new hook object: renderers and the live preview rebuild when it changes
    hook = {
      markdown(md) { for (const ext of wanted) ext.markdown?.(md); },
      spoilers: running.has(spoilers.id) ? spoilerSpans : noSpoilers,
      exportStatic(root) { for (const ext of wanted) ext.exportStatic?.(root); },
    };
    setNoteHook('extended', hook);
  };
  // settings that cannot be read leave every extension on rather than none
  const refresh = async () => apply(await p.settings().catch(() => ({})));

  await refresh();
  // a switch changed in Settings (this window or another): the change feed says 'plugins'
  p.onChange((kinds) => { if (kinds.includes('plugins')) void refresh(); });
  p.onUnload(() => {
    stopped = true;
    for (const off of running.values()) off();
    running.clear();
    if (noteHooks.extended === hook) setNoteHook('extended', null);
  });
}
