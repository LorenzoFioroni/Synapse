// Figures and their numbers. A figure is
//  * an image with a caption: a line holding only the image, the line right below it wholly in
//    *…* (or _…_), with blank lines (or the start or end of the note) around the two:
//
//        ![](attachments/plot.png)
//        *Photon number versus $N$. \label{fig:photons}*
//
//  * or a gallery (gallery.ts) with a caption of its own or on any panel.
//
// Figures are numbered through the note in order, Fig. 1, Fig. 2, …; an image or gallery without a
// caption is not a figure and gets no number. \label{name} in a caption names the figure, and
// \ref{name} in the text gives its number (\eqref{name} the number in parentheses), as for
// equations (equations.ts); equations win when both use the same name.
//
// The renderer (render.ts) finds figures in its tokens, the live preview and the Markdown export
// in the text (figuresOf): both apply imageFigureAt to the note's lines, so they agree.
// No DOM here (tests/js/figures.test.mjs).
import { type Gallery, galleryCaptioned, panelLabel, parseGallery, takeLabels, wholeEmphasis } from './gallery.js';
import { type Scan, scan } from './mdscan.js';

const IMAGE_LINE = /^ {0,3}!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)\s*$/;
const blank = (s: string | undefined) => s === undefined || !s.trim();

/** An image with its caption, as imageFigureAt finds it. */
export interface ImageFigure { src: string; alt: string; width: number | null; caption: string; labels: string[] }

/**
 * The figure that starts at ``lines[i]`` (lines of a note's body), or null: the line holds only an
 * image (at most three spaces in), the next line is wholly emphasised (*…* or _…_, at most three
 * spaces in), and the lines before and after the pair are blank or absent.
 */
export function imageFigureAt(lines: string[], i: number): ImageFigure | null {
  const m = IMAGE_LINE.exec(lines[i] ?? '');
  const cap = lines[i + 1];
  if (!m || cap === undefined || /^\s{4}/.test(cap) || !blank(i > 0 ? lines[i - 1] : undefined) || !blank(lines[i + 2])) return null;
  const inner = wholeEmphasis(cap);
  if (inner === null) return null;
  const { text, labels } = takeLabels(inner);
  const [alt, width] = m[1].split('|');
  return { src: m[2], alt: alt.trim(), width: Number((width || '').trim()) || null, caption: text, labels };
}

/** A figure of a note, found in its text. Offsets are into the text given to figuresOf. */
export interface FigureInText {
  kind: 'image' | 'gallery';
  from: number;              // start of the image line, or of the gallery's opening fence
  to: number;                // end of the caption line, or of the closing fence
  captionFrom?: number;      // an image's caption line
  number: number | null;     // null: no caption, not numbered (galleries only)
  labels: string[];          // the figure's own (a gallery's panels have theirs)
  gallery?: Gallery;
  image?: ImageFigure;
}

export interface Figures { figures: FigureInText[]; labels: Map<string, string> }

/** The figures of a note (front matter allowed) and the numbers their labels stand for;
 *  ``scanned``: mdscan.scan of the same text, when the caller has it. */
export function figuresOf(text: string, scanned?: Scan): Figures {
  text = text.replace(/\r\n?/g, '\n');
  const s = scanned || scan(text);
  const body = text.slice(s.bodyStart);
  const lines = body.split('\n');
  const starts: number[] = [];
  let pos = s.bodyStart;
  for (const l of lines) { starts.push(pos); pos += l.length + 1; }
  const galleries = s.spans.filter((sp) => sp.kind === 'gallery');
  const inGallery = (at: number) => galleries.some((g) => at >= g.from && at <= g.to);
  const lineIndex = (at: number) => { let lo = 0, hi = starts.length - 1; while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= at) lo = mid; else hi = mid - 1; } return lo; };

  const found: FigureInText[] = [];
  for (const g of galleries) {
    const raw = text.slice(g.from, g.to).split('\n');
    const info = /^\s*(?:`{3,}|~{3,})(.*)$/.exec(raw[0])?.[1] ?? 'gallery';
    const gallery = parseGallery((g.open ? raw.slice(1) : raw.slice(1, -1)).join('\n'), info);
    found.push({ kind: 'gallery', from: g.from, to: g.to, number: galleryCaptioned(gallery) ? 0 : null, labels: gallery.labels, gallery });
  }
  const seen = new Set<number>();
  for (const l of s.links) {
    if (l.kind !== 'image' || inGallery(l.from)) continue;
    const i = lineIndex(l.from);
    if (seen.has(i)) continue;
    seen.add(i);
    const image = imageFigureAt(lines, i);
    if (!image) continue;
    found.push({ kind: 'image', from: starts[i], to: starts[i + 1] + lines[i + 1].length, captionFrom: starts[i + 1], number: 0, labels: image.labels, image });
  }
  found.sort((a, b) => a.from - b.from);
  const labels = new Map<string, string>();
  let n = 0;
  for (const f of found) {
    if (f.number === null) continue;
    f.number = ++n;
    for (const name of f.labels) if (!labels.has(name)) labels.set(name, String(n));
    // a label in a panel's caption names that panel: "2(b)"
    f.gallery?.panels.forEach((p, i) => { for (const name of p.labels) if (!labels.has(name)) labels.set(name, `${n}(${panelLabel(i)})`); });
  }
  return { figures: found, labels };
}

/** "Fig. 3": how a figure's number is written. */
export const figureLabel = (n: number): string => `Fig. ${n}`;
