// Markdown → HTML for notes (read mode, tables and math in the live preview, later export).
// No DOM access here, so it is unit-tested with Node (tests/js/render.test.mjs).
//
// Rules follow the notes syntax (docs/notes.md) and the backend parser:
//  * raw HTML is NOT rendered (shown as text): notes may later be written by an AI, and the
//    app must never run a script that came from a note;
//  * indented code blocks are off; fenced code only;
//  * $…$ closes on the same line (Pandoc rules), $$…$$ inline or as a block;
//  * [[Note|alias]], ![[Note]], [@key, p. 4], [see @a; @b], > [!note] callouts, - [ ] tasks;
//  * ```gallery blocks: images with captions, drawn as a carousel (gallery.ts, galleryView.ts);
//  * figures (an image with a caption line, or a captioned gallery) numbered Fig. 1, 2, … with
//    \label / \ref (figures.ts);
//  * display equations are numbered through the note and \eqref{label} resolved (equations.ts);
//  * math macros: the physics package, Settings, and \newcommand in the note (macros.ts).

import type MarkdownIt from 'markdown-it';
import type { RendererRule as MdRenderRule, StateBlock, StateCore, StateInline, Token as MdToken } from 'markdown-it';

export type MdLib = typeof import('../../../vendor/md/md.js');
import { figureLabel, imageFigureAt } from './figures.js';
import { galleryCaptioned, galleryInfo, panelLabel, parseGallery } from './gallery.js';
import { labelId, layoutTag, numberEquations, refText, resolveRefs } from './equations.js';
import { type Cite, attachmentOf, parseCites, referenceDefinition, splitWiki } from './mdscan.js';
import { type Definition, HAS_DEFINITIONS, type Macro, type Macros, baseMacros, macroSource, parseDefinitions, withDefinitions } from './macros.js';

/** What a renderer may know about a cited key (any CiteEntry fits). */
export interface CiteInfo { title?: string; authors?: string; year?: number | string | null; trashed?: boolean }

export interface RendererOptions {
  citeInfo?: (key: string) => CiteInfo | null;   // library info for a key (null if unknown)
  resolveNote?: (target: string) => string | null | undefined;   // path a [[target]] goes to; null: no such note, undefined: not known
  lineOffset?: number;                            // added to data-line (front-matter lines)
  equations?: string | (() => string);           // equation numbering: 'labelled' (default) or 'all'
  macros?: Macros | (() => Macros);              // KaTeX macros every note starts from (macros.ts)
}

export interface Renderer {
  md: MarkdownIt;
  render(src: string): string;
  renderInline(src: string): string;
  katex(tex: string, display: boolean, macros?: Macros): string;
}

/** What renderers use when not told otherwise (set by the notes view from Settings), so note
 *  embeds and other renderers number equations and know the macros the same way. */
export const renderDefaults: { equations: () => string; macros: () => Macros } = { equations: () => 'labelled', macros: () => baseMacros().macros };

const CALLOUTS: Record<string, string> = {
  note: 'Note', tip: 'Tip', warning: 'Warning', question: 'Question', quote: 'Quote',
  info: 'Info', important: 'Important', caution: 'Caution', example: 'Example', todo: 'To do',
};
const ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);

export function slugHeading(text: string): string {
  return String(text).toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').trim().replace(/\s+/g, '-');
}

/** Image source for a path written in a note (vault-relative), or the URL as given. */
export function imageSrc(target: string): string {
  if (/^(https?:)?\/\//i.test(target) || target.startsWith('data:image/')) return target;
  const clean = target.replace(/^\.?\//, '');
  if (clean.startsWith('attachments/')) return `/api/notes/${clean.split('/').map(encodeURIComponent).join('/')}`;
  return `/api/notes/attachments/${clean.split('/').map(encodeURIComponent).join('/')}`;
}

/** A short label for a citation: "Smith et al. 2021, p. 4" when the item is known. */
export function citeLabel(c: Pick<Cite, 'key' | 'prefix' | 'suppress' | 'locator'>, info: CiteInfo | null | undefined): string {
  let base = `@${c.key}`;
  if (info && (info.authors || info.year)) {
    const fams = String(info.authors || '').split(';').map((a) => a.split(',')[0].trim()).filter(Boolean);
    const who = fams.length === 0 ? '' : fams.length === 1 ? fams[0] : fams.length === 2 ? `${fams[0]} & ${fams[1]}` : `${fams[0]} et al.`;
    base = c.suppress ? String(info.year || '') : [who, info.year].filter(Boolean).join(' ');
    base ||= `@${c.key}`;
  }
  const loc = (c.locator || '').replace(/(?:^|[\s,])att:\d+(?=$|[\s,])/gi, '').replace(/^[\s,]+|[\s,]+$/g, '');   // att: is for the app, not the reader
  return [c.prefix, base].filter(Boolean).join(' ') + (loc ? `, ${loc}` : '');
}

/* ---------------------------------------------------------------- markdown-it rules */
function mathInline() {
  return (state: StateInline, silent: boolean) => {
    const src = state.src, pos = state.pos, max = state.posMax;
    if (src[pos] !== '$') return false;
    if (src.startsWith('$$', pos)) {
      const close = src.indexOf('$$', pos + 2);
      if (close < 0 || close + 2 > max) return false;
      if (!silent) { const t = state.push('math_inline', 'math', 0); t.content = src.slice(pos + 2, close); t.markup = '$$'; t.meta = { display: true }; }
      state.pos = close + 2;
      return true;
    }
    if (pos + 1 >= max || ' \t\n$'.includes(src[pos + 1])) return false;
    const eol = src.indexOf('\n', pos);
    const lineEnd = eol < 0 || eol > max ? max : eol;
    let k = pos + 1;
    while (k < lineEnd) {
      if (src[k] === '\\') { k += 2; continue; }
      if (src[k] === '$' && !' \t\n'.includes(src[k - 1]) && !(k + 1 < src.length && '0123456789'.includes(src[k + 1]))) break;
      k++;
    }
    if (k >= lineEnd) return false;
    if (!silent) { const t = state.push('math_inline', 'math', 0); t.content = src.slice(pos + 1, k); t.markup = '$'; t.meta = { display: false }; }
    state.pos = k + 1;
    return true;
  };
}

function mathBlock(state: StateBlock, startLine: number, endLine: number, silent: boolean): boolean {
  const start = state.bMarks[startLine] + state.tShift[startLine];
  const max = state.eMarks[startLine];
  const first = state.src.slice(start, max).trim();
  if (!first.startsWith('$$')) return false;
  const rest = first.slice(2).replace(/\s+$/, '');
  if (first !== '$$' && rest.endsWith('$$')) return false;   // one-line $$x$$: inline rule
  if (silent) return true;
  let line = startLine;
  const content = [rest];
  let closed = false;
  while (++line < endLine) {
    const l = state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
    const t = l.trim();
    if (t.endsWith('$$')) { content.push(t.slice(0, -2)); closed = true; break; }
    content.push(l);
  }
  state.line = closed ? line + 1 : endLine;
  const tok = state.push('math_block', 'math', 0);
  tok.block = true;
  tok.content = content.join('\n').trim();
  tok.map = [startLine, state.line];
  tok.markup = '$$';
  return true;
}

function wikiRule(state: StateInline, silent: boolean): boolean {
  const src = state.src;
  let pos = state.pos;
  const embed = src[pos] === '!';
  if (embed) pos++;
  if (!src.startsWith('[[', pos)) return false;
  const close = src.indexOf(']]', pos + 2);
  if (close < 0 || close > state.posMax) return false;
  const inner = src.slice(pos + 2, close);
  if (!inner || /[[\]\n]/.test(inner)) return false;
  const w = splitWiki(inner);
  if (!w.target && !w.heading) return false;
  if (!silent) {
    const t = state.push(embed ? 'note_embed' : 'note_link', '', 0);
    t.meta = w;
    t.content = inner;
  }
  state.pos = close + 2;
  return true;
}

function citeRule(state: StateInline, silent: boolean): boolean {
  const src = state.src, pos = state.pos;
  if (src[pos] !== '[' || src[pos + 1] === '[') return false;
  if (pos > 0 && '![]\\'.includes(src[pos - 1])) return false;
  let end = pos + 1;
  while (end < state.posMax && !'[]\n'.includes(src[end])) end++;
  if (src[end] !== ']') return false;
  const inner = src.slice(pos + 1, end);
  if (!inner.includes('@')) return false;
  const next = src[end + 1];
  if ((next && '([]'.includes(next)) || referenceDefinition(src, pos, end + 1)) return false;
  const cites = parseCites(inner);
  if (!cites) return false;
  if (!silent) {
    const t = state.push('cite_group', '', 0);
    t.meta = cites;
    t.content = inner;
  }
  state.pos = end + 1;
  return true;
}

const TAG_START = /[A-Za-z]/;
const TAG_BODY = /^[\p{L}\p{N}_\-/]*/u;
const NOT_BEFORE_TAG = /[\p{L}\p{N}_/&#\])([]/u;

/** #tag (same rule as the backend: not after a word character, a slash, a bracket…). */
function tagRule(state: StateInline, silent: boolean): boolean {
  const src = state.src, pos = state.pos;
  if (src[pos] !== '#' || !TAG_START.test(src[pos + 1] || '')) return false;
  if (pos > 0 && NOT_BEFORE_TAG.test(src[pos - 1])) return false;
  const m = TAG_BODY.exec(src.slice(pos + 2, state.posMax))!;
  const tag = (src[pos + 1] + m[0]).replace(/[/-]+$/, '');
  if (!silent) { const t = state.push('tag', '', 0); t.content = tag; }
  state.pos = pos + 1 + tag.length;
  return true;
}

/** > [!type] Title  →  a callout. Runs after block parsing. */
function calloutRule(state: StateCore): void {
  const toks = state.tokens;
  for (let i = 0; i < toks.length; i++) {
    if (toks[i].type !== 'blockquote_open') continue;
    const p = toks[i + 1], inl = toks[i + 2];
    if (!p || p.type !== 'paragraph_open' || !inl || inl.type !== 'inline') continue;
    const m = /^\[!([A-Za-z]+)\]([+-]?)[ \t]*(.*)$/m.exec(inl.content.split('\n')[0]);
    if (!m) continue;
    const type = m[1].toLowerCase();
    toks[i].attrJoin('class', `callout callout-${CALLOUTS[type] ? type : 'note'}`);
    toks[i].meta = { callout: type, title: m[3] || CALLOUTS[type] || m[1] };
    const lines = inl.content.split('\n');
    lines.shift();
    inl.content = lines.join('\n');   // the inline rule parses it later
    if (!inl.content.trim()) { p.hidden = true; toks[i + 3].hidden = true; inl.hidden = true; }
  }
}

/** - [ ] / - [x] at the start of a list item → checkbox. */
function taskRule(state: StateCore): void {
  const toks = state.tokens;
  for (let i = 2; i < toks.length; i++) {
    const inl = toks[i];
    if (inl.type !== 'inline' || toks[i - 1].type !== 'paragraph_open' || toks[i - 2].type !== 'list_item_open') continue;
    const m = /^\[([ xX])\][ \t]/.exec(inl.content);
    if (!m) continue;
    const first = inl.children![0];
    if (!first || first.type !== 'text' || !/^\[[ xX]\][ \t]/.test(first.content)) continue;
    first.content = first.content.slice(4);
    const box = new state.Token('task_box', '', 0);
    box.meta = { checked: m[1] !== ' ', line: toks[i - 2].map?.[0] ?? null };
    inl.children!.unshift(box);
    toks[i - 2].attrJoin('class', m[1] === ' ' ? 'task' : 'task done');
  }
}

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
    else if (inGallery != null && (t.type === 'gallery_panel_open' || t.type === 'gallery_caption_open')) t.meta.fig = inGallery;
  }
  state.env.figLabels = labels;
}

/** data-line on block tokens, so a click in read mode can find the source line. */
function lineAttrs(state: StateCore): void {
  for (const t of state.tokens) {
    if (t.map && t.nesting >= 0 && t.block && t.type !== 'inline') t.attrSet('data-line', String(t.map[0]));
  }
}

/** \eqref{label} / \ref{label} written in the text (outside math): numbered in the core rule below. */
function eqRefRule(state: StateInline, silent: boolean): boolean {
  const src = state.src, pos = state.pos;
  if (src.charCodeAt(pos) !== 0x5C /* \ */) return false;
  const m = /^\\(eqref|ref)\{([^{}\n]*)\}/.exec(src.slice(pos, pos + 200));
  if (!m) return false;
  if (!silent) { const t = state.push('eq_ref', '', 0); t.meta = { kind: m[1], name: m[2].trim() }; }
  state.pos = pos + m[0].length;
  return true;
}

/** Collect the note's macro definitions (macros.ts), number its display equations and resolve
 *  \eqref / \ref (equations.ts), in document order. */
function equationsRule(mode: RendererOptions['equations'], macros: RendererOptions['macros']) {
  return (state: StateCore) => {
    const display: MdToken[] = [], all: MdToken[] = [];
    for (const t of state.tokens) {
      if (t.type === 'math_block') { display.push(t); all.push(t); }
      for (const c of t.children || []) {
        if (c.type === 'math_inline') { all.push(c); if (c.meta?.display) display.push(c); }
      }
    }
    // \newcommand & co. anywhere in the note's math apply to all of its formulas
    const defs: Definition[] = [];
    for (const t of all) {
      if (!HAS_DEFINITIONS.test(t.content)) continue;
      const p = parseDefinitions(t.content);
      if (!p.defs.length) continue;
      defs.push(...p.defs);
      t.content = p.rest;
      t.meta = { ...(t.meta || {}), defines: p.defs.map((d) => d.name) };
    }
    state.env.macros = withDefinitions((typeof macros === 'function' ? macros() : macros) || {}, defs);
    const m = typeof mode === 'function' ? mode() : mode;
    const res = numberEquations(display.map((t) => t.content), { mode: m || 'labelled' });
    display.forEach((t, i) => { t.content = res.formulas[i]; t.meta = { ...(t.meta || {}), labels: res.names[i] }; });
    for (const t of all) t.content = resolveRefs(t.content, res.labels);
    state.env.eqLabels = res.labels;
  };
}

/*
 * KaTeX output, remembered by formula. Rendering every formula again is most of the cost of
 * drawing a note, and read mode draws it after each change. The HTML depends only on the TeX,
 * display or inline, and the macros, so there is one memo per set of macros, compared by what
 * they define (renderers build a new macros object for every render). Shared by all renderers.
 */
const KATEX_MEMO_SIZE = 5000;
const katexMemos = new Map<string, Map<string, string>>();
const macroSigs = new WeakMap<Macros, string>();
const functionIds = new WeakMap<object, string>();
let lastFunctionId = 0;
const macroSig = (v: Macro) => (typeof v === 'string' ? v : macroSource.get(v)
  ?? functionIds.get(v) ?? (functionIds.set(v, `#${++lastFunctionId}`), `#${lastFunctionId}`));
function katexMemo(macros: Macros | undefined): Map<string, string> {
  let sig = macros ? macroSigs.get(macros) : '';
  if (sig === undefined) {
    sig = Object.entries(macros!).map(([k, v]) => `${k}\u0001${macroSig(v)}`).join('\u0002');
    macroSigs.set(macros!, sig);
  }
  let memo = katexMemos.get(sig);
  if (memo) { katexMemos.delete(sig); katexMemos.set(sig, memo); }   // most recently used last
  else {
    memo = new Map();
    katexMemos.set(sig, memo);
    if (katexMemos.size > 4) katexMemos.delete(katexMemos.keys().next().value!);
  }
  return memo;
}

/** A Markdown renderer for notes; ``lib`` is vendor/md/md.js. */
export function createRenderer(lib: MdLib, o: RendererOptions = {}): Renderer {
  const md = new lib.MarkdownIt({ html: false, linkify: true, typographer: false });
  md.disable('code');
  md.use(lib.footnote);
  md.block.ruler.before('fence', 'math_block', mathBlock, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
  md.inline.ruler.before('escape', 'math_inline', mathInline());
  md.inline.ruler.before('link', 'wiki', wikiRule);
  md.inline.ruler.before('image', 'wiki_embed', wikiRule);
  md.inline.ruler.before('link', 'cite', citeRule);
  md.inline.ruler.push('tag', tagRule);
  md.inline.ruler.before('escape', 'eq_ref', eqRefRule);
  md.core.ruler.after('inline', 'equations', equationsRule(o.equations ?? renderDefaults.equations, o.macros ?? renderDefaults.macros));
  md.core.ruler.after('block', 'callout', calloutRule);
  md.core.ruler.after('block', 'gallery', galleryRule);
  md.core.ruler.after('gallery', 'figures', figureRule);
  md.core.ruler.after('inline', 'task', taskRule);
  md.core.ruler.push('line_attrs', lineAttrs);

  const katex = (tex: string, display: boolean, macros?: Macros) => {
    const memo = katexMemo(macros);
    const key = `${display ? 'D' : 'i'}${tex}`;
    const hit = memo.get(key);
    if (hit !== undefined) return hit;
    let out: string;
    try {
      const html = lib.katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: 'ignore', trust: false, output: 'htmlAndMathml',
        macros: { ...(macros || {}) }, maxExpand: 1000 });   // a copy: a \gdef must not leak into other formulas
      out = display ? layoutTag(html) : html;
    } catch (e) {
      out = `<span class="math-error" title="${esc((e as Error).message)}">${esc(tex)}</span>`;
    }
    if (memo.size >= KATEX_MEMO_SIZE) memo.clear();
    memo.set(key, out);
    return out;
  };
  const R = md.renderer.rules;
  const anchors = (names: string[] | undefined) => (names || []).map((n) => `<span class="eq-anchor" id="${esc(labelId(n))}"></span>`).join('');
  // a formula that only defines macros shows nothing (like a LaTeX preamble)
  const onlyDefines = (t: MdToken) => t.meta?.defines && !t.content.trim();
  R.math_inline = (t, i, opts, env) => (onlyDefines(t[i]) ? ''
    : t[i].meta.display ? `<span class="math display">${anchors(t[i].meta.labels)}${katex(t[i].content, true, env.macros)}</span>`
      : `<span class="math">${katex(t[i].content, false, env.macros)}</span>`);
  R.math_block = (t, i, opts, env) => (onlyDefines(t[i]) ? ''
    : `<div class="math-block" data-line="${(t[i].map?.[0] ?? 0) + (o.lineOffset || 0)}">${anchors(t[i].meta?.labels)}${katex(t[i].content, true, env.macros)}</div>\n`);
  R.eq_ref = (t, i, opts, env) => {
    const { kind, name } = t[i].meta;
    // an equation's label, else a figure's (figureRule)
    const text = refText(kind, name, env.eqLabels || new Map()) ?? refText(kind, name, env.figLabels || new Map());
    return text == null ? `<span class="eqref missing" data-kind="${esc(kind)}" data-label="${esc(name)}" title="${esc(`No numbered equation or figure has \\label{${name}}`)}">(??)</span>`
      : `<a class="eqref" href="#${esc(labelId(name))}" data-kind="${esc(kind)}" data-label="${esc(name)}">${esc(text)}</a>`;
  };
  R.note_link = (t, i) => {
    const w = t[i].meta;
    const path = o.resolveNote ? o.resolveNote(w.target) : undefined;   // undefined = unknown, null = does not exist
    const label = w.alias || (w.target ? w.target + (w.heading ? ` › ${w.heading}` : '') : w.heading);
    return `<a class="wikilink${path === null && w.target ? ' unresolved' : ''}" data-note="${esc(w.target)}" data-heading="${esc(w.heading)}"${path ? ` data-path="${esc(path)}"` : ''} href="#">${esc(label)}</a>`;
  };
  R.note_embed = (t, i) => {
    const w = t[i].meta;
    const path = o.resolveNote ? o.resolveNote(w.target) : undefined;
    return `<div class="note-embed${path === null ? ' unresolved' : ''}" data-note="${esc(w.target)}" data-heading="${esc(w.heading)}"${path ? ` data-path="${esc(path)}"` : ''}><div class="embed-title">${esc(w.alias || w.target)}</div><div class="embed-body"></div></div>`;
  };
  R.cite_group = (t, i) => {
    const parts = (t[i].meta as Cite[]).map((c) => {
      const info = o.citeInfo ? o.citeInfo(c.key) : null;
      const cls = ['cite', info ? '' : 'missing', info?.trashed ? 'trashed' : ''].filter(Boolean).join(' ');
      const att = attachmentOf(c.locator);
      return `<a class="${cls}" href="#" data-key="${esc(c.key)}"${c.page ? ` data-page="${c.page}"` : ''}${att ? ` data-att="${att}"` : ''} title="${esc(info ? `${info.title || ''}` : `@${c.key} is not in your library`)}">${esc(citeLabel(c, info))}</a>`;
    });
    return `<span class="cite-group">(${parts.join('; ')})</span>`;
  };
  R.tag = (t, i) => `<span class="tag">#${esc(t[i].content)}</span>`;
  R.task_box = (t, i) => `<input type="checkbox" class="task-box"${t[i].meta.checked ? ' checked' : ''}${t[i].meta.line != null ? ` data-task-line="${t[i].meta.line + (o.lineOffset || 0)}"` : ''}>`;
  const defaultImage = R.image!;
  R.image = (t, i, opts, env, self) => {
    const tok = t[i];
    const alt = tok.children ? self.renderInlineAsText(tok.children, opts, env) : tok.content;
    const [label, width] = alt.split('|');
    tok.attrSet('src', imageSrc(tok.attrGet('src') || ''));
    tok.attrSet('loading', 'lazy');
    if (width && /^\d+$/.test(width.trim())) tok.attrSet('width', width.trim());
    if (tok.children) tok.children = [{ type: 'text', content: label.trim() } as MdToken];
    tok.content = label.trim();
    return defaultImage(t, i, opts, env, self);
  };
  // a gallery: a carousel in the app (galleryView.ts moves it), panels (a), (b), … in exports (exportNote.ts)
  const lineAttr = (t: MdToken) => { const v = t.attrGet('data-line'); return v != null ? ` data-line="${esc(v)}"` : ''; };
  const figNum = (n: number, stop = '.') => `<span class="fig-num">${figureLabel(n)}${stop}</span>`;
  R.figure_open = (t, i) => {
    const f = t[i].meta;
    return `<figure class="figure"${lineAttr(t[i])} data-fig="${f.fig}">${anchors(f.labels)}<div class="figure-image">`
      + `<img src="${esc(imageSrc(f.src))}" alt="${esc(f.alt)}" loading="lazy"${f.width ? ` width="${Number(f.width)}"` : ''}></div>`
      + `<figcaption class="figure-caption">${figNum(f.fig)} `;
  };
  R.figure_close = () => '</figcaption></figure>\n';
  R.gallery_open = (t, i) => {
    const { width, count, fig, labels } = t[i].meta;
    return `<figure class="gallery"${lineAttr(t[i])} data-count="${count}"${fig ? ` data-fig="${fig}"` : ''}${width ? ` style="max-width:${Number(width)}px"` : ''}>`
      + `${fig ? anchors(labels) : ''}<div class="gallery-view"><div class="gallery-track">${count ? '' : '<div class="gallery-empty">Empty gallery: write one image per line, e.g. ![](attachments/x.png)</div>'}`;
  };
  R.gallery_panel_open = (t, i) => {
    const p = t[i].meta;
    return `<div class="gallery-panel"${lineAttr(t[i])} data-index="${p.index}"><div class="gallery-image">`
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
  const defaultFence = R.fence!;
  R.fence = (t, i, opts, env, self) => {
    const tok = t[i];
    const lang = (tok.info || '').trim().split(/\s+/)[0].toLowerCase();
    const line = (tok.map?.[0] ?? 0) + (o.lineOffset || 0);
    if (lang === 'mermaid') return `<div class="mermaid-block" data-line="${line}"><pre class="mermaid-src">${esc(tok.content)}</pre></div>\n`;
    const html = defaultFence(t, i, opts, env, self);
    return `<div class="code-block" data-line="${line}"${lang ? ` data-lang="${esc(lang)}"` : ''}><button class="copy-code" title="Copy" type="button">Copy</button>${html}</div>\n`;
  };
  const defaultBQ: MdRenderRule = R.blockquote_open || ((t, i, opts, env, self) => self.renderToken(t, i, opts));
  R.blockquote_open = (t, i, opts, env, self) => {
    const m = t[i].meta;
    const open = defaultBQ(t, i, opts, env, self);
    return m?.callout ? `${open}<div class="callout-title">${esc(m.title)}</div>\n` : open;
  };
  R.heading_open = (t, i, opts, env, self) => {
    const text = t[i + 1]?.content || '';
    t[i].attrSet('id', `h-${slugHeading(text)}`);
    return self.renderToken(t, i, opts);
  };
  // data-line values include the front-matter offset
  if (o.lineOffset) {
    md.core.ruler.push('line_offset', (state) => {
      for (const t of state.tokens) { const v = t.attrGet && t.attrGet('data-line'); if (v != null) t.attrSet('data-line', String(Number(v) + o.lineOffset!)); }
    });
  }
  return {
    md,
    render: (src: string) => md.render(src),
    renderInline: (src: string) => md.renderInline(src),
    katex,
  };
}
