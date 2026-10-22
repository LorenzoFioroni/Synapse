// Figures, a core plugin (plugins/figures): ```gallery blocks drawn as carousels, and figures (an
// image with a caption line, or a captioned gallery) numbered Fig. 1, 2, … for \ref and \cref.
// The notes editor, read mode and the exports use it through views/notes/hooks.ts; turned off,
// a gallery reads as the Markdown it holds and captions as the text they are.
import type { PluginContext } from '../../api.js';
import { noteHooks, setNoteHook } from '../../../views/notes/hooks.js';
import { installGalleries } from './galleryView.js';
import { figuresHook } from './hook.js';

export function activate(p: PluginContext): void {
  const uninstall = installGalleries();
  setNoteHook('figures', figuresHook);
  p.onUnload(() => {
    if (noteHooks.figures === figuresHook) setNoteHook('figures', null);
    uninstall();
  });
}
