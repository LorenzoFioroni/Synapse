// Galleries and figures in the notes' Markdown renderer (views/notes/render.ts, which calls
// figuresMarkdown through the FiguresHook): ```gallery blocks become carousels, an image with a
// caption line and a captioned gallery become numbered figures, and env.figLabels tells \ref
// what their labels stand for. No DOM here (tests/js/render.test.mjs).
import type MarkdownIt from 'markdown-it';
import type { StateCore, Token as MdToken } from 'markdown-it';
import { anchorsHtml, esc, imageSrc, lineAttr } from '../../../../views/notes/render.js';
import { figureLabel, imageFigureAt } from './figures.js';
import { galleryCaptioned, galleryInfo, panelLabel, parseGallery } from './gallery.js';

/**
 * ```gallery blocks become tokens of their own, with each caption an inline token, so captions are
 * parsed like any text: math (numbered and with the note's macros), citations, links, \eqref.
 * Runs after block parsing, before inline parsing.
 */
function galleryRule(state: StateCore): void {
  if (!state.tokens.some((t) => t.type === 'fence' && galleryInfo(t.info))) return;
  const out: MdToken[] = [];
  for (const tok of state.tokens) {
    if (tok.type !== 'fence' || !galleryInfo(tok.info)) { out.push(tok); continue; }
    const g = parseGallery(tok.content, tok.info);
    const first = (tok.map?.[0] ?? 0) + 1;   // the line after the opening fence
    const block = (type: string, nesting: 1 | 0 | -1, meta: unknown = null, line: number | null = null) => {
      const t = new state.Token(type, '', nesting);
      t.block = true; t.level = tok.level; t.meta = meta;
      if (line != null) t.map = [line, line + 1];
      return t;
    };
    const inline = (content: string, line: number) => {
      const t = new state.Token('inline', '', 0);
      t.content = content; t.map = [line, line + 1]; t.children = []; t.level = tok.level + 1;
      return t;
    };
    // ``fig`` (the figure's number) is filled in by figureRule
    const open = block('gallery_open', 1, { width: g.width, count: g.panels.length, captioned: galleryCaptioned(g), labels: g.labels, fig: null });
    open.map = tok.map;
    out.push(open);
    g.panels.forEach((p, i) => {
      const meta = { ...p, index: i, count: g.panels.length, mainCaption: !!g.caption, fig: null };
      out.push(block('gallery_panel_open', 1, meta, first + p.line), inline(p.caption, first + p.line), block('gallery_panel_close', -1));
    });
    out.push(block('gallery_nav', 0, { count: g.panels.length }));
    if (g.caption) {
      const line = first + (g.captionLine ?? 0);
      out.push(block('gallery_caption_open', 1, { fig: null }, line), inline(g.caption, line), block('gallery_caption_close', -1));
    }
    out.push(block('gallery_close', -1));
  }
  state.tokens = out;
}

/**
 * Figures: a top-level paragraph that is an image and its caption line (imageFigureAt, the rule the
 * live preview and the Markdown export apply to the text) becomes figure tokens, its caption an
 * inline token without the emphasis markers and \label. Then every figure, and every gallery with
 * a caption, gets its number in order, and env.figLabels maps labels to numbers for \ref.
 * Runs after galleryRule, before inline parsing.
 */
function figureRule(state: StateCore): void {
  const toks = state.tokens;
  let lines: string[] | null = null;
  const out: MdToken[] = [];
  for (let i = 0; i < toks.length; i++) {
    const t = toks[i];
    if (t.type === 'paragraph_open' && t.level === 0 && t.map && t.map[1] - t.map[0] === 2
        && toks[i + 1]?.type === 'inline' && toks[i + 2]?.type === 'paragraph_close') {
      lines ||= state.src.split('\n');
      const fig = imageFigureAt(lines, t.map[0]);
      if (fig) {
        const open = new state.Token('figure_open', 'figure', 1);
        open.block = true; open.map = t.map; open.meta = { ...fig, fig: null };
        const caption = toks[i + 1];
        caption.content = fig.caption;
        caption.map = [t.map[0] + 1, t.map[0] + 2];
        const close = new state.Token('figure_close', 'figure', -1);
        close.block = true;
        out.push(open, caption, close);
        i += 2;
        continue;
      }
    }
    out.push(t);
  }
  state.tokens = out;
  const labels = new Map<string, string>();
  let n = 0, inGallery: number | null = null;
  for (const t of out) {
    if (t.type === 'figure_open' || (t.type === 'gallery_open' && t.meta.captioned)) {
      t.meta.fig = ++n;
      for (const name of t.meta.labels) if (!labels.has(name)) labels.set(name, String(n));
      if (t.type === 'gallery_open') inGallery = n;
    } else if (t.type === 'gallery_close') inGallery = null;
    else if (inGallery != null && (t.type === 'gallery_panel_open' || t.type === 'gallery_caption_open')) {
      t.meta.fig = inGallery;
      // a label in a panel's caption names that panel: "2(b)"
      if (t.type === 'gallery_panel_open') for (const name of t.meta.labels) if (!labels.has(name)) labels.set(name, `${inGallery}(${panelLabel(t.meta.index)})`);
    }
  }
  state.env.figLabels = labels;
}

/** Add the gallery and figure rules and their renderers to a notes renderer. They run before the
 *  renderer's own rule for galleries (gallery_plain, which reads one as plain Markdown). */
export function figuresMarkdown(md: MarkdownIt): void {
  md.core.ruler.before('gallery_plain', 'gallery', galleryRule);
  md.core.ruler.after('gallery', 'figures', figureRule);
  const R = md.renderer.rules;
  // a gallery: a carousel in the app (galleryView.ts moves it), panels (a), (b), … in exports (exportStatic.ts)
  const figNum = (n: number, stop = '.') => `<span class="fig-num">${figureLabel(n)}${stop}</span>`;
  R.figure_open = (t, i) => {
    const f = t[i].meta;
    return `<figure class="figure"${lineAttr(t[i])} data-fig="${f.fig}">${anchorsHtml(f.labels)}<div class="figure-image">`
      + `<img src="${esc(imageSrc(f.src))}" alt="${esc(f.alt)}" loading="lazy"${f.width ? ` width="${Number(f.width)}"` : ''}></div>`
      + `<figcaption class="figure-caption">${figNum(f.fig)} `;
  };
  R.figure_close = () => '</figcaption></figure>\n';
  R.gallery_open = (t, i) => {
    const { width, count, fig, labels } = t[i].meta;
    return `<figure class="gallery"${lineAttr(t[i])} data-count="${count}"${fig ? ` data-fig="${fig}"` : ''}${width ? ` style="max-width:${Number(width)}px"` : ''}>`
      + `${fig ? anchorsHtml(labels) : ''}<div class="gallery-view"><div class="gallery-track">${count ? '' : '<div class="gallery-empty">Empty gallery: write one image per line, e.g. ![](attachments/x.png)</div>'}`;
  };
  R.gallery_panel_open = (t, i) => {
    const p = t[i].meta;
    return `<div class="gallery-panel"${lineAttr(t[i])} data-index="${p.index}">${p.fig ? anchorsHtml(p.labels) : ''}<div class="gallery-image">`
      + `<img src="${esc(imageSrc(p.src))}" alt="${esc(p.alt)}" loading="lazy"${p.width ? ` width="${Number(p.width)}"` : ''}></div>`
      // the label hangs to the left of the caption, so the caption's text itself is centred
      + `<div class="gallery-panel-caption"><span class="gallery-caption-text${p.caption.trim() ? '' : ' empty'}">`
      + panelHead(p);
  };
  R.gallery_panel_close = () => '</span></div></div>';
  // what goes before a panel's caption: its label, "(b)"; when the gallery is a figure with no
  // caption of its own, the panels' captions carry its number: "Fig. 2(b)", or "Fig. 2." alone
  function panelHead(p: { index: number; count: number; caption: string; mainCaption: boolean; fig: number | null }): string {
    const num = p.fig && !p.mainCaption && p.caption.trim();
    if (p.count > 1) return `<span class="gallery-label">${num ? figNum(p.fig!, '') : ''}(${panelLabel(p.index)})</span> `;
    return num ? `${figNum(p.fig!)} ` : '';
  }
  R.gallery_nav = (t, i) => {
    const { count } = t[i].meta;
    if (count < 2) return '</div></div>';
    const dots = Array.from({ length: count }, (_, k) => `<button type="button" class="gallery-dot${k ? '' : ' active'}" data-index="${k}" aria-label="Image (${panelLabel(k)})"></button>`).join('');
    return '</div><button type="button" class="gallery-btn gallery-prev" aria-label="Previous image" disabled>‹</button>'
      + '<button type="button" class="gallery-btn gallery-next" aria-label="Next image">›</button></div>'
      + `<div class="gallery-dots">${dots}</div>`;
  };
  R.gallery_caption_open = (t, i) => `<figcaption class="gallery-caption"${lineAttr(t[i])}>${t[i].meta?.fig ? `${figNum(t[i].meta.fig)} ` : ''}`;
  R.gallery_caption_close = () => '</figcaption>';
  R.gallery_close = () => '</figure>\n';
}
