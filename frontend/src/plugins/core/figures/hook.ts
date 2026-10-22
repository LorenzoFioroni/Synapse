// What the Figures plugin hands the notes editor (views/notes/hooks.ts, FiguresHook). No DOM is
// touched on import, so the Node tests set it with setNoteHook('figures', figuresHook).
import type { FiguresHook } from '../../../views/notes/hooks.js';
import { staticGalleries } from './exportStatic.js';
import { figureLabel, figuresOf } from './figures.js';
import { figuresMarkdown } from './markdown.js';
import { portableFigures } from './portable.js';

export const figuresHook: FiguresHook = {
  markdown: figuresMarkdown,
  figuresOf,
  label: figureLabel,
  portable: portableFigures,
  exportStatic: staticGalleries,
};
