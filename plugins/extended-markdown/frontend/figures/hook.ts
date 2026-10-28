// What the figures extension hands the notes editor (views/notes/hooks.ts, FiguresHook). No DOM is
// touched on import, so the Node tests set it with setNoteHook('figures', figuresHook).
import type { FiguresHook } from '../../../../frontend/src/plugins/markdown.js';
import { staticGalleries } from './exportStatic.js';
import { figureLabel, figuresOf } from './figures.js';
import { newGallery } from './gallery.js';
import { figuresMarkdown } from './markdown.js';
import { portableFigures } from './portable.js';

export const figuresHook: FiguresHook = {
  markdown: figuresMarkdown,
  figuresOf,
  label: figureLabel,
  portable: portableFigures,
  exportStatic: staticGalleries,
  gallery: newGallery,
};
