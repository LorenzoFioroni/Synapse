// Live preview for the notes editor (CodeMirror decorations).
//
// Rule: what the cursor (or selection) touches shows its raw Markdown, the rest is rendered.
// Inline things (math, links, citations, emphasis, code) open only when the cursor is in them or
// next to them, so typing in a long paragraph does not unfold all of its formulas;
// line marks (headings, lists, quotes) open for the cursor's line; multi-line constructs (tables,
// $$ blocks, Mermaid diagrams, galleries) count as one unit: touching any of their lines reveals
// all of them, and a $$ block or a gallery being edited shows its rendering below the source.
//
// What counts as math, a [[link]] or a [@citation] comes from mdscan.js (the port of the
// backend parser); everything else from CodeMirror's own Markdown syntax tree.
//
// The work is split so that a keystroke or a cursor move costs little in a long note:
//  * `blocks` (a state field) holds what changes the height of whole lines: the front matter,
//    $$ blocks, tables and Mermaid diagrams. CodeMirror needs these for the whole document. It
//    walks only the block structure of the syntax tree, never the inside of paragraphs.
//  * `inline` (a view plugin) does everything else, and only for the part of the note on screen
//    (CodeMirror's viewport, with a margin), which it redoes as you scroll.
// Widgets keep their DOM while their key stays the same (previewWidgets.ts), so a formula, table
// or diagram is drawn again only when its own source changed.
import type { EditorState, Range as CMRange, Text } from '@codemirror/state';
import type { Decoration as DecorationT, DecorationSet, EditorView as EditorViewT, ViewUpdate } from '@codemirror/view';
import type { CM } from './editor.js';
import { type Scan, type Span, attachmentOf, scan, splitFrontmatter } from './mdscan.js';
import { type CiteTarget, citeTargetInText } from './citeTarget.js';
import { type CiteInfo, type Renderer, citeLabel, imageSrc } from './render.js';
import { type TextRef, numberEquations, refText, resolveRefs, textRefs } from './equations.js';
import { clickOffset } from './mathClick.js';
import { type Definition, HAS_DEFINITIONS, type Macros, parseDefinitions, withDefinitions } from './macros.js';
import { type Equations, previewWidgets } from './previewWidgets.js';
import { galleryInfo } from './gallery.js';
import { type Figures, figureLabel, figuresOf } from './figures.js';

/** A DOM text node (``Text`` is CodeMirror's document here). */
type Text_ = globalThis.Text;

const CALLOUT_TYPES = ['note', 'tip', 'warning', 'question', 'quote', 'info', 'important', 'caution', 'example', 'todo'];

/** What the live preview needs from the notes view. */
export interface PreviewDeps {
  /** The Markdown renderer, or null while it loads. */
  renderer(): Renderer | null;
  /** The path a [[target]] goes to: a path, null (no such note) or undefined (not known yet). */
  resolveNote(target: string): string | null | undefined;
  citeInfo(key: string): CiteInfo | null | undefined;
  openNote(target: string, heading: string): void;
  openCite?(key: string, page: number | null, target: CiteTarget | null, opts: { newTab?: boolean; att?: number | null }): void;
  /** Render a Mermaid diagram into the element. */
  mermaid(el: HTMLElement, code: string): void;
  /** Equation numbering: 'labelled' or 'all'. */
  equations?(): string;
  /** The KaTeX macros every note starts from (macros.ts). */
  macros?(): Macros;
}

/** The block-level decorations, and the ranges they draw (nothing inline is added inside those). */
interface Blocks { deco: DecorationSet; taken: [number, number][]; version: number }

export function livePreview(cm: CM, deps: PreviewDeps) {
  const { Decoration, StateField, StateEffect, EditorView, ViewPlugin, syntaxTree } = cm;
  const W = previewWidgets(cm, deps);
  const refresh = StateEffect.define<null>();
  // Lines show their source only while you are editing: with the editor unfocused, all is rendered.
  const setFocus = StateEffect.define<boolean>();
  const focusField = StateField.define<boolean>({
    create: () => false,
    update: (v, tr) => tr.effects.reduce((acc, e) => (e.is(setFocus) ? e.value : acc), v),
  });

  /* ------------------------------------------------------- per-document data */
  let cache: { doc: Text | null; scan: Scan | null; refs: TextRef[] | null; eq: Equations | null; mode: string | null; base: Macros | null; figs: Figures | null } = { doc: null, scan: null, refs: null, eq: null, mode: null, base: null, figs: null };
  let lastBase: Macros | null = null, baseVersion = 0;
  const scanOf = (state: EditorState): Scan => {
    if (cache.doc !== state.doc) cache = { doc: state.doc, scan: scan(state.doc.toString()), refs: null, eq: null, mode: null, base: null, figs: null };
    return cache.scan!;
  };
  // the note's figures and their numbers (figures.ts), found with the same rule as the renderer's
  const figuresOfDoc = (state: EditorState): Figures => {
    const s = scanOf(state);
    cache.figs ||= figuresOf(state.doc.toString(), s);
    return cache.figs;
  };
  // what \ref / \eqref may name: an equation's label, else a figure's
  const refLabels = (state: EditorState, eq: Equations): Map<string, string | null> => {
    const figs = figuresOfDoc(state).labels;
    return figs.size ? new Map<string, string | null>([...figs, ...eq.labels]) : eq.labels;
  };
  // \eqref{label} / \ref{label} in the text (not in code or math), as document offsets
  const refsOf = (state: EditorState): TextRef[] => {
    const s = scanOf(state);
    if (!cache.refs) {
      const inCode = (from: number, to: number) => s.spans.some((sp) => sp.kind !== 'gallery' && from < sp.to && to > sp.from);
      cache.refs = textRefs(state.doc.sliceString(s.bodyStart))
        .map((r) => ({ ...r, from: r.from + s.bodyStart, to: r.to + s.bodyStart }))
        .filter((r) => !inCode(r.from, r.to));
    }
    return cache.refs;
  };
  // the whole note's formulas: macro definitions taken out (they apply to every formula), display
  // equations numbered; formula start → TeX for KaTeX; labels for \eqref
  const mathSrc = (doc: Text, sp: Span) => (sp.kind === 'mathblock'
    ? doc.sliceString(doc.lineAt(sp.from).from, doc.lineAt(sp.to).to).trim().replace(/^\$\$/, '').replace(/\$\$$/, '').trim()
    : doc.sliceString(sp.from + (sp.display ? 2 : 1), sp.to - (sp.display ? 2 : 1)));
  const equationsOf = (state: EditorState): Equations => {
    const s = scanOf(state);
    const mode = deps.equations?.() || 'labelled';
    const base = deps.macros?.() || {};
    if (base !== lastBase) { lastBase = base; baseVersion++; }
    if (cache.eq && cache.mode === mode && cache.base === base) return cache.eq;
    const doc = state.doc;
    const maths = s.spans.filter((sp) => (sp.kind === 'mathblock' && !sp.open) || sp.kind === 'math').sort((a, b) => a.from - b.from);
    const src = new Map<number, string>(), defines = new Map<number, string[]>(), defs: Definition[] = [];
    for (const sp of maths) {
      let t = mathSrc(doc, sp);
      if (HAS_DEFINITIONS.test(t)) {
        const p = parseDefinitions(t);
        if (p.defs.length) { defs.push(...p.defs); defines.set(sp.from, p.defs.map((d) => d.name)); t = p.rest; }
      }
      src.set(sp.from, t);
    }
    const display = maths.filter((sp) => sp.kind === 'mathblock' || sp.display);
    const res = numberEquations(display.map((sp) => src.get(sp.from)!), { mode });
    const tex = new Map(display.map((sp, i) => [sp.from, resolveRefs(res.formulas[i], res.labels)]));
    for (const sp of maths) if (!tex.has(sp.from)) tex.set(sp.from, resolveRefs(src.get(sp.from)!, res.labels));
    const sig = `${baseVersion}|${defs.map((d) => `${d.name}[${d.nargs}][${d.opt ?? ''}]{${d.body}}`).join('|')}`;
    cache.eq = { tex, labels: res.labels, macros: withDefinitions(base, defs), sig, defines };
    cache.mode = mode;
    cache.base = base;
    return cache.eq;
  };

  /** Where the selection is, and the tests built on it. */
  function selectionOf(state: EditorState) {
    const doc = state.doc;
    const focused = state.field(focusField, false);
    const active: [number, number][] = focused ? state.selection.ranges.map((r) => [doc.lineAt(r.from).from, doc.lineAt(r.to).to]) : [];
    const isActive = (from: number, to: number) => active.some(([a, b]) => from <= b && to >= a);
    return {
      lineActive: (pos: number) => { const l = doc.lineAt(pos); return isActive(l.from, l.to); },
      blockActive: (from: number, to: number) => isActive(doc.lineAt(from).from, doc.lineAt(to).to),
      // inline things open only when the selection is in them or right next to them
      touches: (from: number, to: number) => !!focused && state.selection.ranges.some((r) => r.from <= to && r.to >= from),
    };
  }

  const multiLine = (doc: Text, from: number, to: number) => doc.lineAt(from).to < to;
  const mathWidget = (doc: Text, eq: Equations, sp: Span, block: boolean) => {
    const tex = eq.tex.get(sp.from) ?? mathSrc(doc, sp);
    return eq.defines.has(sp.from) && !tex.trim() ? W.defs(eq.defines.get(sp.from)!, block) : W.katex(eq, tex, block || !!sp.display, block);
  };

  /* ------------------------------------------------------------ block level */
  function buildBlocks(state: EditorState, version: number): Blocks {
    const doc = state.doc;
    const s = scanOf(state);
    const eq = equationsOf(state);
    const { blockActive, touches } = selectionOf(state);
    const out: CMRange<DecorationT>[] = [];
    const taken: [number, number][] = [];
    const lineDeco = (pos: number, cls: string) => out.push(Decoration.line({ class: cls }).range(doc.lineAt(pos).from));
    const lineDecos = (from: number, to: number, cls: (first: boolean, last: boolean) => string) => {
      for (let p = from; p <= to; p = doc.lineAt(p).to + 1) {
        const l = doc.lineAt(p);
        lineDeco(p, cls(l.from === from, l.to >= to));
        if (l.to >= to) break;
      }
    };
    const overlapsTaken = (from: number, to: number) => taken.some(([a, b]) => from < b && to > a);

    // front matter: a compact box of properties until the cursor goes into it
    if (doc.sliceString(0, 4) === '---\n') {
      const fm = splitFrontmatter(doc.sliceString(0, Math.min(doc.length, 50000)));
      if (fm.header !== null) {
        const to = doc.lineAt(Math.max(0, fm.bodyStart - 1)).to;
        if (!blockActive(0, to)) { taken.push([0, to]); out.push(Decoration.replace({ widget: W.props(fm.header), block: true }).range(0, to)); }
      }
    }

    // $$ blocks, and the rare formula that runs over several lines (a replacement that hides a
    // line break has to come from here)
    for (const sp of s.spans) {
      if (sp.kind === 'mathblock' && !sp.open) {
        const from = doc.lineAt(sp.from).from, to = doc.lineAt(sp.to).to;
        if (blockActive(from, to)) {
          lineDecos(from, to, (first, last) => `cm-lp-mathsrc cm-lp-mathsrc-block${first ? ' first' : ''}${last ? ' last' : ''}`);
          // the formula being edited stays visible below its source
          const tex = eq.tex.get(sp.from) ?? mathSrc(doc, sp);
          if (!(eq.defines.has(sp.from) && !tex.trim())) out.push(Decoration.widget({ widget: W.katex(eq, tex, true, true, true), block: true, side: 1 }).range(to));
          continue;
        }
        taken.push([from, to]);
        out.push(Decoration.replace({ widget: mathWidget(doc, eq, sp, true), block: true }).range(from, to));
      } else if (sp.kind === 'math' && multiLine(doc, sp.from, sp.to)) {
        taken.push([sp.from, sp.to]);
        out.push((touches(sp.from, sp.to) ? Decoration.mark({ class: 'cm-lp-mathsrc' }) : Decoration.replace({ widget: mathWidget(doc, eq, sp, false) })).range(sp.from, sp.to));
      }
    }

    // tables and fenced code, from the block structure of the syntax tree
    syntaxTree(state).iterate({
      from: s.bodyStart,
      enter: (node) => {
        const name = node.name;
        if (name === 'Table') {
          const from = doc.lineAt(node.from).from, to = doc.lineAt(node.to).to;
          if (blockActive(from, to) || overlapsTaken(from, to)) lineDecos(from, to, () => 'cm-lp-tablesrc');
          else {
            // drawn by the renderer, so math, citations and links in its cells are rendered too
            taken.push([from, to]);
            out.push(Decoration.replace({ widget: W.table(doc.sliceString(from, to), `${version}:${eq.sig}`), block: true }).range(from, to));
          }
          return false;
        }
        if (name === 'FencedCode') {
          const from = doc.lineAt(node.from).from, to = doc.lineAt(node.to).to;
          const info = node.node.getChild('CodeInfo');
          const lang = info ? doc.sliceString(info.from, info.to).trim().toLowerCase() : '';
          if (galleryInfo(lang)) {
            // a gallery is one unit: drawn as its carousel, or, while the cursor is in it, its
            // source with the carousel below it (nothing inside the source is rendered)
            const src = doc.sliceString(from, to);
            // its figure number, and \ref in its captions, come from the whole note: redraw when they change
            const fig = figuresOfDoc(state).figures.find((f) => f.kind === 'gallery' && f.from === from)?.number ?? null;
            const labels = refLabels(state, eq);
            const key = `${version}:${eq.sig}:${fig}${/\\(eq)?ref\{/.test(src) ? `:${[...labels].join(';')}` : ''}`;
            taken.push([from, to]);
            if (blockActive(from, to)) {
              lineDecos(from, to, (first, last) => `cm-lp-gallerysrc${first ? ' first' : ''}${last ? ' last' : ''}`);
              out.push(Decoration.widget({ widget: W.gallery(src, labels, fig, key, true), block: true, side: 1 }).range(to));
            } else out.push(Decoration.replace({ widget: W.gallery(src, labels, fig, key, false), block: true }).range(from, to));
            return false;
          }
          if (lang === 'mermaid' && !blockActive(from, to)) {
            const textNode = node.node.getChild('CodeText');
            taken.push([from, to]);
            out.push(Decoration.replace({ widget: W.mermaid(textNode ? doc.sliceString(textNode.from, textNode.to) : ''), block: true }).range(from, to));
          } else lineDecos(from, to, (first, last) => `cm-lp-code${first ? ' cm-lp-code-first' : ''}${last ? ' cm-lp-code-last' : ''}`);
          return false;
        }
        // lists and quotes may hold tables and code; paragraphs and headings hold only inline content
        return name === 'Document' || name.endsWith('List') || name === 'ListItem' || name === 'Blockquote' ? undefined : false;
      },
    });
    return { deco: Decoration.set(out, true), taken, version };
  }

  const blocks = StateField.define<Blocks>({
    create: (state) => buildBlocks(state, 0),
    update(v, tr) {
      const refreshed = tr.effects.some((e) => e.is(refresh));
      if (refreshed || tr.docChanged || tr.selection || tr.effects.some((e) => e.is(setFocus))
          || syntaxTree(tr.startState) !== syntaxTree(tr.state)) return buildBlocks(tr.state, v.version + (refreshed ? 1 : 0));
      return v;
    },
    provide: (f) => EditorView.decorations.from(f, (v) => v.deco),
  });

  /* ------------------------------------------------------------------ inline */
  function buildInline(view: EditorViewT): DecorationSet {
    const state = view.state;
    const doc = state.doc;
    const s = scanOf(state);
    const eq = equationsOf(state);
    const { lineActive, touches } = selectionOf(state);
    const { from: vFrom, to: vTo } = view.viewport;
    const blockTaken = state.field(blocks).taken;
    const out: CMRange<DecorationT>[] = [];
    const add = (from: number, to: number, deco: DecorationT) => { if (from <= to) out.push(deco.range(from, to)); };
    // a plugin may not hide line breaks: such a replacement is left out (the source shows)
    const replace = (from: number, to: number, deco: DecorationT) => { if (from <= to && !multiLine(doc, from, to)) out.push(deco.range(from, to)); };
    const lineDeco = (pos: number, cls: string) => out.push(Decoration.line({ class: cls }).range(doc.lineAt(pos).from));
    const shown = (from: number, to: number) => to >= vFrom && from <= vTo && !blockTaken.some(([a, b]) => from < b && to > a);
    const hide = Decoration.replace({});

    // regions handled by the scanner win over CodeMirror's own nodes
    const taken: [number, number][] = [];
    const overlapsTaken = (from: number, to: number) => taken.some(([a, b]) => from < b && to > a);

    // math on one line (from the scanner: same rules as the backend)
    for (const sp of s.spans) {
      if (sp.kind !== 'math' || !shown(sp.from, sp.to) || multiLine(doc, sp.from, sp.to)) continue;
      taken.push([sp.from, sp.to]);
      if (touches(sp.from, sp.to)) add(sp.from, sp.to, Decoration.mark({ class: 'cm-lp-mathsrc' }));
      else replace(sp.from, sp.to, Decoration.replace({ widget: mathWidget(doc, eq, sp, false) }));
    }

    // \eqref{label} / \ref{label} in the text: the equation's (or figure's) number
    const labels = refLabels(state, eq);
    for (const r of refsOf(state)) {
      if (!shown(r.from, r.to)) continue;
      taken.push([r.from, r.to]);
      if (touches(r.from, r.to)) { add(r.from, r.to, Decoration.mark({ class: 'cm-lp-linksrc eqref' })); continue; }
      const text = refText(r.kind, r.name, labels);
      replace(r.from, r.to, Decoration.replace({ widget: W.chip(`e:${r.from}:${text}`, `eqref${text == null ? ' missing' : ''}`, text ?? '(??)', { label: r.name }) }));
    }

    // an image's caption line: its figure number in front, its \label hidden, until you edit it
    for (const f of figuresOfDoc(state).figures) {
      if (f.kind !== 'image' || !shown(f.captionFrom!, f.to)) continue;
      lineDeco(f.captionFrom!, 'cm-lp-figcaption');
      if (lineActive(f.captionFrom!)) continue;
      out.push(Decoration.widget({ widget: W.chip(`fig:${f.number}`, 'fignum', `${figureLabel(f.number!)}.`, {}), side: -1 }).range(f.captionFrom!));
      const line = doc.sliceString(f.captionFrom!, f.to);
      for (const m of line.matchAll(/\s*\\label\{[^{}\n]*\}/g)) {
        const a = f.captionFrom! + m.index!, b = a + m[0].length;
        if (!s.spans.some((sp) => a >= sp.from && a < sp.to)) replace(a, b, hide);   // not "taken": the *…* around it still hide
      }
    }

    // [[links]], ![[embeds]], [@citations] (one chip per group), images
    let cites: typeof s.links = [];
    const flushCites = () => {
      if (!cites.length) return;
      const ls = cites;
      cites = [];
      const from = ls[0].from, to = ls[0].to;
      taken.push([from, to]);
      const missing = ls.some((l) => !deps.citeInfo(l.target));
      const trashed = ls.some((l) => deps.citeInfo(l.target)?.trashed);
      const keys = ls.map((l) => l.target).join(';');
      const pages = ls.map((l) => l.page ?? '').join(';');
      const atts = ls.map((l) => attachmentOf(l.locator) ?? '').join(';');
      if (touches(from, to)) { add(from, to, Decoration.mark({ class: `cm-lp-citesrc${missing ? ' missing' : ''}`, attributes: { 'data-keys': keys, 'data-pages': pages, 'data-atts': atts } })); return; }
      const label = `(${ls.map((l) => citeLabel({ key: l.target, prefix: l.prefix, suppress: l.suppress_author, locator: l.locator }, deps.citeInfo(l.target))).join('; ')})`;
      replace(from, to, Decoration.replace({ widget: W.chip(`c:${from}:${label}:${missing}:${trashed}`, `cite${missing ? ' missing' : ''}${trashed ? ' trashed' : ''}`, label, { keys, pages, atts }) }));
    };
    for (const l of s.links) {
      if (!shown(l.from, l.to)) continue;
      if (l.kind === 'cite') {
        if (cites.length && cites[0].from !== l.from) flushCites();
        cites.push(l);
        continue;
      }
      taken.push([l.from, l.to]);
      if (l.kind === 'image') {
        if (!touches(l.from, l.to)) replace(l.from, l.to, Decoration.replace({ widget: W.image(imageSrc(l.target), l.alias, l.width, l.target) }));
        continue;
      }
      const path = deps.resolveNote(l.target);
      const cls = `${l.kind === 'embed' ? 'embed' : 'wikilink'}${path === null && l.target ? ' unresolved' : ''}`;
      if (touches(l.from, l.to)) { add(l.from, l.to, Decoration.mark({ class: `cm-lp-linksrc ${cls}` })); continue; }
      const label = (l.kind === 'embed' ? '↪ ' : '') + (l.alias || (l.target ? l.target + (l.heading ? ` › ${l.heading}` : '') : `› ${l.heading}`));
      replace(l.from, l.to, Decoration.replace({ widget: W.chip(`n:${l.from}:${label}:${cls}`, cls, label, { note: l.target, heading: l.heading, path }) }));
    }
    flushCites();
    for (const t of s.tags) if (shown(t.from, t.to)) add(t.from, t.to, Decoration.mark({ class: 'cm-lp-tag' }));

    // CodeMirror syntax tree: headings, emphasis, quotes, callouts, tasks, rules, links
    syntaxTree(state).iterate({
      from: Math.max(s.bodyStart, vFrom),
      to: vTo,
      enter: (node) => {
        const name = node.name;
        // tables and code are the field's; nothing is added inside what a block widget draws
        if (name === 'Table' || name === 'FencedCode' || blockTaken.some(([a, b]) => node.from >= a && node.to <= b)) return false;
        if (/^ATXHeading[1-6]$/.test(name)) {
          lineDeco(node.from, `cm-lp-h cm-lp-h${name.slice(-1)}`);
          if (!lineActive(node.from)) {
            const mark = node.node.getChild('HeaderMark');
            if (mark && mark.from === node.from) replace(mark.from, Math.min(mark.to + 1, node.to), hide);
          }
          return;
        }
        if (name === 'Emphasis' || name === 'StrongEmphasis' || name === 'Strikethrough' || name === 'InlineCode') {
          if (overlapsTaken(node.from, node.to) || touches(node.from, node.to)) return name === 'InlineCode' ? false : undefined;
          const markName = name === 'Strikethrough' ? 'StrikethroughMark' : name === 'InlineCode' ? 'CodeMark' : 'EmphasisMark';
          for (const m of node.node.getChildren(markName)) replace(m.from, m.to, hide);
          return name === 'InlineCode' ? false : undefined;
        }
        if (name === 'Link') {
          if (overlapsTaken(node.from, node.to)) return false;
          const url = node.node.getChild('URL');
          if (!url) return false;   // [text] without a destination: leave as typed
          add(node.from, node.to, Decoration.mark({ class: 'cm-lp-link', attributes: { 'data-href': doc.sliceString(url.from, url.to) } }));
          if (touches(node.from, node.to)) return false;
          const marks = node.node.getChildren('LinkMark');
          // hide "[" and "](url "title")"
          if (marks[0]) replace(marks[0].from, marks[0].to, hide);
          if (marks[1]) replace(marks[1].from, node.to, hide);
          return false;
        }
        if (name === 'Image') return false;
        if (name === 'Escape') {
          if (!touches(node.from, node.to) && !overlapsTaken(node.from, node.to)) replace(node.from, node.from + 1, hide);
          return false;
        }
        if (name === 'ListMark') {
          const item = node.node.parent;
          if (!item || item.parent?.name !== 'BulletList' || lineActive(node.from)) return false;
          if (item.getChild('Task')) replace(node.from, Math.min(node.to + 1, doc.lineAt(node.from).to), hide);   // the checkbox is enough
          else replace(node.from, node.to, Decoration.replace({ widget: W.bullet }));
          return false;
        }
        if (name === 'HorizontalRule') {
          if (!lineActive(node.from)) replace(node.from, node.to, Decoration.replace({ widget: W.hr }));
          return false;
        }
        if (name === 'Blockquote') {
          const first = doc.lineAt(node.from);
          const m = /^\s*>\s*\[!([A-Za-z]+)\]([+-]?)[ \t]*(.*)$/.exec(first.text);
          const type = m ? (CALLOUT_TYPES.includes(m[1].toLowerCase()) ? m[1].toLowerCase() : 'note') : null;
          for (let p = node.from; p <= node.to; p = doc.lineAt(p).to + 1) {
            const line = doc.lineAt(p);
            lineDeco(p, type ? `cm-lp-callout cm-lp-callout-${type}${line.from === first.from ? ' cm-lp-callout-head' : ''}` : 'cm-lp-quote');
            if (!lineActive(p)) {
              const qm = /^(\s*>[ \t]?)/.exec(line.text);
              if (qm) replace(line.from, line.from + qm[1].length, hide);
            }
            if (line.to >= node.to) break;
          }
          if (m && !lineActive(first.from)) {
            const start = first.from + first.text.indexOf('[!');
            const end = start + m[1].length + 3 + m[2].length;
            const title = m[3] ? '' : m[1][0].toUpperCase() + m[1].slice(1).toLowerCase();
            replace(start, Math.min(end + (m[3] ? 1 : 0), first.to), Decoration.replace({ widget: W.calloutTitle(type!, title) }));
          }
          return undefined;
        }
        if (name === 'TaskMarker') {
          const checked = /x/i.test(doc.sliceString(node.from, node.to));
          if (checked) lineDeco(node.from, 'cm-lp-task-done');
          if (!lineActive(node.from)) replace(node.from, node.to, Decoration.replace({ widget: W.checkbox(checked, node.from) }));
          return false;
        }
        return undefined;
      },
    });
    return Decoration.set(out, true);
  }

  const inline = ViewPlugin.fromClass(class {
    decorations: DecorationSet;
    constructor(view: EditorViewT) { this.decorations = buildInline(view); }
    update(u: ViewUpdate) {
      if (u.docChanged || u.selectionSet || u.viewportChanged
          || u.startState.field(blocks) !== u.state.field(blocks)
          || u.transactions.some((tr) => tr.effects.some((e) => e.is(refresh) || e.is(setFocus)))
          || syntaxTree(u.startState) !== syntaxTree(u.state)) this.decorations = buildInline(u.view);
    }
  }, { decorations: (p) => p.decorations });

  /** Put the cursor where a rendered formula was clicked (mathClick.js), instead of at its edge. */
  function clickIntoMath(e: MouseEvent, view: EditorViewT, w: HTMLElement): boolean {
    const html = w.querySelector('.katex-html');
    if (!html) return false;
    let start: number | null;
    try { start = view.posAtDOM(w); } catch { return false; }
    if (w.classList.contains('cm-lp-mathpreview')) start = null;
    const s = scanOf(view.state);
    const sp = s.spans.find((x) => (x.kind === 'math' && start != null && x.from === start)
      || (x.kind === 'mathblock' && !x.open && (start != null ? view.state.doc.lineAt(x.from).from === start
        : view.state.selection.main.head >= x.from && view.state.selection.main.head <= x.to)));
    if (!sp) return false;
    const doc = view.state.doc;
    let texFrom: number, tex: string;
    if (sp.kind === 'math') {
      const d = sp.display ? 2 : 1;
      texFrom = sp.from + d; tex = doc.sliceString(texFrom, sp.to - d);
    } else {
      const from = doc.lineAt(sp.from).from, to = doc.lineAt(sp.to).to;
      const raw = doc.sliceString(from, to);
      const a = raw.indexOf('$$') + 2, b = raw.lastIndexOf('$$');
      texFrom = from + a; tex = raw.slice(a, b > a ? b : raw.length);
    }
    // the characters KaTeX drew (not the number column), and which one was clicked
    const nodes: Text_[] = [];
    const walk = document.createTreeWalker(html, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement!.closest('.katex-tag, .eq-ghost') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    for (let n = walk.nextNode(); n; n = walk.nextNode()) nodes.push(n as Text_);
    const drawn: string[] = [], owner: number[] = [];
    nodes.forEach((n, k) => { for (const ch of n.data) { if (/\S/.test(ch) && ch !== '\u200b') { drawn.push(ch); owner.push(k); } } });
    let pos: number;
    const caret = document.caretRangeFromPoint?.(e.clientX, e.clientY);
    const at = caret ? nodes.indexOf(caret.startContainer as Text_) : -1;
    if (caret && at >= 0) {
      const before = drawn.filter((_, i) => owner[i] < at).length;
      const inNode = nodes[at].data.slice(0, caret.startOffset).replace(/\s|\u200b/g, '').length;
      const idx = Math.min(drawn.length - 1, before + Math.max(0, inNode - 1));
      const after = inNode > 0 && caret.startOffset >= nodes[at].data.length;
      pos = texFrom + clickOffset(tex, drawn, idx, after || inNode > 1);
    } else if ((e.target as Element).closest?.('.katex-tag')) {
      const t = tex.search(/\\tag\b/);
      pos = texFrom + (t >= 0 ? t : tex.length);
    } else {
      // between symbols: the nearest one by position
      const r = html.getBoundingClientRect();
      const frac = r.width ? Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) : 0;
      pos = texFrom + (drawn.length ? clickOffset(tex, drawn, Math.round(frac * (drawn.length - 1)), frac > 0.5) : 0);
    }
    e.preventDefault();
    view.dispatch({ selection: { anchor: pos }, effects: setFocus.of(true), scrollIntoView: true, userEvent: 'select.pointer' });
    view.focus();
    return true;
  }

  const clicks = EditorView.domEventHandlers({
    mousedown(e, view) {
      const target = e.target as Element;
      const mw = e.button === 0 && !e.shiftKey && !e.metaKey && !e.ctrlKey && target.closest?.<HTMLElement>('.cm-lp-math, .cm-lp-mathblock');
      if (mw && clickIntoMath(e, view, mw)) return true;
      const ref = target.closest?.<HTMLElement>('.cm-lp-chip.eqref');
      if (ref && e.button === 0 && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        const label = ref.dataset.label || '';
        const s = scanOf(view.state);
        const sp = s.spans.find((x) => (x.kind === 'mathblock' || (x.kind === 'math' && x.display))
          && new RegExp(`\\\\label\\s*\\{\\s*${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\}`).test(view.state.doc.sliceString(x.from, x.to)));
        // else a figure with that label
        const at = sp ? sp.from : figuresOfDoc(view.state).figures.find((f) => f.labels.includes(label))?.from;
        if (at != null) view.dispatch({ effects: EditorView.scrollIntoView(at, { y: 'center' }) });
        return true;
      }
      const c = target.closest?.<HTMLElement>('.cm-lp-chip.wikilink, .cm-lp-chip.embed');
      if (c && e.button === 0) {
        e.preventDefault();
        deps.openNote(c.dataset.note || '', c.dataset.heading || '');
        return true;
      }
      const cite = target.closest?.<HTMLElement>('.cm-lp-chip.cite, .cm-lp-citesrc');
      if (cite && (e.metaKey || e.ctrlKey) && e.button === 0) {
        const keys = (cite.dataset.keys || '').split(';');
        const pages = (cite.dataset.pages || '').split(';');
        const atts = (cite.dataset.atts || '').split(';');
        e.preventDefault();
        let spot: CiteTarget | null = null;   // the quote or snapshot the citation belongs to (citeTarget.js)
        try { spot = citeTargetInText(view.state.doc.toString(), view.posAtDOM(cite)); } catch { /* not in the document */ }
        deps.openCite?.(keys[0], pages[0] ? Number(pages[0]) : null, spot, { newTab: e.shiftKey, att: atts[0] ? Number(atts[0]) : null });   // Cmd/Ctrl+Shift: new tab
        return true;
      }
      const link = target.closest?.<HTMLElement>('.cm-lp-link');
      if (link && (e.metaKey || e.ctrlKey) && link.dataset.href) {
        e.preventDefault();
        window.open(link.dataset.href, '_blank', 'noopener');
        return true;
      }
      return false;
    },
  });

  const focusEvents = EditorView.domEventHandlers({
    focus(_e, view) { if (!view.state.field(focusField)) view.dispatch({ effects: setFocus.of(true) }); return false; },
    blur(_e, view) {
      // a click on a widget blurs for a moment: only collapse when focus has really left
      setTimeout(() => { if (!view.hasFocus && view.state.field(focusField)) view.dispatch({ effects: setFocus.of(false) }); }, 0);
      return false;
    },
  });

  return { extension: [focusField, blocks, inline, clicks, focusEvents], refresh };
}
