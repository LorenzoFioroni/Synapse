// Public Markdown SDK: shared scanners, reference conventions and rendering helpers.
// Plugin bundles import this host module once; mutable renderer defaults never get copied.
export { scan, mask, GALLERY_INFO } from '../views/notes/mdscan.js';
export type { Scan, Span } from '../views/notes/mdscan.js';
export { refNames, refParts, refPlain } from '../views/notes/crossref.js';
export type { Lookup, RefKind } from '../views/notes/crossref.js';
export { textRefs } from '../views/notes/equations.js';
export { anchorsHtml, esc, imageSrc, lineAttr } from '../views/notes/render.js';
export type { TaskBlockOptions } from '../views/notes/render.js';
export type { FiguresHook, FigureInText, ExtendedMarkdownHook, Spoiler } from '../views/notes/hooks.js';
import type { FiguresHook, ExtendedMarkdownHook } from '../views/notes/hooks.js';
export interface MarkdownApi {
  register(provider: ExtendedMarkdownHook): () => void;
  figures: { register(provider: FiguresHook): () => void };
}

import { renderDefaults } from '../views/notes/render.js';
import type { TaskBlockOptions } from '../views/notes/render.js';
export const renderTaskBlock = (ids: string[], options: TaskBlockOptions): string => renderDefaults.taskBlockHtml(ids, options);
