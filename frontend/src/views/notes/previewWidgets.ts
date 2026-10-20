// The widgets the live preview (livePreview.ts) draws in place of Markdown source: formulas,
// chips for links and citations, task boxes, the front-matter box, tables, diagrams and galleries.
//
// CodeMirror keeps a widget's DOM as long as a new widget at the same place is `eq` to it, so each
// widget's key holds everything it draws from, and nothing else: a widget whose key did not
// change is never drawn again.
import type { EditorView as EditorViewT } from '@codemirror/view';
import type { CM } from './editor.js';
import type { Macros } from './macros.js';
import type { Lookup, RefKind } from './crossref.js';
import { refHtml } from './render.js';
import { fitSoon } from './mathFit.js';
import type { PreviewDeps } from './livePreview.js';

/** The note's formulas, prepared once per version of the document (livePreview.ts, equationsOf). */
export interface Equations {
  tex: Map<number, string>;
  labels: Map<string, string | null>;
  macros: Macros;
  sig: string;
  defines: Map<number, string[]>;
}

export function previewWidgets(cm: CM, deps: PreviewDeps) {
  const { WidgetType } = cm;

  class HtmlWidget extends WidgetType {
    key: string;
    make: (el: HTMLElement) => void;
    block: boolean;
    cls: string;
    constructor(key: string, make: (el: HTMLElement) => void, { block = false, cls = '' } = {}) {
      super(); this.key = key; this.make = make; this.block = block; this.cls = cls;
    }
    eq(o: HtmlWidget) { return o.key === this.key; }
    toDOM(_view?: EditorViewT): HTMLElement {
      const el = document.createElement(this.block ? 'div' : 'span');
      el.className = `cm-lp-widget ${this.cls}`;
      this.make(el);
      return el;
    }
    ignoreEvent(e: Event) { return e.type !== 'mousedown'; }   // a click places the cursor (and reveals the source)
  }

  // the carousel's arrows and dots turn it (galleryView.ts); a click anywhere else opens the source
  class GalleryWidget extends HtmlWidget {
    ignoreEvent(e: Event) { return !!(e.target as Element | null)?.closest?.('.gallery-btn, .gallery-dot') || super.ignoreEvent(e); }
  }

  class CheckboxWidget extends WidgetType {
    checked: boolean;
    pos: number;
    constructor(checked: boolean, pos: number) { super(); this.checked = checked; this.pos = pos; }
    eq(o: CheckboxWidget) { return o.checked === this.checked && o.pos === this.pos; }
    toDOM(view: EditorViewT) {
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

  // a formula; ``eq`` (equationsOf) brings the note's macros, whose signature is part of the key,
  // so a changed \newcommand redraws the formulas that may use it
  class MathWidget extends HtmlWidget {
    display: boolean;
    constructor(tex: string, display: boolean, block: boolean, preview: boolean, eq: Equations) {
      super(`m:${display}:${block}:${preview}:${eq.sig}:${tex}`, (el) => {
        const r = deps.renderer();
        el.innerHTML = r ? r.katex(tex, display, eq.macros) : tex;
      }, { block, cls: (block ? 'cm-lp-mathblock' : display ? 'cm-lp-math display' : 'cm-lp-math') + (preview ? ' cm-lp-mathpreview' : '') });
      this.display = display;
    }
    toDOM(view: EditorViewT) {
      const el = super.toDOM(view);
      if (this.display) fitSoon(el, () => view.requestMeasure());
      return el;
    }
  }

  return {
    HtmlWidget,
    bullet: new HtmlWidget('bullet', (el) => { el.textContent = '•'; }, { cls: 'cm-lp-bullet' }),
    hr: new HtmlWidget('hr', () => {}, { cls: 'cm-lp-hr' }),
    checkbox: (checked: boolean, pos: number) => new CheckboxWidget(checked, pos),
    katex: (eq: Equations, tex: string, display: boolean, block = false, preview = false) => new MathWidget(tex, display, block, preview, eq),

    /** A formula that only defines macros: a small label (click it to edit the definitions). */
    defs: (names: string[], block: boolean) => new HtmlWidget(`defs:${block}:${names.join(' ')}`, (el) => {
      el.textContent = `Macros: ${names.join('  ')}`;
      el.title = 'Macro definitions, used by every formula of this note: click to edit';
    }, { block, cls: 'cm-lp-mathdefs' }),

    chip: (key: string, cls: string, label: string, attrs: Record<string, string | number | null | undefined>) => new HtmlWidget(key, (el) => {
      el.textContent = label;
      for (const [k, v] of Object.entries(attrs)) if (v != null) el.dataset[k] = String(v);
    }, { cls: `cm-lp-chip ${cls}` }),

    image: (src: string, alt: string, width: number | null | undefined, target: string) => new HtmlWidget(`img:${src}:${width}`, (el) => {
      const img = document.createElement('img');
      img.src = src; img.alt = alt; img.loading = 'lazy';
      if (width) img.width = width;
      img.onerror = () => { el.classList.add('broken'); el.textContent = `Image not found: ${target}`; };
      el.appendChild(img);
    }, { cls: 'cm-lp-image' }),

    calloutTitle: (type: string, title: string) => new HtmlWidget(`co:${type}:${title}`, (el) => { el.textContent = title; }, { cls: `cm-lp-callout-icon ${type}` }),

    /** A table rendered as in read mode. ``version`` changes when link or library data did. */
    table: (src: string, version: string) => new HtmlWidget(`t:${version}:${src}`, (el) => {
      const r = deps.renderer();
      el.innerHTML = r ? r.render(src) : '';
    }, { block: true, cls: 'cm-lp-table' }),

    /** A gallery's carousel, drawn by the renderer; ``preview``: shown below its source while it is edited. */
    /** A gallery's carousel, drawn by the renderer; ``preview``: shown below its source while it is
     *  edited. Its figure number and \ref numbers come from the whole note (``fig``, ``labels``). */
    gallery: (src: string, lookup: Lookup, fig: number | null, version: string, preview: boolean) => new GalleryWidget(`g:${version}:${preview}:${src}`, (el) => {
      const r = deps.renderer();
      el.innerHTML = r ? r.render(src) : '';
      if (fig != null) el.querySelectorAll('.fig-num').forEach((n) => { n.textContent = n.textContent!.replace(/\d+/, String(fig)); });
      // written again with the whole note's labels (the renderer saw only this block)
      for (const ref of el.querySelectorAll<HTMLElement>('.cref[data-names]')) {
        ref.outerHTML = refHtml(ref.dataset.kind as RefKind, (ref.dataset.names || '').split(','), lookup);
      }
      for (const ref of el.querySelectorAll<HTMLElement>('.eqref[data-label]')) {
        if (!ref.closest('.cref')) ref.outerHTML = refHtml((ref.dataset.kind || 'eqref') as RefKind, [ref.dataset.label!], lookup);
      }
    }, { block: true, cls: `cm-lp-gallery${preview ? ' cm-lp-gallerypreview' : ''}` }),

    mermaid: (code: string) => new HtmlWidget(`mm:${code}:${document.documentElement.dataset.theme}`, (el) => deps.mermaid(el, code), { block: true, cls: 'cm-lp-mermaid' }),

    /** "key: value" lines (and "- item" lists) of the front matter, shown as a small table. */
    props: (header: string) => new HtmlWidget(`fm:${header}`, (el) => {
      const rows: [string, string][] = [];
      for (const line of header.split('\n')) {
        const m = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
        if (m) rows.push([m[1], m[2].replace(/^\[(.*)\]$/, '$1').replace(/^["']|["']$/g, '')]);
        else if (/^\s+-\s+/.test(line) && rows.length) {
          const last = rows[rows.length - 1];
          last[1] = (last[1] ? `${last[1]}, ` : '') + line.replace(/^\s+-\s+/, '').replace(/^["']|["']$/g, '');
        }
      }
      const shown = rows.filter(([k, v]) => k !== 'id' && v !== '');
      el.title = 'Properties (front matter): click to edit';
      const dl = document.createElement('dl');
      for (const [k, v] of shown) {
        const dt = document.createElement('dt'); dt.textContent = k;
        const dd = document.createElement('dd'); dd.textContent = v;
        dl.append(dt, dd);
      }
      if (!shown.length) { const dd = document.createElement('dd'); dd.textContent = 'Properties'; dl.append(dd); }
      el.appendChild(dl);
    }, { block: true, cls: 'cm-lp-props' }),
  };
}
