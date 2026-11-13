// A LaTeX project for pdfLaTeX from the host's Markdown tokens. No renderer HTML or library data
// is rewritten. The result reads as a LaTeX note rather than a copy of the app: the note's title
// heading becomes \title (with \author and \date, the export date, for \maketitle), captioned figures and tables float, a gallery is one figure with
// panels (a), (b), … under one caption, and citations are \cite with a numbered bibliography.
import type { Token } from 'markdown-it';
import { parseDefinitions } from '../../../frontend/src/plugins/markdown.js';
import { displayMath, mapLabels, mathPreamble } from './texMath.js';
import { mathUnicode, texEscape, unicodePreamble } from './texText.js';

export { texEscape } from './texText.js';

export interface SourceNote { path: string; title: string; text: string }
export interface LatexInputs {
  /** The physics package, the macros of Settings and equation numbering ('labelled' or 'all'). */
  math?: { physics: boolean; macros: string; numbering?: string };
  /** Images per row in a gallery's figure; 0 or absent: up to three, as in the HTML export. */
  galleryColumns?: number;
  /** The author and export date under the title (\author, \date); without it both stay empty. */
  header?: { author: string; date: string };
  parse(text: string): Token[];
  body(text: string): string;
  section(text: string, heading: string): string;
  resolve(target: string): string | null;
  read(path: string): Promise<SourceNote>;
  image(source: string): Promise<{ bytes: Uint8Array; extension: string } | null>;
  bibliography(keys: string[]): Promise<{ text: string; missing: string[] }>;
}
export interface LatexProject { files: Map<string, string | Uint8Array>; warnings: string[] }

/** What a token list is converted in: its note, label scope and heading levels. */
interface Context {
  path: string;
  scope: string;
  seen: Set<string>;
  depth: number;
  /** The sectioning command (index in COMMANDS) of a Markdown heading level. */
  command(level: number): number;
  title?: Token | null;
  /** In a caption, heading or footnote: no verbatim URLs. */
  moving?: boolean;
  heading?: boolean;
}

const COMMANDS = ['section', 'subsection', 'subsubsection', 'paragraph', 'subparagraph'];
// The width of the note's text column in the app: ![|350](…) there is half of \linewidth here.
const NOTE_WIDTH = 700;
const widthOf = (px: number | null | undefined) => (px ? Math.min(1, px / NOTE_WIDTH) : 1);
const size = (width: number, height: number) =>
  `width=${width >= 0.9995 ? '' : width.toFixed(3)}\\linewidth,height=${height.toFixed(3)}\\textheight,keepaspectratio`;
// As the HTML export: all panels up to three in a row, a 2×2 grid for four, else rows of three.
const autoColumns = (n: number) => (n <= 3 ? n : n === 4 ? 2 : 3);
const panelLabel = (i: number) => (i < 26 ? String.fromCharCode(97 + i) : String(i + 1));
const endsSentence = (text: string) => /[.!?…][\s"'”’)\]*_$]*$/.test(text.trim());
// Hex identifiers avoid collisions and TeX-special characters in labels.
const identifier = (text: string) => [...new TextEncoder().encode(text)].map(n => n.toString(16).padStart(2, '0')).join('');
const norm = (text: string) => text.replace(/\\/g, '/').replace(/\.md$/i, '').normalize('NFC').toLowerCase().trim();
const headingId = (text: string) => text.toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').trim().replace(/\s+/g, '-');
/** Tabs as the app shows code: to the next multiple of four columns. */
const expandTabs = (line: string) => { let out = ''; for (const c of line) out += c === '\t' ? ' '.repeat(4 - (out.length % 4)) : c; return out; };
/** A formula as plain text for PDF bookmarks. */
const plainMath = (tex: string) => texEscape(tex.replace(/\\[A-Za-z]+\*?/g, ' ').replace(/[{}^_\\]/g, '').replace(/\s+/g, ' ').trim());

export async function latexProject(root: SourceNote, linked: boolean, input: LatexInputs): Promise<LatexProject> {
  const files = new Map<string, string | Uint8Array>();
  const warnings = new Set<string>();
  const notes = new Map<string, SourceNote>([[root.path, root]]);
  const tokens = new Map<string, Token[]>();
  const keys = new Set<string>();
  const images = new Map<string, string>();
  const footnotes = new Map<string, Map<number, Token[]>>();
  const prefixes = new Map<string, string>([[root.path, '']]);
  const aliases = new Map<string, Map<string, string>>();
  const anchors = new Set<string>();
  const displays = new WeakMap<Token, string>();
  const numberAll = input.math?.numbering === 'all';
  let mathSource = input.math?.macros || '';
  let embedCount = 0, enumerateDepth = 0;
  const noteFile = (path: string) => `notes/note-${[...notes.keys()].indexOf(path)}.tex`;
  const targetPath = (target: string, current: string) => (target ? input.resolve(target) : current);
  const walk = function* (ts: Token[]): Generator<Token> { for (const t of ts) { yield t; if (t.children) yield* walk(t.children); } };
  const parse = (text: string) => input.parse(input.body(text).replace(/^( {0,3}#{1,6})\*([ \t]+)/gm, '$1$2'));

  // Labels keep the note's names where TeX allows them; embedded and linked notes get a prefix of
  // their own, and "+" (never in a kept name) marks generated ones, so nothing collides.
  const prefix = (scope: string) => {
    let p = prefixes.get(scope);
    if (p == null) { p = `n${prefixes.size}+`; prefixes.set(scope, p); }
    return p;
  };
  const texName = (name: string) => (/^[A-Za-z0-9:._-]+$/.test(name) ? name : `x+${identifier(name)}`);
  const label = (scope: string, name: string) => prefix(scope) + texName(aliases.get(scope)?.get(name) ?? name);
  const anchor = (scope: string, heading = '') => prefix(scope) + (heading ? `h+${texName(heading)}` : 'note+');
  const labels = (scope: string, names: string[] = []) => names.map(n => `\\label{${label(scope, n)}}`).join('');

  // Traverse links found in the current text, including an unsaved root; never revisit cycles.
  for (const note of notes.values()) {
    const ts = parse(note.text);
    tokens.set(note.path, ts);
    if (!linked) break;
    for (const t of walk(ts)) {
      if (t.type !== 'note_link' && t.type !== 'note_embed') continue;
      const path = targetPath(t.meta.target, note.path);
      if (!path || notes.has(path)) continue;
      if (notes.size >= 500) throw new Error('This project links more than 500 notes. Export a smaller group.');
      notes.set(path, await input.read(path));
    }
  }

  async function graphic(source: string, alt: string, options: string): Promise<string> {
    let file = images.get(source);
    if (!file) {
      const asset = await input.image(source);
      if (!asset) { warnings.add(`Image unavailable offline: ${source}`); return `\\textit{${texEscape(alt || source)}}`; }
      file = `figures/figure-${images.size + 1}.${asset.extension}`;
      images.set(source, file); files.set(file, asset.bytes);
    }
    return `\\includegraphics[${options}]{${file}}`;
  }

  function definitions(text: string): { commands: string; rest: string } {
    const parsed = parseDefinitions(text);
    const commands = parsed.defs.map(d => {
      const args = d.nargs ? `[${d.nargs}]${d.opt == null ? '' : `[${d.opt}]`}` : '';
      return d.provide ? `\\providecommand{${d.name}}${args}{${d.body}}\n`
        : `\\providecommand{${d.name}}${args}{}\n\\renewcommand{${d.name}}${args}{${d.body}}\n`;
    }).join('');
    return { commands, rest: parsed.rest };
  }

  /** The note's title heading, by the app's rule (a first-level heading first, others below it),
   *  and sectioning commands from its top heading level down, starting at COMMANDS[top]. */
  function outline(ts: Token[], top: number, titled: boolean): Pick<Context, 'title' | 'command'> {
    const headings = ts.filter(t => t.type === 'heading_open');
    const title = titled && headings.length > 1 && headings[0].tag === 'h1' && headings.slice(1).some(t => t.tag !== 'h1') ? headings[0] : null;
    const base = headings.filter(t => t !== title).reduce((n, t) => Math.min(n, Number(t.tag.slice(1))), 6);
    return { title, command: level => Math.min(COMMANDS.length - 1, Math.max(0, level - base + top)) };
  }

  /** A note's body, its macro definitions and title (when ``ctx.title`` is found). */
  async function renderNote(ts: Token[], ctx: Context): Promise<{ body: string; commands: string; title: string | null }> {
    let commands = '';
    const renamed = aliases.get(ctx.scope) || new Map<string, string>();
    aliases.set(ctx.scope, renamed);
    for (const t of walk(ts)) if (t.type === 'math_inline' || t.type === 'math_block') {
      const parsed = definitions(t.content); commands += parsed.commands; t.content = parsed.rest;
      mathSource += `${parsed.commands}${parsed.rest}\n`;
      if (t.type === 'math_block' || t.meta?.display) {
        const display = displayMath(parsed.rest, numberAll);
        displays.set(t, display.tex);
        for (const [name, kept] of display.aliases) if (!renamed.has(name)) renamed.set(name, kept);
      }
    }
    const entries = new Map<number, Token[]>();
    for (let i = 0; i < ts.length; i++) if (ts[i].type === 'footnote_open') {
      const start = i + 1; while (i < ts.length && ts[i].type !== 'footnote_close') i++;
      entries.set(ts[start - 1].meta.id, ts.slice(start, i));
    }
    footnotes.set(ctx.scope, entries);
    const title = ctx.title ? await convert(ts[ts.indexOf(ctx.title) + 1].children || [], { ...ctx, moving: true, heading: true }) : null;
    return { body: tidy(await convert(ts, ctx)), commands, title };
  }

  /** Paragraphs separated by one blank line, outside verbatim code. */
  const tidy = (tex: string) => tex.split(/(\\begin\{verbatim\}[\s\S]*?\\end\{verbatim\})/)
    .map((part, i) => (i % 2 ? part : part.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n'))).join('').trim();

  function cite(citations: { key: string; prefix: string; locator: string }[]): string {
    const parts: string[] = [];
    let plain: string[] = [];
    const flush = () => { if (plain.length) parts.push(`\\cite{${plain.join(',')}}`); plain = []; };
    for (const c of citations) {
      keys.add(c.key);
      const locator = c.locator.replace(/(?:^|[\s,])att:\d+(?=$|[\s,])/gi, '').replace(/^[\s,]+|[\s,]+$/g, '');
      if (!c.prefix && !locator) { plain.push(c.key); continue; }
      flush();
      // braces keep a "]" in a note from ending the optional argument
      const note = (text: string) => (text.includes(']') ? `{${text}}` : text);
      const post = note(texEscape(locator).replace(/^(pp?\.)\s+/, '$1~'));
      parts.push(c.prefix ? `\\cite[${note(texEscape(c.prefix))}][${post}]{${c.key}}` : `\\cite[${post}]{${c.key}}`);
    }
    flush();
    return parts.join(', ');
  }

  async function gallery(ts: Token[], start: number, ctx: Context): Promise<{ tex: string; end: number }> {
    const open = ts[start];
    const panels: { meta: { src: string; alt: string; width: number | null; labels: string[] }; caption: Token }[] = [];
    let caption: Token | null = null, end = start;
    for (let i = start + 1; i < ts.length; i++) {
      if (ts[i].type === 'gallery_close') { end = i; break; }
      if (ts[i].type === 'gallery_panel_open') panels.push({ meta: ts[i].meta, caption: ts[i + 1] });
      if (ts[i].type === 'gallery_caption_open') caption = ts[i + 1];
    }
    if (!panels.length) return { tex: '', end };
    const n = panels.length, captioned = !!open.meta.captioned;
    // One caption, the gallery's and then each panel's: "Caption. (a) First. (b) Second."
    const parts: string[] = [];
    const sentence = async (inline: Token) => {
      const tex = (await convert(inline.children || [], { ...ctx, moving: true })).trim();
      return endsSentence(inline.content) ? tex : `${tex}.`;
    };
    if (caption?.content.trim()) parts.push(await sentence(caption));
    for (const [i, panel] of panels.entries()) {
      if (panel.caption.content.trim()) parts.push(`${n > 1 ? `(${panelLabel(i)}) ` : ''}${await sentence(panel.caption)}`);
    }
    let body = '';
    const names = [...open.meta.labels];
    if (n === 1) {
      body = `${await graphic(panels[0].meta.src, panels[0].meta.alt, size(widthOf(panels[0].meta.width ?? open.meta.width), 0.75))}\n`;
      names.push(...panels[0].meta.labels);
    } else {
      const columns = Math.max(1, Math.min(n, Math.round(input.galleryColumns || 0) || autoColumns(n)));
      const rows = Math.ceil(n / columns), gap = 0.02;
      // rounded down with room to spare: a row as wide as the line would wrap its last panel
      const cell = Math.floor(((widthOf(open.meta.width) - gap * (columns - 1)) / columns) * 1000 - 2) / 1000;
      for (const [i, panel] of panels.entries()) {
        if (i) body += i % columns ? `\\hspace{${gap}\\linewidth}%\n` : '\\par\\medskip\n';
        // the panel's letter under its image; its caption is part of the figure's
        body += `\\begin{subfigure}[t]{${cell.toFixed(3)}\\linewidth}\n\\centering\n`
          + `${await graphic(panel.meta.src, panel.meta.alt, size(1, Math.min(0.75, 0.7 / rows)))}\n`
          + `${captioned ? `\\caption{}${labels(ctx.scope, panel.meta.labels)}\n` : ''}\\end{subfigure}%\n`;
      }
    }
    const tex = captioned
      ? `\\begin{figure}[htbp]\n\\centering\n${body}\\caption{${parts.join(' ')}}${labels(ctx.scope, names)}\n\\end{figure}`
      : `\\begin{center}\n${body}\\end{center}`;
    return { tex: `\n${tex}\n\n`, end };
  }

  /** The children of a paragraph wholly in *italics* right below a table: its caption. */
  function tableCaption(ts: Token[], i: number, tableEnd: number): Token[] | null {
    const [open, inline, close] = [ts[i], ts[i + 1], ts[i + 2]];
    if (open?.type !== 'paragraph_open' || inline?.type !== 'inline' || close?.type !== 'paragraph_close') return null;
    if (!open.map || open.map[0] > tableEnd + 1) return null;
    const children = inline.children || [];
    if (children[0]?.type !== 'em_open' || children[children.length - 1]?.type !== 'em_close') return null;
    let depth = 0;
    for (const [k, c] of children.entries()) {
      if (c.type === 'em_open') depth++;
      else if (c.type === 'em_close' && --depth === 0 && k < children.length - 1) return null;
    }
    return children.slice(1, -1);
  }

  async function table(ts: Token[], start: number, ctx: Context): Promise<{ tex: string; end: number }> {
    const rows: { cells: string[]; header: boolean }[] = [];
    const align: string[] = [];
    let end = start;
    for (let i = start + 1; i < ts.length; i++) {
      const t = ts[i];
      if (t.type === 'table_close') { end = i; break; }
      if (t.type === 'tr_open') rows.push({ cells: [], header: false });
      if (t.type !== 'th_open' && t.type !== 'td_open') continue;
      const row = rows[rows.length - 1];
      const style = t.attrGet('style') || '';
      if (rows.length === 1) align.push(/center/.test(style) ? 'c' : /right/.test(style) ? 'r' : 'l');
      const cell = (await convert(ts[i + 1].children || [], { ...ctx, moving: true })).trim();
      row.header = t.type === 'th_open';
      row.cells.push(row.header && cell ? `\\textbf{${cell}}` : cell);
    }
    // Framed like the app's tables; scaled down only when wider than the text.
    const tabular = `\\resizebox{\\ifdim\\width>\\linewidth\\linewidth\\else\\width\\fi}{!}{%\n`
      + `\\begin{tabular}{|${(align.length ? align : ['l']).join('|')}|}\n\\hline\n`
      + rows.map(row => `${row.cells.join(' & ')} \\\\\n\\hline\n`).join('') + '\\end{tabular}}';
    const caption = tableCaption(ts, end + 1, ts[start].map?.[1] ?? Infinity);
    if (!caption) return { tex: `\n\\begin{center}\n${tabular}\n\\end{center}\n\n`, end };
    const names: string[] = [];
    for (const c of caption) if (c.type === 'text') c.content = c.content.replace(/\\label\{([^{}]*)\}/g, (_, name: string) => { names.push(name.trim()); return ''; });
    const text = (await convert(caption, { ...ctx, moving: true })).trim();
    return { tex: `\n\\begin{table}[htbp]\n\\centering\n\\caption{${text}}${labels(ctx.scope, names)}\n${tabular}\n\\end{table}\n\n`, end: end + 3 };
  }

  async function convert(ts: Token[], ctx: Context): Promise<string> {
    let out = '';
    const links: boolean[] = [];
    // a tie keeps a reference on the line of the word before it
    const tie = () => { if (out.endsWith(' ')) out = `${out.slice(0, -1)}~`; };
    for (let index = 0; index < ts.length; index++) {
      const t = ts[index];
      const meta = t.meta || {};
      switch (t.type) {
        case 'inline': out += await convert(t.children || [], ctx); break;
        case 'footnote_block_open': while (index < ts.length && ts[index].type !== 'footnote_block_close') index++; break;
        case 'footnote_ref': {
          const text = await convert(footnotes.get(ctx.scope)?.get(meta.id) || [], { ...ctx, moving: true });
          out += `\\footnote{${tidy(text)}}`; break;
        }
        case 'footnote_anchor': case 'task_box': break;
        case 'toc_open': out += '\\tableofcontents\n\n'; break;
        case 'underline_open': out += '\\uline{'; break;
        case 'underline_close': out += '}'; break;
        case 'text': out += texEscape(t.content); break;
        case 'softbreak': out += '\n'; break;
        case 'hardbreak': out += '\\newline\n'; break;
        case 'paragraph_open': {
          // a paragraph of images alone shows them centred on their own line
          const children = ts[index + 1]?.children || [];
          const pictures = children.filter(c => c.type === 'image');
          if (!pictures.length || !children.every(c => c.type === 'image' || c.type === 'softbreak' || (c.type === 'text' && !c.content.trim()))) break;
          const parts = [];
          for (const c of pictures) {
            const [alt, px] = c.content.split('|');
            parts.push(await graphic(c.attrGet('src') || '', alt, size(Math.min(widthOf(Number(px) || null), 0.98 / pictures.length), 0.75)));
          }
          out += `\n\\begin{center}\n${parts.join('\\hfill\n')}\n\\end{center}\n\n`;
          index += 2; break;
        }
        case 'paragraph_close':
          // a display formula right after a paragraph belongs to it, as in LaTeX (not one that only defines macros)
          out += t.hidden || (ts[index + 1]?.type === 'math_block' && displays.get(ts[index + 1])) ? '\n' : '\n\n'; break;
        case 'heading_open': {
          const inline = ts[index + 1];
          index += 2;
          if (t === ctx.title) break;
          const title = await convert(inline?.children || [], { ...ctx, moving: true, heading: true });
          out += `\n\\${COMMANDS[ctx.command(Number(t.tag.slice(1)))]}{${title.trim()}}`;
          const id = anchor(ctx.scope, headingId(inline?.content || ''));
          if (!anchors.has(id)) { anchors.add(id); out += `\\label{${id}}`; }
          out += `${labels(ctx.scope, meta.sectionLabels)}\n`;
          break;
        }
        case 'em_open': out += '\\emph{'; break;
        case 'strong_open': out += '\\textbf{'; break;
        case 's_open': out += '\\sout{'; break;
        case 'em_close': case 'strong_close': case 's_close': out += '}'; break;
        case 'code_inline': out += `\\texttt{${texEscape(t.content)}}`; break;
        case 'fence': case 'code_block': {
          // tabs as the app shows them; a literal terminator is split so code cannot end its environment
          const code = t.content.replace(/\n$/, '').split('\n').map(expandTabs).join('\n').replace(/\\end\{verbatim\}/g, '\\end{verbatim}\n\\texttt{\\textbackslash{}end\\{verbatim\\}}\n\\begin{verbatim}');
          out += `\n\\begin{verbatim}\n${code}\n\\end{verbatim}\n\n`;
          if (/^mermaid\b/.test(t.info)) warnings.add('Mermaid diagrams are included as source code.');
          break;
        }
        case 'math_block': case 'math_inline': {
          const display = displays.get(t);
          if (display == null) {
            if (!t.content.trim()) break;
            const tex = mapLabels(mathUnicode(t.content), n => label(ctx.scope, n));
            out += ctx.heading ? `\\texorpdfstring{$${tex}$}{${plainMath(t.content)}}` : `$${tex}$`;
            break;
          }
          if (!display) break;
          out += `${out && !out.endsWith('\n') ? '\n' : ''}${mapLabels(mathUnicode(display), n => label(ctx.scope, n))}\n`;
          // text on the next line continues the paragraph, after a blank line it starts a new one
          const next = ts[index + 1];
          if (t.type === 'math_block' && !(next?.type === 'paragraph_open' && next.map && t.map && next.map[0] === t.map[1])) out += '\n';
          break;
        }
        case 'eq_ref':
          if (meta.kind === 'eqref' || meta.kind === 'ref') tie();
          out += `\\${meta.kind}{${(meta.names || [meta.name]).map((n: string) => label(ctx.scope, n)).join(',')}}`; break;
        case 'cite_group': tie(); out += cite(t.meta); break;
        case 'note_link': case 'note_embed': {
          const target = targetPath(meta.target, ctx.path);
          const text = texEscape(meta.alias || meta.heading || meta.target || 'Note');
          if (t.type === 'note_embed' && !linked && target && !ctx.seen.has(target) && ctx.depth < 3) {
            const note = notes.get(target) || await input.read(target);
            const ts = input.parse(input.section(input.body(note.text), meta.heading || ''));
            const embed = await renderNote(ts, { path: target, scope: `${target}#embed-${++embedCount}`, seen: new Set(ctx.seen).add(target), depth: ctx.depth + 1, ...outline(ts, 0, false) });
            out += `\n\\begingroup\n${embed.commands}${embed.body}\n\\endgroup\n`;
          } else if (target && (target === ctx.path || notes.has(target))) {
            out += `\\hyperref[${anchor(target === ctx.path ? ctx.scope : target, meta.heading ? headingId(meta.heading) : '')}]{${text}}`;
          } else {
            out += text;
            if (!target) warnings.add(`Unresolved note: ${meta.target || meta.heading}`);
          }
          break;
        }
        case 'image': {
          const [alt, px] = t.content.split('|');
          out += await graphic(t.attrGet('src') || '', alt, size(widthOf(Number(px) || null), 0.75)); break;
        }
        case 'link_open': {
          const href = t.attrGet('href') || '';
          const safe = /^(https?:|mailto:)/i.test(href) && !/[{}\\%\r\n]/.test(href);
          const text = ts[index + 1];
          // a bare address can break across lines
          if (safe && !ctx.moving && /^[\w:/.?=+@!*'(),;~-]+$/.test(href) && text?.type === 'text' && text.content === href.replace(/^mailto:/i, '') && ts[index + 2]?.type === 'link_close') {
            out += `\\url{${href}}`; index += 2; break;
          }
          links.push(safe); if (safe) out += `\\href{${href.replace(/[\\#]/g, '\\$&')}}{`;
          break;
        }
        case 'link_close': if (links.pop()) out += '}'; break;
        case 'bullet_list_open': out += '\n\\begin{itemize}\n'; break;
        case 'bullet_list_close': out += '\\end{itemize}\n\n'; break;
        case 'ordered_list_open': {
          const start = Number(t.attrGet('start') || 1);
          const counter = ['enumi', 'enumii', 'enumiii', 'enumiv'][enumerateDepth];
          out += `\n\\begin{enumerate}\n${start !== 1 && counter ? `\\setcounter{${counter}}{${start - 1}}\n` : ''}`;
          enumerateDepth++; break;
        }
        case 'ordered_list_close': enumerateDepth--; out += '\\end{enumerate}\n\n'; break;
        case 'list_item_open': {
          const first = ts[index + 2]?.type === 'inline' ? ts[index + 2].children?.[0] : null;
          if (first?.type === 'task_box') out += first.meta?.checked ? '\\item[$\\boxtimes$] ' : '\\item[$\\square$] ';
          else out += first?.type === 'text' && first.content.startsWith('[') ? '\\item{} ' : '\\item ';
          break;
        }
        case 'list_item_close': if (!out.endsWith('\n')) out += '\n'; break;
        case 'blockquote_open': {
          const title = meta.title || (meta.callout ? meta.callout[0].toUpperCase() + meta.callout.slice(1) : '');
          out += `\n\\begin{quote}\n${title ? `\\textbf{${texEscape(title)}}\\par\n` : ''}`; break;
        }
        case 'blockquote_close': out += '\\end{quote}\n\n'; break;
        case 'hr': out += '\n\\begin{center}\n\\rule{0.5\\linewidth}{0.4pt}\n\\end{center}\n\n'; break;
        case 'table_open': { const r = await table(ts, index, ctx); out += r.tex; index = r.end; break; }
        case 'figure_open': {
          const caption = await convert(ts[index + 1]?.children || [], { ...ctx, moving: true });
          out += `\n\\begin{figure}[htbp]\n\\centering\n${await graphic(meta.src, meta.alt, size(widthOf(meta.width), 0.75))}\n`
            + `\\caption{${caption.trim()}}${labels(ctx.scope, meta.labels)}\n\\end{figure}\n\n`;
          index += 2; break;
        }
        case 'gallery_open': { const r = await gallery(ts, index, ctx); out += r.tex; index = r.end; break; }
        case 'task_ref': out += texEscape(`Task ${meta.id}`); warnings.add('Task references are exported as IDs.'); break;
        case 'tag': out += texEscape(`#${t.content}`); break;
        default:
          if (t.children) out += await convert(t.children, ctx);
          else if (!t.nesting && t.content) out += texEscape(t.content);
      }
    }
    return out;
  }

  let body = '', commands = '', title = texEscape(root.title);
  const appendix: string[] = [];
  for (const note of notes.values()) {
    const ts = tokens.get(note.path)!;
    const isRoot = note.path === root.path;
    const ctx: Context = { path: note.path, scope: note.path, seen: new Set([note.path]), depth: 0, ...outline(ts, isRoot ? 0 : 1, true) };
    const result = await renderNote(ts, ctx);
    if (isRoot) {
      commands = result.commands;
      if (result.title) title = result.title;
      body = `\\phantomsection\\label{${anchor(note.path)}}\n${result.body}\n`;
    } else {
      const heading = `\\section{${result.title || texEscape(note.title)}}\\label{${anchor(note.path)}}\n`;
      files.set(noteFile(note.path), `${heading}\\begingroup\n${result.commands}${result.body}\n\\endgroup\n`);
      appendix.push(`\\input{${noteFile(note.path)}}`);
    }
  }
  if (appendix.length) body += `\n\\appendix\n${appendix.join('\n')}\n`;

  const bib = keys.size ? await input.bibliography([...keys]) : { text: '', missing: [] };
  for (const key of bib.missing) warnings.add(`Reference not in the library: ${key}`);
  if (keys.size) files.set('references.bib', bib.text);
  const texts = [...files.values()].filter((v): v is string => typeof v === 'string');
  const unicode = unicodePreamble([body, commands, title, input.header?.author || '', input.math?.macros || '', bib.text, ...texts]);
  if (unicode.missing.length) warnings.add(`Characters pdfLaTeX cannot typeset are shown as their code point: ${unicode.missing.join(' ')}`);
  const preamble = [
    '% Synapse LaTeX export. Build with: latexmk -pdf main.tex',
    '% (or pdflatex, biber, pdflatex, pdflatex).',
    '\\documentclass[11pt]{article}',
    '\\usepackage[T1]{fontenc}',
    '\\usepackage{lmodern}',
    '\\usepackage[a4paper,margin=2.5cm]{geometry}',
    '\\usepackage{microtype}',
    '\\usepackage{mathtools,amssymb}',
    ...(input.math?.physics ? ['\\usepackage{physics}'] : []),
    '\\usepackage{graphicx}',
    '\\usepackage{xcolor}',
    '\\usepackage[font=small,labelfont=bf,tableposition=top]{caption}',
    '\\usepackage[labelformat=simple]{subcaption}',
    '\\renewcommand{\\thesubfigure}{(\\alph{subfigure})}',
    '\\usepackage[normalem]{ulem}',
    ...(keys.size ? ['\\usepackage[backend=biber,style=numeric-comp,sorting=none]{biblatex}', '\\addbibresource{references.bib}'] : []),
    '\\usepackage[colorlinks,linkcolor=blue!50!black,citecolor=blue!50!black,urlcolor=blue!50!black]{hyperref}',
    '\\usepackage{bookmark}',
    '\\usepackage[capitalise,nameinlink]{cleveref}',
    '\\extrafloats{100}',
  ].join('\n') + '\n' + mathPreamble(mathSource) + unicode.preamble + definitions(input.math?.macros || '').commands + commands;
  files.set('main.tex', `${preamble}\n\\title{${title}}\n\\author{${texEscape(input.header?.author || '')}}\n\\date{${texEscape(input.header?.date || '')}}\n\n\\begin{document}\n\\maketitle\n\n${body}${keys.size ? '\n\\printbibliography\n' : ''}\\end{document}\n`);
  const columns = Math.round(input.galleryColumns || 0);
  files.set('README.txt', 'Synapse LaTeX export\n\nBuild the PDF with pdfLaTeX and Biber:\n\n    latexmk -pdf main.tex\n\n'
    + 'or run pdflatex, biber, pdflatex, pdflatex on main.tex.\nKeep figures/, notes/ and references.bib beside main.tex.\n'
    + `Galleries: ${columns > 0 ? `${columns} ${columns === 1 ? 'image' : 'images'} per row` : 'up to three images per row'}.\n`
    + (warnings.size ? `\nExport notes:\n${[...warnings].map(w => `- ${w}`).join('\n')}\n` : ''));
  // the main file comes first in the archive
  const ordered = new Map([...files].sort(([a], [b]) => Number(b === 'main.tex') - Number(a === 'main.tex')));
  return { files: ordered, warnings: [...warnings] };
}

/** Resolve names with the vault index's shortest-path preference. */
export function noteResolver(paths: string[]): (target: string) => string | null {
  const ordered = [...paths].sort((a, b) => a.split('/').length - b.split('/').length || a.length - b.length || (a < b ? -1 : a > b ? 1 : 0));
  return target => {
    const name = norm(target);
    return ordered.find(path => name.includes('/') ? norm(path) === name || norm(path).endsWith(`/${name}`) : norm(path.split('/').pop()!) === name) || null;
  };
}
