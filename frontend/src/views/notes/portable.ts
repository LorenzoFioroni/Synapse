// A note as other programs can show it (exportNote.ts): Markdown without the notes' own syntax,
// citations written out with a list of references, and the reference entries HTML and PDF
// exports use. No DOM (tests/js/portable.test.mjs).
import { textRefs } from './equations.js';
import { figureLabel, figuresOf } from './figures.js';
import { galleryMarkdown, italic } from './gallery.js';
import { mask, scan, splitFrontmatter } from './mdscan.js';
import { type CiteInfo, citeLabel } from './render.js';

/** What a reference entry is made from: a library item (GET /api/items?ids=), or at least the
 *  citation index's entry for it (any CiteEntry fits). */
export interface RefInfo extends CiteInfo {
  id?: string;   // the library item, to load all of its fields
  item_type?: string; journal?: string | null; volume?: string; issue?: string; pages?: string;
  publisher?: string; place?: string; edition?: string; series?: string;
  doi?: string; url?: string; archive_id?: string;
}

/** A note converted to portable Markdown, and the keys it cites (in order, embeds included). */
export interface Portable { markdown: string; keys: string[] }

export interface PortableOptions {
  citeInfo: (key: string) => RefInfo | null;
  /** The converted text of an embedded note (or one of its sections), null when it is not available. */
  embed?: (target: string, heading: string) => Portable | null;
}

/** "Smith, A. M., Doe, J. & Roe, R." from "Smith, Anna Maria; Doe, John; Roe, R." */
export function formatAuthors(authors: string | null | undefined): string {
  const names = String(authors || '').split(';').map((a) => a.trim()).filter(Boolean).map((a) => {
    const comma = a.indexOf(',');
    if (comma < 0) return a;   // an institution, or a name written without a comma
    const given = a.slice(comma + 1).trim().split(/[\s.]+/).filter(Boolean)
      .map((g) => g.split('-').map((p) => `${[...p][0]}.`).join('-')).join(' ');
    return given ? `${a.slice(0, comma).trim()}, ${given}` : a.slice(0, comma).trim();
  });
  if (names.length <= 1) return names[0] || '';
  return `${names.slice(0, -1).join(', ')} & ${names[names.length - 1]}`;
}

/** Where a reader gets the work: its DOI, else its arXiv page, else its URL. */
export function referenceLink(info: RefInfo): string | null {
  const doi = String(info.doi || '').trim().replace(/^(https?:\/\/(dx\.)?doi\.org\/|doi:\s*)/i, '');
  if (/^10\.\d{4,9}\//.test(doi)) return `https://doi.org/${doi}`;
  const arxiv = /^arxiv:\s*(.+)$/i.exec(String(info.archive_id || '').trim());
  if (arxiv) return `https://arxiv.org/abs/${arxiv[1]}`;
  const url = String(info.url || '').trim();
  return /^https?:\/\//i.test(url) ? url : null;
}

/** A piece of a reference entry; ``i``: set in italics. */
export interface RefPart { t: string; i?: boolean }

const clean = (s: unknown) => String(s ?? '').replace(/\s+/g, ' ').trim();
const stop = (s: string) => (/[.?!]$/.test(s) ? s : `${s}.`);
const ordinal = (e: string) => {
  if (!/^\d+$/.test(e)) return e;
  const n = Number(e), t = n % 100;
  return `${n}${t >= 11 && t <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] || 'th'}`;
};

/**
 * A reference entry in author–year style, by item type, with everything needed to find the work:
 *   Smith, A. & Doe, J. (2021). Title. *Journal*, *12*(3), 45–67. https://doi.org/…
 *   Lee, J. (2019). *Book title* (2nd ed.). Publisher, Place.
 *   Roe, R. (2020). Chapter. In *Book title* (pp. 1–20). Publisher.
 * The link (referenceLink) is not among the parts: the caller shows it as a link.
 */
export function referenceParts(key: string, info: RefInfo): RefPart[] {
  const out: RefPart[] = [];
  const add = (t: string, i = false) => { if (t) out.push(i ? { t, i } : { t }); };
  const authors = formatAuthors(info.authors);
  const title = clean(info.title).replace(/\.$/, '') || `@${key}`;
  const container = clean(info.journal).replace(/\.$/, '');
  const publisher = [clean(info.publisher), clean(info.place)].filter(Boolean).join(', ');
  const pages = clean(info.pages).replace(/\s*-+\s*/g, '–');
  const type = info.item_type || 'journalArticle';
  add(`${authors ? `${authors} ` : ''}(${info.year ? clean(info.year) : 'n.d.'}). `);
  if (type === 'book' || type === 'thesis' || type === 'report' || type === 'dataset') {
    // a work of its own: its title in italics, then who published it
    const notes = [
      info.edition ? `${ordinal(clean(info.edition))} ed.` : '',
      type === 'book' && info.volume ? `Vol. ${clean(info.volume)}` : '',
      type !== 'book' ? clean(info.series) : '',
    ].filter(Boolean);
    add(title, true);
    add(`${notes.length ? ` (${notes.join(', ')})` : ''}${type === 'thesis' ? ' [Thesis]' : ''}${type === 'dataset' ? ' [Data set]' : ''}. `);
    if (type === 'book' && info.series) add(`${stop(clean(info.series))} `);
    add(stop(publisher || (type === 'report' ? container : '') || clean(info.archive_id)).replace(/^\.$/, ''));
  } else if (type === 'bookSection' || type === 'conferencePaper') {
    // a part of a larger work: "In *Book* (pp. 1–20). Publisher."
    add(`${stop(title)} `);
    if (container) { add('In '); add(container, true); add(`${pages ? ` (pp. ${pages})` : ''}. `); }
    else if (pages) add(`(pp. ${pages}). `);
    add(publisher ? stop(publisher) : '');
  } else {
    // an article, preprint, web page or anything else: "Title. *Journal*, *12*(3), 45–67."
    add(stop(title));
    if (container) {
      add(' '); add(container, true);
      if (info.volume) { add(', '); add(clean(info.volume), true); }
      if (info.issue) add(`(${clean(info.issue)})`);
      if (pages) add(`, ${pages}`);
      if (type === 'preprint' && info.archive_id && !container.toLowerCase().includes(clean(info.archive_id).toLowerCase())) add(`, ${clean(info.archive_id)}`);
      add('.');
    } else if (type === 'preprint' && info.archive_id) add(` ${stop(clean(info.archive_id))}`);
    else if (publisher) add(` ${stop(publisher)}`);
  }
  // the separators were written ahead of what might follow: drop a trailing space
  while (out.length && !out[out.length - 1].i && !out[out.length - 1].t.trim()) out.pop();
  if (out.length && !out[out.length - 1].i) out[out.length - 1].t = out[out.length - 1].t.replace(/\s+$/, '');
  return out;
}

// emphasis, code and HTML would change the text; brackets alone do not make a link
const mdEscape = (s: string) => s.replace(/([\\*_<>`])/g, '\\$1');

/** A reference entry in Markdown: "Smith, A. (2021). Title. *Journal*, *12*(3), 4–9. <https://doi.org/…>" */
export function referenceMarkdown(key: string, info: RefInfo): string {
  const text = referenceParts(key, info).map((p) => (p.i ? `*${mdEscape(p.t)}*` : mdEscape(p.t))).join('');
  const link = referenceLink(info);
  return link ? `${text} <${link}>` : text;
}

const ESC: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ESC[c]);

/** A reference entry as HTML, its DOI (or other link) clickable. */
export function referenceHtml(key: string, info: RefInfo): string {
  const text = referenceParts(key, info).map((p) => (p.i ? `<i>${esc(p.t)}</i>` : esc(p.t))).join('');
  const link = referenceLink(info);
  return link ? `${text} <a class="ref-link" href="${esc(link)}">${esc(link)}</a>` : text;
}

/** The id of a key's entry in the References list (an HTML id, and a URL fragment, for any key). */
export const referenceId = (key: string) => `ref-${key.replace(/[^A-Za-z0-9_-]/g, (c) => `_${c.codePointAt(0)!.toString(16)}`)}`;

/** The cited keys that are in the library, in order, without repeats. */
export function referenceKeys(keys: string[], citeInfo: (key: string) => RefInfo | null): string[] {
  return [...new Set(keys)].filter((k) => citeInfo(k));
}

/** A note's body as portable Markdown: the front matter dropped, [[links]] as their text, ![[embeds]]
 *  replaced by what they embed, citations as "(Smith et al. 2021, p. 4)". */
export function portableBody(text: string, opts: PortableOptions): Portable {
  text = text.replace(/\r\n?/g, '\n');
  const { links } = scan(text);
  const keys: string[] = [];
  let out = '', at = splitFrontmatter(text).bodyStart;
  for (let i = 0; i < links.length; i++) {
    const l = links[i];
    if (l.from < at) continue;   // another citation of a group already written
    out += text.slice(at, l.from);
    if (l.kind === 'cite') {
      const group = links.filter((c) => c.kind === 'cite' && c.from === l.from);
      out += `(${group.map((c) => {
        keys.push(c.target);
        return citeLabel({ key: c.target, prefix: c.prefix, suppress: c.suppress_author, locator: c.locator }, opts.citeInfo(c.target));
      }).join('; ')})`;
    } else if (l.kind === 'note') {
      out += l.alias || (l.target ? l.target + (l.heading ? ` › ${l.heading}` : '') : l.heading);
    } else if (l.kind === 'embed') {
      const inner = opts.embed?.(l.target, l.heading);
      if (inner) { out += inner.markdown.trim(); keys.push(...inner.keys); }
      else out += `*${l.alias || l.target || l.heading}*`;
    } else {
      // an image: "alt|300" sets the width in the app; elsewhere it would show as part of the text
      const src = text.slice(l.from, l.to);
      out += src.replace(/^!\[([^\]]*)\]/, (_m, alt: string) => `![${alt.split('|')[0].trim()}]`);
    }
    at = l.to;
  }
  return { markdown: portableFigures(out + text.slice(at)), keys };
}

/**
 * Figures (figures.ts), after their citations were written out: a gallery becomes its images on one
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
  // equations win a name both use, as in the app
  const inMath = new Set(spans.filter((sp) => sp.kind === 'math' || sp.kind === 'mathblock')
    .flatMap((sp) => [...md.slice(sp.from, sp.to).matchAll(/\\label\s*\{([^{}]*)\}/g)].map((m) => m[1].trim())));
  for (const r of textRefs(md).reverse()) {
    const n = inMath.has(r.name) ? null : labels.get(r.name);
    if (n == null || spans.some((sp) => sp.kind !== 'gallery' && r.from >= sp.from && r.from < sp.to)) continue;
    md = md.slice(0, r.from) + (r.kind === 'eqref' ? `(${n})` : n) + md.slice(r.to);
  }
  return md;
}

/** The whole export: a title (unless the note starts with one), the body (portableBody) and the
 *  references, from ``refInfo`` (the full library items when they could be loaded). */
export function portableMarkdown(title: string, note: Portable, refInfo: (key: string) => RefInfo | null): string {
  let body = note.markdown.replace(/^\s+/, '').replace(/\s+$/, '');
  if (!/^# /.test(body) && title) body = `# ${title}\n\n${body}`;
  const refs = referenceKeys(note.keys, refInfo);
  if (refs.length) body += `\n\n## References\n\n${refs.map((k, i) => `${i + 1}. ${referenceMarkdown(k, refInfo(k)!)}`).join('\n')}`;
  return `${body}\n`;
}
