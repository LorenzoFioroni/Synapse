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
import { GALLERY_INFO, type Scan, type Span, attachmentOf, scan, splitFrontmatter } from './mdscan.js';
import { type CiteTarget, citeTargetInText } from './citeTarget.js';
import { type CiteInfo, type Renderer, citeLabel, imageSrc } from './render.js';
import { type TextRef, numberEquations, resolveRefs, textRefs } from './equations.js';
import { type Lookup, type RefKind, lookupIn, refMissing, refNames, refParts, refPlain } from './crossref.js';
import { clickOffset } from './mathClick.js';
import { type Definition, HAS_DEFINITIONS, type Macros, parseDefinitions, withDefinitions } from './macros.js';
import { type Equations, previewWidgets } from './previewWidgets.js';
import type { TaskPreview } from './workspace.js';
import { type Figures, type Spoiler, noteHooks } from './hooks.js';
import { sectionsOf } from './sections.js';
import { calloutAt, quoteEnd, quotedTableEnd, quoteWrappers, tableSource } from './quoteBlocks.js';
import { previewSelection } from './previewSelection.js';

/** A DOM text node (``Text`` is CodeMirror's document here). */
type Text_ = globalThis.Text;

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
  /** How {{task:id}} lines are drawn (the Tasks module); without it they show as their id. */
  task?(): TaskPreview | null;
}

/** A list item whose only content is a task line: its bullet is not drawn (the checkbox is enough). */
const TASK_ITEM = /^\s*(?:>\s*)*(?:[-*+]|\d+[.)])\s+\{\{task:[a-z0-9]{4,32}\}\}\s*$/;
/** A line that only shows a task (perhaps indented, quoted or in a list item). */
const TASK_LINE = /^\s*(?:>\s*)*(?:(?:[-*+]|\d+[.)])\s+)?\{\{task:[a-z0-9]{4,32}\}\}\s*$/;

/** The block-level decorations, and the ranges they draw (nothing inline is added inside those). */
interface Blocks { deco: DecorationSet; taken: [number, number][]; version: number; quotes: ReturnType<typeof quoteWrappers> }

export function livePreview(cm: CM, deps: PreviewDeps) {
  const { Decoration, StateField, StateEffect, EditorView, ViewPlugin, syntaxTree } = cm;
  const W = previewWidgets(cm, deps);
  const drag = previewSelection(cm);
  const refresh = StateEffect.define<null>();
  // Lines show their source only while you are editing: with the editor unfocused, all is rendered.
  const setFocus = StateEffect.define<boolean>();
  const focusField = StateField.define<boolean>({
    create: () => false,
    update: (v, tr) => tr.effects.reduce((acc, e) => (e.is(setFocus) ? e.value : acc), v),
  });

  /* ------------------------------------------------------- per-document data */
  let cache: { doc: Text | null; scan: Scan | null; refs: TextRef[] | null; eq: Equations | null; mode: string | null; base: Macros | null; figs: Figures | null; sections: ReturnType<typeof sectionsOf> | null; spoilers: { of: unknown; spans: Spoiler[] } | null; atomic: DecorationSet | null } = { doc: null, scan: null, refs: null, eq: null, mode: null, base: null, figs: null, sections: null, spoilers: null, atomic: null };
  let lastBase: Macros | null = null, baseVersion = 0;
  const scanOf = (state: EditorState): Scan => {
    if (cache.doc !== state.doc) cache = { doc: state.doc, scan: scan(state.doc.toString()), refs: null, eq: null, mode: null, base: null, figs: null, sections: null, spoilers: null, atomic: null };
    return cache.scan!;
  };
  // the note's figures and their numbers (Extended Markdown), found with the same rule as the
  // renderer's; none while galleries and figures are off
  const noFigures: Figures = { figures: [], labels: new Map() };
  const figuresOfDoc = (state: EditorState): Figures => {
    const s = scanOf(state);
    const hook = noteHooks.figures;
    if (!hook) return noFigures;
    cache.figs ||= hook.figuresOf(state.doc.toString(), s);
    return cache.figs;
  };
  // the note's spoilers (Extended Markdown), found once per document version: both decoration
  // passes ask on every keystroke
  const spoilersOfDoc = (state: EditorState): Spoiler[] => {
    const s = scanOf(state);
    const hook = noteHooks.extended;
    if (!hook) return [];
    // keyed on the hook too: switching spoilers on or off in Settings gives a new hook, same text
    if (cache.spoilers?.of !== hook) cache.spoilers = { of: hook, spans: hook.spoilers(state.doc.toString(), s) };
    return cache.spoilers.spans;
  };
  // what a reference may name: an equation's label, else a figure's or a gallery panel's
  const sectionsOfDoc = (state: EditorState) => (cache.sections ||= sectionsOf(state.doc.toString(), scanOf(state)));
  const refLookup = (state: EditorState, eq: Equations): Lookup => lookupIn(eq.labels, figuresOfDoc(state).labels, sectionsOfDoc(state).labels);
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
    const held = state.field(drag.held, false);
    const selection = held?.selection ?? state.selection;
    const focused = held ? held.focused : state.field(focusField, false);
    const active: [number, number][] = focused ? selection.ranges.map((r) => [doc.lineAt(r.from).from, doc.lineAt(r.to).to]) : [];
    const isActive = (from: number, to: number) => active.some(([a, b]) => from <= b && to >= a);
    return {
      lineActive: (pos: number) => { const l = doc.lineAt(pos); return isActive(l.from, l.to); },
      blockActive: (from: number, to: number) => isActive(doc.lineAt(from).from, doc.lineAt(to).to),
      // inline things open only when the selection is in them or right next to them
      touches: (from: number, to: number) => !!focused && selection.ranges.some((r) => r.from <= to && r.to >= from),
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

    const spoilers = spoilersOfDoc(state);
    for (const sp of spoilers) {
      if (!sp.block || blockActive(sp.from, sp.to) || overlapsTaken(sp.from, sp.to)) continue;
      taken.push([sp.from, sp.to]);
      out.push(Decoration.replace({ widget: W.spoiler(doc.sliceString(sp.from, sp.to), String(version), true), block: true }).range(sp.from, sp.to));
    }

    // A LaTeX-style table of contents uses the headings from the whole note.
    for (let n = 1; n <= doc.lines; n++) {
      const line = doc.line(n);
      if (line.text.trim() !== '\\tableofcontents' || overlapsTaken(line.from, line.to)) continue;
      if (blockActive(line.from, line.to)) lineDeco(line.from, 'cm-lp-tocsrc');
      else {
        taken.push([line.from, line.to]);
        out.push(Decoration.replace({ widget: W.toc(sectionsOfDoc(state).sections, String(version)), block: true }).range(line.from, line.to));
      }
    }

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
      if (overlapsTaken(sp.from, sp.to)) continue;
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
        if (taken.some(([a, b]) => node.from >= a && node.to <= b)) return false;
        if (name === 'Table') {
          // in a quote, only the lines the read view counts as the table (the rest stay source)
          const end = quotedTableEnd(doc, node.node);
          if (end === null) return false;
          const from = doc.lineAt(node.from).from, to = doc.lineAt(end).to;
          if (blockActive(from, to) || overlapsTaken(from, to)) lineDecos(from, to, () => 'cm-lp-tablesrc');
          else {
            // drawn by the renderer, so math, citations and links in its cells are rendered too
            const lookup = refLookup(state, eq);
            taken.push([from, to]);
            out.push(Decoration.replace({ widget: W.table(tableSource(doc, node.node, end), lookup, `${version}:${eq.sig}`), block: true }).range(from, to));
          }
          return false;
        }
        if (name === 'FencedCode') {
          const from = doc.lineAt(node.from).from, to = doc.lineAt(node.to).to;
          const info = node.node.getChild('CodeInfo');
          const lang = info ? doc.sliceString(info.from, info.to).trim().toLowerCase() : '';
          if (noteHooks.extended?.fence(lang)) {
            const src = doc.sliceString(from, to);
            taken.push([from, to]);
            if (blockActive(from, to)) lineDecos(from, to, (first, last) => `cm-lp-code${first ? ' cm-lp-code-first' : ''}${last ? ' cm-lp-code-last' : ''}`);
            else out.push(Decoration.replace({ widget: W.markdownBlock(src, String(version)), block: true }).range(from, to));
            return false;
          }
          if (GALLERY_INFO.test(lang)) {
            // with galleries off a gallery is its lines, as any text
            if (!noteHooks.figures) return false;
            // a gallery is one unit: drawn as its carousel, or, while the cursor is in it, its
            // source with the carousel below it (nothing inside the source is rendered)
            const src = doc.sliceString(from, to);
            // its figure number, and \ref in its captions, come from the whole note: redraw when they change
            const fig = figuresOfDoc(state).figures.find((f) => f.kind === 'gallery' && f.from === from)?.number ?? null;
            const lookup = refLookup(state, eq);
            const key = `${version}:${eq.sig}:${fig}${/\\(eq|c|C)?ref\{/.test(src) ? `:${[...eq.labels].join(';')}|${[...figuresOfDoc(state).labels].join(';')}` : ''}`;
            taken.push([from, to]);
            if (blockActive(from, to)) {
              lineDecos(from, to, (first, last) => `cm-lp-gallerysrc${first ? ' first' : ''}${last ? ' last' : ''}`);
              out.push(Decoration.widget({ widget: W.gallery(src, lookup, fig, key, true), block: true, side: 1 }).range(to));
            } else out.push(Decoration.replace({ widget: W.gallery(src, lookup, fig, key, false), block: true }).range(from, to));
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
    return { deco: Decoration.set(out, true), taken, version, quotes: quoteWrappers(cm, state, s.spans) };
  }

  const blocks = StateField.define<Blocks>({
    create: (state) => buildBlocks(state, 0),
    update(v, tr) {
      const refreshed = tr.effects.some((e) => e.is(refresh));
      if (refreshed || tr.docChanged || tr.selection || tr.startState.field(drag.held, false) !== tr.state.field(drag.held, false) || tr.effects.some((e) => e.is(setFocus))
          || syntaxTree(tr.startState) !== syntaxTree(tr.state)) return buildBlocks(tr.state, v.version + (refreshed ? 1 : 0));
      return v;
    },
    provide: (f) => [EditorView.decorations.from(f, (v) => v.deco), EditorView.blockWrappers.from(f, (v) => v.quotes)],
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

    // CodeMirror's Markdown grammar does not know the LaTeX-style `##* Heading` extension.
    // Draw the entire line as an inline-rendered heading when it is not being edited, so it has
    // the same typography, formatting and math as an ordinary heading.
    for (const section of sectionsOfDoc(state).sections) {
      const line = doc.lineAt(section.from);
      if (!/^( {0,3}#{1,6})\*([ \t]+)/.test(line.text) || !shown(line.from, line.to)) continue;
      lineDeco(line.from, `cm-lp-h cm-lp-h${section.level}`);
      if (lineActive(line.from)) continue;
      taken.push([line.from, line.to]);
      replace(line.from, line.to, Decoration.replace({
        widget: W.heading(section.text, section.level, section.labels.length ? section.number : '', String(state.field(blocks).version)),
      }));
    }

    for (const sp of spoilersOfDoc(state)) {
      if (!sp.block && shown(sp.from, sp.to) && !touches(sp.from, sp.to)) {
        taken.push([sp.from, sp.to]);
        replace(sp.from, sp.to, Decoration.replace({ widget: W.spoiler(doc.sliceString(sp.from, sp.to), String(state.field(blocks).version), false) }));
      }
    }

    // math on one line (from the scanner: same rules as the backend)
    for (const sp of s.spans) {
      if (sp.kind !== 'math' || overlapsTaken(sp.from, sp.to) || !shown(sp.from, sp.to) || multiLine(doc, sp.from, sp.to)) continue;
      taken.push([sp.from, sp.to]);
      if (touches(sp.from, sp.to)) add(sp.from, sp.to, Decoration.mark({ class: 'cm-lp-mathsrc' }));
      else replace(sp.from, sp.to, Decoration.replace({ widget: mathWidget(doc, eq, sp, false) }));
    }

    // \eqref, \ref, \cref, \Cref in the text: the equations' or figures' numbers (crossref.ts)
    const lookup = refLookup(state, eq);
    for (const r of refsOf(state)) {
      if (!shown(r.from, r.to) || overlapsTaken(r.from, r.to)) continue;
      taken.push([r.from, r.to]);
      if (touches(r.from, r.to)) { add(r.from, r.to, Decoration.mark({ class: 'cm-lp-linksrc eqref' })); continue; }
      const names = refNames(r.kind, r.name);
      const parts = refParts(r.kind as RefKind, names, lookup);
      const text = refPlain(parts), missing = refMissing(parts);
      replace(r.from, r.to, Decoration.replace({ widget: W.chip(`e:${r.from}:${text}:${missing}`, `eqref${missing ? ' missing' : ''}`, text, { label: names[0] || '' }) }));
    }

    // Labels on headings are hidden until that line is edited; labelled headings show their section number.
    for (const section of sectionsOfDoc(state).sections) {
      if (!shown(section.from, section.from)) continue;
      const line = doc.lineAt(section.from);
      const starred = /^( {0,3}#{1,6})\*([ \t]+)/.exec(line.text);
      if (starred) continue;   // rendered as a whole above (or shown as source while edited)
      if (lineActive(section.from)) continue;
      if (section.labels.length) out.push(Decoration.widget({ widget: W.chip(`sec:${section.from}:${section.number}`, 'section-num', section.number, {}), side: 1 }).range(section.from));
      for (const label of section.labels) replace(label.from, label.to, hide);
    }

    // an image's caption line: its figure number in front, its \label hidden, until you edit it
    const figureLabel = noteHooks.figures?.label;
    for (const f of figureLabel ? figuresOfDoc(state).figures : []) {
      if (f.kind !== 'image' || !shown(f.captionFrom!, f.to)) continue;
      lineDeco(f.captionFrom!, 'cm-lp-figcaption');
      if (lineActive(f.captionFrom!)) continue;
      out.push(Decoration.widget({ widget: W.chip(`fig:${f.number}`, 'fignum', `${figureLabel!(f.number!)}.`, {}), side: -1 }).range(f.captionFrom!));
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
      if (!shown(l.from, l.to) || overlapsTaken(l.from, l.to)) continue;
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
      if (l.kind === 'task') {
        // a task never shows its {{task:…}} source in the live preview: the cursor steps over it
        const hooks = deps.task?.();
        // a line of its own is laid out as a row, so the task takes the width left after any indentation
        const tline = doc.lineAt(l.from);
        if (hooks && TASK_LINE.test(tline.text)) {
          lineDeco(l.from, 'cm-lp-taskline');
          // up to three spaces before it mean nothing in Markdown (read mode ignores them): not drawn,
          // so its box lines up with the others
          const pad = /^ {1,3}(?=\{\{task:)/.exec(tline.text);
          if (pad) replace(tline.from, tline.from + pad[0].length, hide);
        }
        if (hooks) replace(l.from, l.to, Decoration.replace({ widget: W.task(l.target, hooks) }));
        else if (!touches(l.from, l.to)) replace(l.from, l.to, Decoration.replace({ widget: W.chip(`k:${l.from}:${l.target}`, 'taskref', `Task ${l.target}`, { task: l.target }) }));
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
        // An active display-math block deliberately shows its source. CodeMirror still parses
        // Markdown inside that source (for example ``\,`` as an escape and ``_x_`` as emphasis),
        // but the note renderer treats every character between the $$ marks as TeX. Do not let
        // live-preview Markdown decorations hide or restyle those TeX commands.
        if (s.spans.some((sp) => sp.kind === 'mathblock' && node.from >= sp.from && node.to <= sp.to)) return false;
        if (/^ATXHeading[1-6]$/.test(name)) {
          lineDeco(node.from, `cm-lp-h cm-lp-h${name.slice(-1)}`);
          if (!lineActive(node.from)) {
            const mark = node.node.getChild('HeaderMark');
            if (mark && mark.from === node.from) replace(mark.from, Math.min(mark.to + 1, node.to), hide);
          }
          return;
        }
        if (name === 'Emphasis' || name === 'StrongEmphasis' || name === 'Strikethrough' || name === 'Underline' || name === 'InlineCode') {
          if (overlapsTaken(node.from, node.to) || touches(node.from, node.to)) return name === 'InlineCode' ? false : undefined;
          const markName = name === 'Underline' ? 'UnderlineMark' : name === 'Strikethrough' ? 'StrikethroughMark' : name === 'InlineCode' ? 'CodeMark' : 'EmphasisMark';
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
        if (name === 'QuoteMark') {
          if (!lineActive(node.from)) replace(node.from, node.to, hide);
          return false;
        }
        if (name === 'Escape') {
          if (!touches(node.from, node.to) && !overlapsTaken(node.from, node.to)) replace(node.from, node.from + 1, hide);
          return false;
        }
        if (name === 'ListMark') {
          const item = node.node.parent;
          if (!item || item.parent?.name !== 'BulletList' || lineActive(node.from)) return false;
          if (TASK_ITEM.test(doc.lineAt(node.from).text)) { replace(node.from, Math.min(node.to + 1, doc.lineAt(node.from).to), hide); return false; }
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
          const callout = calloutAt(doc, node.from);
          const type = callout?.type;
          const end = quoteEnd(doc, node.node);
          for (let p = node.from; p <= end; p = doc.lineAt(p).to + 1) {
            const line = doc.lineAt(p);
            lineDeco(p, type ? `cm-lp-callout cm-lp-callout-${type}${line.from === first.from ? ' cm-lp-callout-head' : ''}` : 'cm-lp-quote');
            if (line.to >= end) break;
          }
          if (callout && !lineActive(first.from)) {
            const title = callout.title ? '' : callout.fallback;
            replace(callout.markFrom, Math.min(callout.markTo + (callout.title ? 1 : 0), first.to), Decoration.replace({ widget: W.calloutTitle(type!, title) }));
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
          || u.startState.field(drag.held, false) !== u.state.field(drag.held, false)
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
      if (target.closest('.cm-lp-spoiler .spoiler-toggle, .cm-lp-spoiler summary')) return false;
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
        const at = sp ? sp.from : figuresOfDoc(view.state).figures
          .find((f) => f.labels.includes(label) || f.gallery?.panels.some((p) => p.labels.includes(label)))?.from;
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

  // task lines are one unit for the cursor: arrows step over them, Backspace deletes the whole line's token
  const atomic = EditorView.atomicRanges.of((view) => {
    const st = view.state;
    scanOf(st);
    if (!cache.atomic) {
      const s = cache.scan!;
      cache.atomic = Decoration.set(s.links.filter((l) => l.kind === 'task').map((l) => Decoration.mark({}).range(l.from, l.to)), true);
    }
    return cache.atomic;
  });

  return { extension: [drag.extension, focusField, blocks, inline, clicks, focusEvents, atomic], refresh };
}
