// Live preview for the notes editor (CodeMirror decorations).
//
// Rule: a line shows its raw Markdown while the cursor (or selection) is on it, and is shown
// rendered otherwise. Multi-line constructs (tables, $$ blocks, Mermaid diagrams) count as
// one unit: touching any of their lines reveals all of them.
//
// What counts as math, a [[link]] or a [@citation] comes from mdscan.js (the port of the
// backend parser); everything else from CodeMirror's own Markdown syntax tree.
import { scan } from './mdscan.js';
import { citeLabel, imageSrc } from './render.js';

const CALLOUT_TYPES = ['note', 'tip', 'warning', 'question', 'quote', 'info', 'important', 'caution', 'example', 'todo'];

/**
 * @param {object} cm        the CodeMirror bundle
 * @param {object} deps
 *   renderer()              → {render(src), katex(tex, display)} or null while loading
 *   resolveNote(target)     → path | null (does not exist) | undefined (unknown)
 *   citeInfo(key)           → library info or null
 *   openNote(target, heading)
 *   mermaid(el, code)       render a diagram into el
 */
export function livePreview(cm, deps) {
  const { Decoration, WidgetType, StateField, StateEffect, EditorView, syntaxTree } = cm;
  const refresh = StateEffect.define();

  /* ------------------------------------------------------------------ widgets */
  class HtmlWidget extends WidgetType {
    constructor(key, make, { block = false, cls = '' } = {}) { super(); this.key = key; this.make = make; this.block = block; this.cls = cls; }
    eq(o) { return o.key === this.key; }
    toDOM() {
      const el = document.createElement(this.block ? 'div' : 'span');
      el.className = `cm-lp-widget ${this.cls}`;
      this.make(el);
      return el;
    }
    ignoreEvent(e) { return e.type !== 'mousedown'; }   // a click places the cursor (and reveals the source)
  }

  class CheckboxWidget extends WidgetType {
    constructor(checked, pos) { super(); this.checked = checked; this.pos = pos; }
    eq(o) { return o.checked === this.checked && o.pos === this.pos; }
    toDOM(view) {
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.className = 'cm-lp-task';
      box.checked = this.checked;
      box.addEventListener('mousedown', (e) => {
        e.preventDefault();
        view.dispatch({ changes: { from: this.pos + 1, to: this.pos + 2, insert: this.checked ? ' ' : 'x' } });
      });
      return box;
    }
    ignoreEvent() { return true; }
  }

  const bullet = new HtmlWidget('bullet', (el) => { el.textContent = '•'; }, { cls: 'cm-lp-bullet' });

  const katexWidget = (tex, display, block = false) => new HtmlWidget(`m:${display}:${block}:${tex}`, (el) => {
    const r = deps.renderer();
    el.innerHTML = r ? r.katex(tex, display) : tex;
  }, { block, cls: block ? 'cm-lp-mathblock' : display ? 'cm-lp-math display' : 'cm-lp-math' });

  const chip = (key, cls, label, attrs) => new HtmlWidget(key, (el) => {
    el.textContent = label;
    for (const [k, v] of Object.entries(attrs)) if (v != null) el.dataset[k] = v;
  }, { cls: `cm-lp-chip ${cls}` });

  /* ------------------------------------------------------------------ build */
  let cache = { doc: null, scan: null };
  const scanOf = (state) => {
    if (cache.doc !== state.doc) cache = { doc: state.doc, scan: scan(state.doc.toString()) };
    return cache.scan;
  };

  function build(state) {
    const doc = state.doc;
    const s = scanOf(state);
    const out = [];
    const add = (from, to, deco) => { if (from <= to) out.push(deco.range(from, to)); };
    const lineDeco = (pos, cls) => out.push(Decoration.line({ class: cls }).range(doc.lineAt(pos).from));

    const active = state.selection.ranges.map((r) => [doc.lineAt(r.from).from, doc.lineAt(r.to).to]);
    const isActive = (from, to) => active.some(([a, b]) => from <= b && to >= a);
    const lineActive = (pos) => { const l = doc.lineAt(pos); return isActive(l.from, l.to); };
    const blockActive = (from, to) => isActive(doc.lineAt(from).from, doc.lineAt(to).to);
    const hide = Decoration.replace({});

    // regions handled by the scanner win over CodeMirror's own nodes
    const taken = [];
    const overlapsTaken = (from, to) => taken.some(([a, b]) => from < b && to > a);

    // math (from the scanner: same rules as the backend)
    for (const sp of s.spans) {
      if (sp.kind === 'mathblock' && !sp.open) {
        const from = doc.lineAt(sp.from).from, to = doc.lineAt(sp.to).to;
        taken.push([from, to]);
        if (blockActive(from, to)) { for (let p = from; p <= to; p = doc.lineAt(p).to + 1) lineDeco(p, 'cm-lp-mathsrc'); continue; }
        const src = doc.sliceString(from, to).trim().replace(/^\$\$/, '').replace(/\$\$$/, '');
        add(from, to, Decoration.replace({ widget: katexWidget(src.trim(), true, true), block: true }));
      } else if (sp.kind === 'math') {
        taken.push([sp.from, sp.to]);
        if (isActive(sp.from, sp.to)) { add(sp.from, sp.to, Decoration.mark({ class: 'cm-lp-mathsrc' })); continue; }
        const d = sp.display ? 2 : 1;
        add(sp.from, sp.to, Decoration.replace({ widget: katexWidget(doc.sliceString(sp.from + d, sp.to - d), sp.display) }));
      }
    }

    // [[links]], ![[embeds]], [@citations], images
    const groups = new Map();
    for (const l of s.links) {
      if (l.kind === 'cite') { (groups.get(l.from) || groups.set(l.from, []).get(l.from)).push(l); continue; }
      taken.push([l.from, l.to]);
      if (l.kind === 'image') {
        if (lineActive(l.from)) continue;
        const src = imageSrc(l.target);
        add(l.from, l.to, Decoration.replace({ widget: new HtmlWidget(`img:${src}:${l.width}`, (el) => {
          const img = document.createElement('img');
          img.src = src; img.alt = l.alias; img.loading = 'lazy';
          if (l.width) img.width = l.width;
          img.onerror = () => { el.classList.add('broken'); el.textContent = `Image not found: ${l.target}`; };
          el.appendChild(img);
        }, { cls: 'cm-lp-image' }) }));
        continue;
      }
      const path = deps.resolveNote(l.target);
      const cls = `${l.kind === 'embed' ? 'embed' : 'wikilink'}${path === null && l.target ? ' unresolved' : ''}`;
      if (isActive(l.from, l.to)) { add(l.from, l.to, Decoration.mark({ class: `cm-lp-linksrc ${cls}` })); continue; }
      const label = (l.kind === 'embed' ? '↪ ' : '') + (l.alias || (l.target ? l.target + (l.heading ? ` › ${l.heading}` : '') : `› ${l.heading}`));
      add(l.from, l.to, Decoration.replace({ widget: chip(`n:${l.from}:${label}:${cls}`, cls, label, { note: l.target, heading: l.heading, path }) }));
    }
    for (const [from, ls] of groups) {
      const to = ls[0].to;
      taken.push([from, to]);
      const missing = ls.some((l) => !deps.citeInfo(l.target));
      const trashed = ls.some((l) => deps.citeInfo(l.target)?.trashed);
      const keys = ls.map((l) => l.target).join(';');
      const pages = ls.map((l) => l.page ?? '').join(';');
      if (isActive(from, to)) { add(from, to, Decoration.mark({ class: `cm-lp-citesrc${missing ? ' missing' : ''}`, attributes: { 'data-keys': keys, 'data-pages': pages } })); continue; }
      const label = `(${ls.map((l) => citeLabel({ key: l.target, prefix: l.prefix, suppress: l.suppress_author, locator: l.locator }, deps.citeInfo(l.target))).join('; ')})`;
      add(from, to, Decoration.replace({ widget: chip(`c:${from}:${label}:${missing}:${trashed}`, `cite${missing ? ' missing' : ''}${trashed ? ' trashed' : ''}`, label, { keys, pages }) }));
    }
    for (const t of s.tags) add(t.from, t.to, Decoration.mark({ class: 'cm-lp-tag' }));

    // CodeMirror syntax tree: headings, emphasis, quotes, callouts, tasks, rules, tables, code
    const tree = syntaxTree(state);
    tree.iterate({
      from: s.bodyStart,
      enter: (node) => {
        const name = node.name;
        if (/^ATXHeading[1-6]$/.test(name)) {
          lineDeco(node.from, `cm-lp-h cm-lp-h${name.slice(-1)}`);
          if (!lineActive(node.from)) {
            const mark = node.node.getChild('HeaderMark');
            if (mark && mark.from === node.from) add(mark.from, Math.min(mark.to + 1, node.to), hide);
          }
          return;
        }
        if (name === 'Emphasis' || name === 'StrongEmphasis' || name === 'Strikethrough' || name === 'InlineCode') {
          if (overlapsTaken(node.from, node.to) || lineActive(node.from)) return name === 'InlineCode' ? false : undefined;
          const markName = name === 'Strikethrough' ? 'StrikethroughMark' : name === 'InlineCode' ? 'CodeMark' : 'EmphasisMark';
          for (const m of node.node.getChildren(markName)) add(m.from, m.to, hide);
          return name === 'InlineCode' ? false : undefined;
        }
        if (name === 'Link') {
          if (overlapsTaken(node.from, node.to)) return false;
          const url = node.node.getChild('URL');
          if (!url) return false;   // [text] without a destination: leave as typed
          add(node.from, node.to, Decoration.mark({ class: 'cm-lp-link', attributes: { 'data-href': doc.sliceString(url.from, url.to) } }));
          if (lineActive(node.from)) return false;
          const marks = node.node.getChildren('LinkMark');
          // hide "[" and "](url "title")"
          if (marks[0]) add(marks[0].from, marks[0].to, hide);
          if (marks[1]) add(marks[1].from, node.to, hide);
          return false;
        }
        if (name === 'Image') return false;
        if (name === 'Escape') {
          if (!lineActive(node.from) && !overlapsTaken(node.from, node.to)) add(node.from, node.from + 1, hide);
          return false;
        }
        if (name === 'ListMark') {
          const item = node.node.parent;
          if (!item || item.parent?.name !== 'BulletList' || lineActive(node.from)) return false;
          if (item.getChild('Task')) add(node.from, Math.min(node.to + 1, doc.lineAt(node.from).to), hide);   // the checkbox is enough
          else add(node.from, node.to, Decoration.replace({ widget: bullet }));
          return false;
        }
        if (name === 'HorizontalRule') {
          if (!lineActive(node.from)) add(node.from, node.to, Decoration.replace({ widget: new HtmlWidget('hr', () => {}, { cls: 'cm-lp-hr' }) }));
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
              if (qm) add(line.from, line.from + qm[1].length, hide);
            }
            if (line.to >= node.to) break;
          }
          if (m && !lineActive(first.from)) {
            const start = first.from + first.text.indexOf('[!');
            const end = start + m[1].length + 3 + m[2].length;
            const title = m[3] ? '' : m[1][0].toUpperCase() + m[1].slice(1).toLowerCase();
            add(start, Math.min(end + (m[3] ? 1 : 0), first.to), Decoration.replace({ widget: new HtmlWidget(`co:${type}:${title}`, (el) => { el.textContent = title; }, { cls: `cm-lp-callout-icon ${type}` }) }));
          }
          return undefined;
        }
        if (name === 'TaskMarker') {
          if (!lineActive(node.from)) {
            const checked = /x/i.test(doc.sliceString(node.from, node.to));
            add(node.from, node.to, Decoration.replace({ widget: new CheckboxWidget(checked, node.from) }));
          }
          return false;
        }
        if (name === 'Table') {
          const from = doc.lineAt(node.from).from, to = doc.lineAt(node.to).to;
          if (blockActive(from, to) || overlapsTaken(from, to)) { for (let p = from; p <= to; p = doc.lineAt(p).to + 1) lineDeco(p, 'cm-lp-tablesrc'); return false; }
          const src = doc.sliceString(from, to);
          add(from, to, Decoration.replace({ widget: new HtmlWidget(`t:${src}`, (el) => {
            const r = deps.renderer();
            el.innerHTML = r ? r.render(src) : '';
          }, { block: true, cls: 'cm-lp-table' }), block: true }));
          return false;
        }
        if (name === 'FencedCode') {
          const from = doc.lineAt(node.from).from, to = doc.lineAt(node.to).to;
          const info = node.node.getChild('CodeInfo');
          const lang = info ? doc.sliceString(info.from, info.to).trim().toLowerCase() : '';
          if (lang === 'mermaid' && !blockActive(from, to)) {
            const textNode = node.node.getChild('CodeText');
            const code = textNode ? doc.sliceString(textNode.from, textNode.to) : '';
            add(from, to, Decoration.replace({ widget: new HtmlWidget(`mm:${code}:${document.documentElement.dataset.theme}`, (el) => deps.mermaid(el, code), { block: true, cls: 'cm-lp-mermaid' }), block: true }));
            return false;
          }
          for (let p = from; p <= to; p = doc.lineAt(p).to + 1) {
            const l = doc.lineAt(p);
            lineDeco(p, `cm-lp-code${l.from === from ? ' cm-lp-code-first' : ''}${l.to === to ? ' cm-lp-code-last' : ''}`);
            if (l.to >= to) break;
          }
          return false;
        }
        return undefined;
      },
    });
    return Decoration.set(out, true);
  }

  const field = StateField.define({
    create: (state) => build(state),
    update(deco, tr) {
      if (tr.docChanged || tr.selection || tr.effects.some((e) => e.is(refresh))
          || syntaxTree(tr.startState) !== syntaxTree(tr.state)) return build(tr.state);
      return deco;
    },
    provide: (f) => EditorView.decorations.from(f),
  });

  const clicks = EditorView.domEventHandlers({
    mousedown(e) {
      const c = e.target.closest?.('.cm-lp-chip.wikilink, .cm-lp-chip.embed');
      if (c && e.button === 0) {
        e.preventDefault();
        deps.openNote(c.dataset.note || '', c.dataset.heading || '');
        return true;
      }
      const cite = e.target.closest?.('.cm-lp-chip.cite, .cm-lp-citesrc');
      if (cite && (e.metaKey || e.ctrlKey) && e.button === 0) {
        const keys = (cite.dataset.keys || '').split(';');
        const pages = (cite.dataset.pages || '').split(';');
        e.preventDefault();
        deps.openCite?.(keys[0], pages[0] ? Number(pages[0]) : null);
        return true;
      }
      const link = e.target.closest?.('.cm-lp-link');
      if (link && (e.metaKey || e.ctrlKey) && link.dataset.href) {
        e.preventDefault();
        window.open(link.dataset.href, '_blank', 'noopener');
        return true;
      }
      return false;
    },
  });

  return { extension: [field, clicks], refresh };
}
