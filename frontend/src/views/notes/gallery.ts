// Galleries: several images shown as one carousel in the app, and as one figure with panels
// (a), (b), … in exports. Written as a fenced block whose info string is "gallery":
//
//   ```gallery|600                       ← optional width in pixels, as for ![alt|600](…)
//   ![](attachments/clean.png)            ← an image starts a panel
//   *Clean system.*                       ← the lines right below it are its caption
//   ![|300](attachments/speckle.png) With speckle, $\xi=1\,\mu$m.   ← or the rest of its line
//
//   Photon number versus $N$ [@key].      ← lines not attached to an image: the gallery's caption
//   ```
//
// A blank line ends a panel's caption. Captions are Markdown (math, citations, links), and a
// caption wrapped as a whole in *…* or _…_ (the usual way to caption an image) is unwrapped.
// The block is not code: the parser and mdscan.ts read its lines like any paragraph, so its images
// and citations count. No DOM here (tests/js/gallery.test.mjs).
import { GALLERY_INFO } from './mdscan.js';

export interface GalleryPanel {
  src: string;           // the image path (or URL) as written
  alt: string;           // the alt text, without its |width
  width: number | null;  // ![alt|300](…)
  caption: string;       // Markdown
  line: number;          // 0-based line of its image within the block's content
}

export interface Gallery {
  width: number | null;  // ```gallery|600
  caption: string;       // Markdown
  captionLine: number | null;
  panels: GalleryPanel[];
}

const IMAGE = /^!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/;

/** The options of a gallery's info string ("gallery", "gallery|600", "gallery 600px",
 *  "gallery width=600"), or null when the block is not a gallery. */
export function galleryInfo(info: string | null | undefined): { width: number | null } | null {
  const s = String(info || '').trim();
  if (!GALLERY_INFO.test(s)) return null;
  const m = /^gallery[\s|]+(?:width\s*=\s*)?(\d+)(?:px)?\b/i.exec(s);
  const width = m ? Number(m[1]) : null;
  return { width: width && width > 0 ? width : null };
}

/** A caption written as one emphasised span, *…* or _…_, without the markers. */
function unwrap(caption: string): string {
  const c = caption.trim();
  const m = /^([*_])(?!\1)([\s\S]*[^\s\\])\1$/.exec(c);
  return m && !m[2].includes(m[1]) ? m[2] : c;
}

/** Read the content of a gallery block (the lines between its fences). */
export function parseGallery(content: string, info = 'gallery'): Gallery {
  const panels: GalleryPanel[] = [];
  const captionLines: string[] = [];
  let captionLine: number | null = null;
  let current: { panel: GalleryPanel; lines: string[] } | null = null;
  const close = () => {
    if (current) current.panel.caption = unwrap(current.lines.join('\n'));
    current = null;
  };
  content.replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n').forEach((raw, n) => {
    let rest = raw.trim();
    if (!rest) { close(); return; }
    if (IMAGE.test(rest)) {
      close();
      // several images at the start of a line are several panels; the text after them is the
      // caption of the last one
      for (let m = IMAGE.exec(rest); m; m = IMAGE.exec(rest)) {
        close();
        const [alt, width] = m[1].split('|');
        const panel: GalleryPanel = { src: m[2], alt: alt.trim(), width: Number((width || '').trim()) || null, caption: '', line: n };
        panels.push(panel);
        current = { panel, lines: [] };
        rest = rest.slice(m[0].length).trim();
      }
      if (rest) current!.lines.push(rest);
      return;
    }
    if (current) current.lines.push(rest);
    else {
      if (captionLine === null) captionLine = n;
      captionLines.push(rest);
    }
  });
  close();
  return { width: galleryInfo(info)?.width ?? null, caption: unwrap(captionLines.join('\n')), captionLine, panels };
}

/** "a", "b", … "z", then "27", "28", …: the label of the i-th panel (0-based), shown as "(a)". */
export const panelLabel = (i: number): string => (i < 26 ? String.fromCharCode(97 + i) : String(i + 1));

/** How many panels a row of an exported figure holds: all of them up to three, two for four (a 2×2
 *  grid reads better than 3 + 1), else three. */
export const figureColumns = (n: number): number => (n <= 3 ? Math.max(1, n) : n === 4 ? 2 : 3);

/** Whether a caption already ends a sentence ("…", closing quotes or brackets after the mark). */
export const endsSentence = (text: string): boolean => /[.!?…][\s"'”’)\]*_$]*$/.test(text.trim());

/** The caption of the exported figure: the gallery's own caption, then each panel's as
 *  "(a) First. (b) Second.", every part ending as a sentence. Panels without a caption are left
 *  out, and a gallery of one image has no "(a)". */
export function figureCaption(caption: string, panels: string[]): string {
  const sentence = (s: string) => { const t = s.trim().replace(/\s*\n\s*/g, ' '); return endsSentence(t) ? t : `${t}.`; };
  const parts: string[] = [];
  if (caption.trim()) parts.push(sentence(caption));
  panels.forEach((c, i) => { if (c.trim()) parts.push(panels.length > 1 ? `(${panelLabel(i)}) ${sentence(c)}` : sentence(c)); });
  return parts.join(' ');
}

/**
 * A gallery as plain Markdown for other programs (portable.ts): its images on one line, so most
 * renderers set them side by side, then the figure caption in italics.
 */
export function galleryMarkdown(g: Gallery): string {
  const images = g.panels.map((p) => `![${p.alt}](${p.src})`).join(' ');
  const caption = figureCaption(g.caption, g.panels.map((p) => p.caption));
  if (!caption) return images;
  return `${images}\n\n${caption.includes('*') ? caption : `*${caption}*`}`;
}
