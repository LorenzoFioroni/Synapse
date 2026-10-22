// Figures in a note exported as plain Markdown (views/notes/portable.ts calls portableFigures
// through the FiguresHook). No DOM (tests/js/portable.test.mjs).
import { type Lookup, type RefKind, refNames, refParts, refPlain } from '../../../views/notes/crossref.js';
import { textRefs } from '../../../views/notes/equations.js';
import { mask } from '../../../views/notes/mdscan.js';
import { figureLabel, figuresOf } from './figures.js';
import { galleryMarkdown, italic } from './gallery.js';

/**
 * A note's figures (figures.ts) for other programs (views/notes/portable.ts), after its citations were written out: a gallery becomes its images on one
 * line and the figure's caption below (galleryMarkdown), an image's caption line gets its number,
 * "**Fig. 2.** *Caption.*", \label{…} is dropped from captions, and \ref{…} to a figure becomes
 * its number (references to equations stay as they are, like the formulas).
 */
export function portableFigures(md: string): string {
  const { figures, labels } = figuresOf(md);
  for (const f of [...figures].reverse()) {
    if (f.kind === 'gallery') md = md.slice(0, f.from) + galleryMarkdown(f.gallery!, f.number) + md.slice(f.to);
    else {
      const c = f.image!.caption;
      md = md.slice(0, f.captionFrom!) + `**${figureLabel(f.number!)}.** ${italic(c)}` + md.slice(f.to);
    }
  }
  if (!labels.size) return md;
  const { spans } = mask(md);
  // equations win a name both use, as in the app; a reference that names an equation stays as it is
  const inMath = new Set(spans.filter((sp) => sp.kind === 'math' || sp.kind === 'mathblock')
    .flatMap((sp) => [...md.slice(sp.from, sp.to).matchAll(/\\label\s*\{([^{}]*)\}/g)].map((m) => m[1].trim())));
  const lookup: Lookup = (name) => { const n = inMath.has(name.trim()) ? null : labels.get(name.trim()); return n == null ? null : { type: 'fig', num: n }; };
  for (const r of textRefs(md).reverse()) {
    if (spans.some((sp) => sp.kind !== 'gallery' && r.from >= sp.from && r.from < sp.to)) continue;
    const names = refNames(r.kind, r.name);
    if (!names.length || names.some((n) => !lookup(n))) continue;
    md = md.slice(0, r.from) + refPlain(refParts(r.kind as RefKind, names, lookup)) + md.slice(r.to);
  }
  return md;
}
