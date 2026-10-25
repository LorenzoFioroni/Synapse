// One piece of extra Markdown syntax in the Extended Markdown package.
import type MarkdownIt from 'markdown-it';

export interface MarkdownExtension {
  id: string;
  /** Adds its markdown-it rules: Read mode, embeds and exports share the one renderer. */
  markdown(md: MarkdownIt): void;
  /** Prepares a rendered copy for HTML/PDF export (reveal what is interactive, say). */
  exportStatic?(root: HTMLElement): void;
  /** Starts page-wide behaviour (event listeners); returns what undoes it. */
  attach?(): () => void;
}
