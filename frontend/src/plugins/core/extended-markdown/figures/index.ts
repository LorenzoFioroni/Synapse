// Galleries and numbered figures, an extension of Extended Markdown: ```gallery blocks drawn as
// carousels, and figures (an image with a caption line, or a captioned gallery) numbered
// Fig. 1, 2, … for \ref and \cref. The notes editor, read mode and the exports use it through the
// `figures` hook in views/notes/hooks.ts; turned off, a gallery reads as the Markdown it holds
// and captions as the text they are.
import { noteHooks, setNoteHook } from '../../../../views/notes/hooks.js';
import type { MarkdownExtension } from '../extension.js';
import { installGalleries } from './galleryView.js';
import { figuresHook } from './hook.js';

export const figures: MarkdownExtension = {
  id: 'figures',
  attach() {
    const uninstall = installGalleries();
    setNoteHook('figures', figuresHook);
    return () => {
      if (noteHooks.figures === figuresHook) setNoteHook('figures', null);
      uninstall();
    };
  },
};
