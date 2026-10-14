// A note as other programs can show it (exportNote.ts): Markdown without the notes' own syntax,
// citations written out with a list of references, and the reference entries HTML and PDF
// exports use. No DOM (tests/js/portable.test.mjs).
import { scan, splitFrontmatter } from './mdscan.js';
import { type CiteInfo, citeLabel } from './render.js';

/** What a reference entry is made from (any CiteEntry fits). */
export interface RefInfo extends CiteInfo { journal?: string | null }

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

/** The parts of a reference entry: Authors (Year). Title. Journal. */
export function referenceParts(key: string, info: RefInfo): { authors: string; year: string; title: string; journal: string } {
  return {
    authors: formatAuthors(info.authors),
    year: info.year ? String(info.year) : 'n.d.',
    title: String(info.title || `@${key}`).trim().replace(/\.$/, ''),
    journal: String(info.journal || '').trim().replace(/\.$/, ''),
  };
}

/** A reference entry in Markdown: "Smith, A. (2021). Title. *Journal*." */
export function referenceMarkdown(key: string, info: RefInfo): string {
  const r = referenceParts(key, info);
  return `${r.authors ? `${r.authors} ` : ''}(${r.year}). ${r.title}.${r.journal ? ` *${r.journal}*.` : ''}`;
}

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
  return { markdown: out + text.slice(at), keys };
}

/** The whole export: a title (unless the note starts with one), the body and its references. */
export function portableMarkdown(title: string, text: string, opts: PortableOptions): string {
  const { markdown, keys } = portableBody(text, opts);
  let body = markdown.replace(/^\s+/, '').replace(/\s+$/, '');
  if (!/^# /.test(body) && title) body = `# ${title}\n\n${body}`;
  const refs = referenceKeys(keys, opts.citeInfo);
  if (refs.length) body += `\n\n## References\n\n${refs.map((k, i) => `${i + 1}. ${referenceMarkdown(k, opts.citeInfo(k)!)}`).join('\n')}`;
  return `${body}\n`;
}
