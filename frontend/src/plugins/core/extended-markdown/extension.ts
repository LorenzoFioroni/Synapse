// One piece of extra Markdown syntax in the Extended Markdown package. Its `id` is also the key of
// the package setting that turns it on or off (plugins/extended-markdown/manifest.json).
import type MarkdownIt from 'markdown-it';

export interface MarkdownExtension {
  id: string;
  /** Adds its markdown-it rules: Read mode, embeds and exports share the one renderer. */
  markdown?(md: MarkdownIt): void;
  /** Prepares a rendered copy for HTML/PDF export (reveal what is interactive, say). */
  exportStatic?(root: HTMLElement): void;
  /** Starts what it needs while on (page-wide listeners, a note hook); returns what undoes it. */
  attach?(): () => void;
}
