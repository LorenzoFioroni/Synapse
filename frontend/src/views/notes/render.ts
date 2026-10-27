// Markdown → HTML for notes (read mode, tables and math in the live preview, later export).
// No DOM access here, so it is unit-tested with Node (tests/js/render.test.mjs).
//
// Rules follow the notes syntax (docs/notes.md) and the backend parser:
//  * raw HTML is NOT rendered (shown as text): notes may later be written by an AI, and the
//    app must never run a script that came from a note;
//  * indented code blocks are off; fenced code only;
//  * $…$ closes on the same line (Pandoc rules), $$…$$ inline or as a block;
//  * [[Note|alias]], ![[Note]], [@key, p. 4], [see @a; @b], > [!note] callouts, - [ ] tasks;
//  * ```gallery blocks and numbered figures come from Extended Markdown (hooks.ts, FiguresHook;
//    plugins/extended-markdown/frontend/figures/markdown.ts); without it a gallery reads as the Markdown it holds;
//  * display equations are numbered through the note and \eqref{label} resolved (equations.ts);
//  * math macros: the physics package, Settings, and \newcommand in the note (macros.ts).

import type MarkdownIt from 'markdown-it';
import type { RendererRule as MdRenderRule, StateBlock, StateCore, StateInline, Token as MdToken } from 'markdown-it';

export type MdLib = typeof import('../../../vendor/md/md.js');
import { type RefKind, type Lookup, lookupIn, refNames, refParts } from './crossref.js';
import { labelId, layoutTag, numberEquations, resolveRefs } from './equations.js';
import { type Cite, GALLERY_INFO, attachmentOf, parseCites, referenceDefinition, splitWiki } from './mdscan.js';
import { hooksVersion, noteHooks } from './hooks.js';
import { type Definition, HAS_DEFINITIONS, type Macro, type Macros, baseMacros, macroSource, parseDefinitions, withDefinitions } from './macros.js';
import { takeSectionLabels } from './sections.js';

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
export interface TaskBlockOptions { view: 'list' | 'table'; group: 'none' | 'deadline'; completed: 'inline' | 'bottom' | 'hide' }
export const renderDefaults: {
  equations: () => string; macros: () => Macros; taskHtml: (id: string) => string;
  taskBlockHtml: (ids: string[], options: TaskBlockOptions) => string;
} = {
  equations: () => 'labelled',
  macros: () => baseMacros().macros,
  // what a {{task:id}} shows; the Tasks module (views/tasks/taskRender.ts) draws the task itself
  taskHtml: (id) => `<span class="task-ref" data-task="${esc(id)}">{{task:${esc(id)}}}</span>`,
  taskBlockHtml: (ids) => `<div class="task-block unavailable">${ids.map((id) => `<div>${esc(id)}</div>`).join('')}</div>`,
};

const CALLOUTS: Record<string, string> = {
  note: 'Note', tip: 'Tip', warning: 'Warning', question: 'Question', quote: 'Quote',
  info: 'Info', important: 'Important', caution: 'Caution', example: 'Example', todo: 'To do',
};
const ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
/** Text as HTML (for text and attribute values). */
export const esc = (s: unknown): string => String(s ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);

/** The anchors that \ref and \eqref links to these labels go to. */
export const anchorsHtml = (names: string[] | undefined): string => (names || []).map((n) => `<span class="eq-anchor" id="${esc(labelId(n))}"></span>`).join('');

/** ' data-line="…"' of a block token (set by the renderer's line rules), or nothing. */
export const lineAttr = (t: MdToken): string => { const v = t.attrGet('data-line'); return v != null ? ` data-line="${esc(v)}"` : ''; };

/** A reference (\ref, \eqref, \cref, \Cref) as HTML: each number links to its label's anchor, an
 *  unknown name shows as ?? in red. Also used by the live preview to number a gallery widget's. */
export function refHtml(kind: RefKind, names: string[], lookup: Lookup): string {
  const parts = refParts(kind, names, lookup).map((p) => (typeof p === 'string' ? esc(p)
    : p.missing ? `<span class="eqref missing" data-kind="${esc(kind)}" data-label="${esc(p.name)}" title="${esc(`No numbered equation or figure has \\label{${p.name}}`)}">${esc(p.text)}</span>`
      : `<a class="eqref" href="#${esc(labelId(p.name))}" data-kind="${esc(kind)}" data-label="${esc(p.name)}">${esc(p.text)}</a>`)).join('');
  return kind === 'cref' || kind === 'Cref' ? `<span class="cref" data-kind="${kind}" data-names="${esc(names.join(','))}">${parts}</span>` : parts;
}

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

/** {{task:id}}: a task (a record in library.db) shown in the note; _TASK in parser.py. */
const TASK_REF = /^\{\{task:([a-z0-9]{4,32})\}\}/;
function taskRefRule(state: StateInline, silent: boolean): boolean {
  if (state.src.charCodeAt(state.pos) !== 0x7b) return false;
  const m = TASK_REF.exec(state.src.slice(state.pos));
  if (!m) return false;
  if (!silent) { const t = state.push('task_ref', '', 0); t.meta = { id: m[1] }; }
  state.pos += m[0].length;
  return true;
}

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
 * A ```gallery block when galleries are off (Extended Markdown) (it turns them into carousels before this
 * runs): the Markdown it holds, read like the rest of the note (its images, captions, citations).
 * Runs after block parsing, before inline parsing.
 */
function plainGalleries(state: StateCore): void {
  const isGallery = (t: MdToken) => t.type === 'fence' && GALLERY_INFO.test((t.info || '').trim());
  if (!state.tokens.some(isGallery)) return;
  const out: MdToken[] = [];
  for (const tok of state.tokens) {
    if (!isGallery(tok)) { out.push(tok); continue; }
    const inner: MdToken[] = [];
    state.md.block.parse(tok.content, state.md, state.env, inner);
    const shift = (tok.map?.[0] ?? 0) + 1;   // the line after the opening fence
    for (const t of inner) {
      if (t.map) t.map = [t.map[0] + shift, t.map[1] + shift];
      t.level += tok.level;
    }
    out.push(...inner);
  }
  state.tokens = out;
}

/** data-line on block tokens, so a click in read mode can find the source line. */
function lineAttrs(state: StateCore): void {
  for (const t of state.tokens) {
    if (t.map && t.nesting >= 0 && t.block && t.type !== 'inline') t.attrSet('data-line', String(t.map[0]));
  }
}

/** \eqref{label}, \ref{label}, \cref{a,b}, \Cref{a} written in the text (outside math): written out
 *  by R.eq_ref (crossref.ts) once equations and figures are numbered. */
function eqRefRule(state: StateInline, silent: boolean): boolean {
  const src = state.src, pos = state.pos;
  if (src.charCodeAt(pos) !== 0x5C /* \ */) return false;
  const m = /^\\(eqref|ref|cref|Cref)\{([^{}\n]*)\}/.exec(src.slice(pos, pos + 400));
  if (!m) return false;
  if (!silent) { const t = state.push('eq_ref', '', 0); t.meta = { kind: m[1], name: m[2].trim(), names: refNames(m[1], m[2]) }; }
  state.pos = pos + m[0].length;
  return true;
}

/** Number headings and collect their \label targets before their inline content is parsed. */
function sectionsRule(state: StateCore): void {
  const headings = state.tokens.filter((t) => t.type === 'heading_open');
  const title = headings.length > 1 && headings[0].tag === 'h1' && headings.slice(1).some((t) => t.tag !== 'h1') ? headings[0] : null;
  const numbered = title ? headings.slice(1) : headings;
  const base = numbered.reduce((n, t) => Math.min(n, Number(t.tag.slice(1))), 6);
  const counters = [0, 0, 0, 0, 0, 0];
  const labels = new Map<string, string>();
  for (let i = 0; i < state.tokens.length - 1; i++) {
    const open = state.tokens[i], inline = state.tokens[i + 1];
    if (open.type !== 'heading_open' || inline.type !== 'inline') continue;
    const level = Number(open.tag.slice(1));
    let number = '';
    if (open !== title) {
      counters[level - 1]++; counters.fill(0, level);
      number = counters.slice(base - 1, level).map((n) => n || 1).join('.');
    }
    const p = takeSectionLabels(inline.content);
    inline.content = p.text;
    const inToc = !(state.env.notocHeadings as Set<number> | undefined)?.has(open.map?.[0] ?? -1);
    open.meta = { ...(open.meta || {}), sectionNumber: number, sectionLabels: p.labels.map((l) => l.name), inToc };
    for (const l of p.labels) if (number && !labels.has(l.name)) labels.set(l.name, number);
  }
  state.env.sectionLabels = labels;
}

/** Replace a paragraph containing only \tableofcontents with links to the note's headings. */
function tableOfContentsRule(state: StateCore): void {
  const headings: { level: number; number: string; text: string; id: string }[] = [];
  for (let i = 0; i < state.tokens.length - 1; i++) {
    const open = state.tokens[i], inline = state.tokens[i + 1];
    if (open.type !== 'heading_open' || inline.type !== 'inline') continue;
    if (open.meta?.inToc !== false) headings.push({ level: Number(open.tag.slice(1)), number: open.meta?.sectionNumber || '', text: inline.content, id: `h-${slugHeading(inline.content)}` });
  }
  for (let i = 0; i < state.tokens.length - 2; i++) {
    const open = state.tokens[i], inline = state.tokens[i + 1], close = state.tokens[i + 2];
    if (open.type !== 'paragraph_open' || inline.type !== 'inline' || close.type !== 'paragraph_close' || inline.content.trim() !== '\\tableofcontents') continue;
    open.type = 'toc_open'; open.tag = ''; open.nesting = 0;
    inline.type = 'toc_body'; inline.children = null; inline.content = '';
    inline.meta = { headings };
    close.type = 'toc_close'; close.tag = ''; close.nesting = 0;
  }
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
  // Built again when a plugin's hook changed (hooks.ts), so a renderer made before the Figures
  // plugin started, or after it stopped, draws as it would now.
  const build = (): MarkdownIt => {
    const md = new lib.MarkdownIt({ html: false, linkify: true, typographer: false });
    md.disable('code');
    md.use(lib.footnote);
    md.block.ruler.before('fence', 'math_block', mathBlock, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
    md.inline.ruler.before('escape', 'math_inline', mathInline());
    md.inline.ruler.before('link', 'wiki', wikiRule);
    md.inline.ruler.before('image', 'wiki_embed', wikiRule);
    md.inline.ruler.before('link', 'cite', citeRule);
    md.inline.ruler.push('tag', tagRule);
    md.inline.ruler.before('emphasis', 'task_ref', taskRefRule);
    md.inline.ruler.before('escape', 'eq_ref', eqRefRule);
    md.core.ruler.before('inline', 'sections', sectionsRule);
    md.core.ruler.after('sections', 'table_of_contents', tableOfContentsRule);
    md.core.ruler.after('inline', 'equations', equationsRule(o.equations ?? renderDefaults.equations, o.macros ?? renderDefaults.macros));
    md.core.ruler.after('block', 'callout', calloutRule);
    md.core.ruler.after('block', 'gallery_plain', plainGalleries);
    md.core.ruler.after('inline', 'task', taskRule);
    md.core.ruler.push('line_attrs', lineAttrs);

    const R = md.renderer.rules;
    const anchors = anchorsHtml;
    // a formula that only defines macros shows nothing (like a LaTeX preamble)
    const onlyDefines = (t: MdToken) => t.meta?.defines && !t.content.trim();
    R.math_inline = (t, i, opts, env) => (onlyDefines(t[i]) ? ''
      : t[i].meta.display ? `<span class="math display">${anchors(t[i].meta.labels)}${katex(t[i].content, true, env.macros)}</span>`
        : `<span class="math">${katex(t[i].content, false, env.macros)}</span>`);
    R.math_block = (t, i, opts, env) => (onlyDefines(t[i]) ? ''
      : `<div class="math-block" data-line="${(t[i].map?.[0] ?? 0) + (o.lineOffset || 0)}">${anchors(t[i].meta?.labels)}${katex(t[i].content, true, env.macros)}</div>\n`);
    // an equation's label, else a figure's (env.figLabels, from Extended Markdown's figures); \cref writes the kind too (crossref.ts)
    R.eq_ref = (t, i, opts, env) => {
      const { kind, names } = t[i].meta;
      return refHtml(kind, names, lookupIn(env.eqLabels, env.figLabels, env.sectionLabels));
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
    R.task_ref = (t, i) => renderDefaults.taskHtml(t[i].meta.id);
    R.task_box = (t, i) => `<input type="checkbox" class="task-box"${t[i].meta.checked ? ' checked' : ''}${t[i].meta.line != null ? ` data-task-line="${t[i].meta.line + (o.lineOffset || 0)}"` : ''}>`;
    R.toc_open = () => '<nav class="note-toc" aria-label="Table of contents"><div class="note-toc-title">Contents</div><ol>';
    R.toc_body = (t, i) => {
      const hs = t[i].meta.headings as { level: number; number: string; text: string; id: string }[];
      const shown = hs.filter((h, n) => h.number || n > 0);
      const base = shown.reduce((v, h) => Math.min(v, h.level), 6);
      return shown.map((h) => `<li style="--toc-depth:${Math.max(0, h.level - base)}"><a href="#${esc(h.id)}">${h.number ? `<span>${esc(h.number)}</span>` : ''}${md.renderInline(h.text)}</a></li>`).join('');
    };
    R.toc_close = () => '</ol></nav>\n';
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
      const names = t[i].meta?.sectionLabels as string[] | undefined;
      const prefix = names?.length && t[i].meta.sectionNumber ? `${anchorsHtml(names)}<span class="section-num">${esc(t[i].meta.sectionNumber)}</span> ` : '';
      return self.renderToken(t, i, opts) + prefix;
    };
    // data-line values include the front-matter offset
    if (o.lineOffset) {
      md.core.ruler.push('line_offset', (state) => {
        for (const t of state.tokens) { const v = t.attrGet && t.attrGet('data-line'); if (v != null) t.attrSet('data-line', String(Number(v) + o.lineOffset!)); }
      });
    }
    noteHooks.figures?.markdown(md);
    noteHooks.extended?.markdown(md);
    return md;
  };
  let md = build(), built = hooksVersion;
  const current = () => {
    if (built !== hooksVersion) { built = hooksVersion; md = build(); }
    return md;
  };
  return {
    get md() { return current(); },
    render: (src: string) => {
      const notocHeadings = new Set<number>();
      const normalized = src.split('\n').map((line, index) => {
        const m = /^( {0,3}#{1,6})\*([ \t]+)/.exec(line);
        if (!m) return line;
        notocHeadings.add(index);
        return `${m[1]} ${line.slice(m[1].length + 1)}`;
      }).join('\n');
      return current().render(normalized, { notocHeadings });
    },
    renderInline: (src: string) => current().renderInline(src),
    katex,
  };
}
