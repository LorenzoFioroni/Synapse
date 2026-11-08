// Portable LaTeX from the host's Markdown tokens. No renderer HTML or library data is rewritten.
import type { Token } from 'markdown-it';
import { parseDefinitions } from '../../../frontend/src/plugins/markdown.js';

export interface SourceNote { path: string; title: string; text: string }
export interface LatexInputs {
  math?: { physics: boolean; macros: string };
  parse(text: string): Token[];
  body(text: string): string;
  section(text: string, heading: string): string;
  resolve(target: string): string | null;
  read(path: string): Promise<SourceNote>;
  image(source: string): Promise<{ bytes: Uint8Array; extension: string } | null>;
  bibliography(keys: string[]): Promise<{ text: string; missing: string[] }>;
}
export interface LatexProject { files: Map<string, string | Uint8Array>; warnings: string[] }

export function texEscape(text: string): string {
  const escapes: Record<string, string> = { '\\': '\\textbackslash{}', '{': '\\{', '}': '\\}', '#': '\\#', '$': '\\$', '%': '\\%', '&': '\\&', '_': '\\_', '^': '\\textasciicircum{}', '~': '\\textasciitilde{}' };
  return text.replace(/[\\{}#$%&_^~]/g, c => escapes[c]);
}

// Hex identifiers avoid collisions and TeX-special characters in paths and labels.
const identifier = (text: string) => [...new TextEncoder().encode(text)].map(n => n.toString(16).padStart(2, '0')).join('');
const norm = (text: string) => text.replace(/\\/g, '/').replace(/\.md$/i, '').normalize('NFC').toLowerCase().trim();
const headingId = (text: string) => text.toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').trim().replace(/\s+/g, '-');

export async function latexProject(root: SourceNote, linked: boolean, input: LatexInputs): Promise<LatexProject> {
  const files = new Map<string, string | Uint8Array>();
  const warnings = new Set<string>();
  const notes = new Map<string, SourceNote>([[root.path, root]]);
  const tokens = new Map<string, Token[]>();
  const keys = new Set<string>();
  const images = new Map<string, string>();
  let embedCount = 0;
  const footnotes = new Map<string, Map<number, Token[]>>();
  const label = (path: string, name = '') => `n${identifier(path)}-${identifier(name)}`;
  const noteFile = (path: string) => `notes/note-${[...notes.keys()].indexOf(path)}.tex`;
  const targetPath = (target: string, current: string) => target ? input.resolve(target) : current;
  const walk = function* (ts: Token[]): Generator<Token> { for (const t of ts) { yield t; if (t.children) yield* walk(t.children); } };
  const parse = (text: string) => input.parse(input.body(text).replace(/^( {0,3}#{1,6})\*([ \t]+)/gm, '$1$2'));

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

  async function image(source: string, alt = ''): Promise<string> {
    let file = images.get(source);
    if (!file) {
      const asset = await input.image(source);
      if (!asset) { warnings.add(`Image unavailable offline: ${source}`); return `\\textit{${texEscape(alt || source)}}`; }
      file = `figures/figure-${images.size + 1}.${asset.extension}`;
      images.set(source, file); files.set(file, asset.bytes);
    }
    return `\\includegraphics[width=\\linewidth,height=0.7\\textheight,keepaspectratio]{${file}}`;
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

  async function renderNote(ts: Token[], path: string, seen: Set<string>, depth = 0, scope = path): Promise<string> {
    let commands = '';
    for (const t of walk(ts)) if (t.type === 'math_inline' || t.type === 'math_block') {
      const parsed = definitions(t.content); commands += parsed.commands; t.content = parsed.rest;
    }
    const entries = new Map<number, Token[]>();
    for (let i = 0; i < ts.length; i++) if (ts[i].type === 'footnote_open') {
      const start = i + 1; while (i < ts.length && ts[i].type !== 'footnote_close') i++;
      entries.set(ts[start - 1].meta.id, ts.slice(start, i));
    }
    footnotes.set(scope, entries);
    return `\\begingroup\n${commands}${await convert(ts, path, seen, depth, scope)}\n\\endgroup\n`;
  }

  async function convert(ts: Token[], path: string, seen: Set<string>, depth = 0, scope = path): Promise<string> {
    let out = '';
    const links: boolean[] = [];
    let cells = 0;
    for (let index = 0; index < ts.length; index++) {
      const t = ts[index];
      const meta = t.meta || {};
      switch (t.type) {
        case 'inline': out += await convert(t.children || [], path, seen, depth, scope); break;
        case 'footnote_block_open': while (index < ts.length && ts[index].type !== 'footnote_block_close') index++; break;
        case 'footnote_ref': out += `\\footnote{${await convert(footnotes.get(scope)?.get(meta.id) || [], path, seen, depth, scope)}}`; break;
        case 'footnote_anchor': break;
        case 'toc_open': out += '\\tableofcontents\n'; break;
        case 'underline_open': out += '\\uline{'; break;
        case 'underline_close': out += '}'; break;
        case 'task_box': out += meta.checked ? '[x] ' : '[ ] '; break;
        case 'text': case 'html_inline': case 'html_block': out += texEscape(t.content); break;
        case 'softbreak': out += '\n'; break;
        case 'hardbreak': out += '\\\\\n'; break;
        case 'paragraph_open': break;
        case 'paragraph_close': out += '\n\n'; break;
        case 'heading_open': {
          const next = ts[index + 1];
          const title = await convert(next?.children || [], path, seen, depth, scope);
          const command = ['section', 'subsection', 'subsubsection', 'paragraph', 'subparagraph', 'subparagraph'][Number(t.tag.slice(1)) - 1];
          out += `\n\\${command}{${title}}\\label{${label(scope, headingId(next?.content || ''))}}\n`;
          for (const name of meta.sectionLabels || []) out += `\\label{${label(scope, name)}}`;
          index += 2; break;
        }
        case 'em_open': out += '\\emph{'; break;
        case 'strong_open': out += '\\textbf{'; break;
        case 's_open': out += '\\sout{'; break;
        case 'em_close': case 'strong_close': case 's_close': out += '}'; break;
        case 'code_inline': out += `\\texttt{${texEscape(t.content)}}`; break;
        case 'fence': case 'code_block': {
          // Split a literal terminator so code cannot accidentally end its environment.
          const code = t.content.replace(/\\end\{verbatim\}/g, '\\end{verbatim}\n\\texttt{\\textbackslash{}end\\{verbatim\\}}\n\\begin{verbatim}');
          out += `\n\\begin{verbatim}\n${code}\n\\end{verbatim}\n`;
          if (/^mermaid\b/.test(t.info)) warnings.add('Mermaid diagrams are included as source code.');
          break;
        }
        case 'math_inline': case 'math_block': {
          if (!t.content.trim()) break;
          const tex = t.content.replace(/\\(label|eqref|ref|[cC]ref)\{([^{}]+)\}/g, (_, cmd: string, names: string) => `\\${cmd}{${names.split(',').map(n => label(scope, n.trim())).join(',')}}`);
          out += t.type === 'math_block' || meta.display ? `\n\\begin{equation}\n${tex}\n\\end{equation}\n` : `$${tex}$`;
          break;
        }
        case 'eq_ref': out += `\\${meta.kind}{${(meta.names || [meta.name]).map((n: string) => label(scope, n)).join(',')}}`; break;
        case 'cite_group': {
          const citations = t.meta as { key: string; prefix: string; locator: string; suppress: boolean }[];
          out += citations.map(c => {
            keys.add(c.key);
            const locator = c.locator.replace(/(?:^|[\s,])att:\d+(?=$|[\s,])/gi, '').replace(/^[\s,]+|[\s,]+$/g, '');
            const options = c.prefix || locator ? `[${texEscape(c.prefix)}][${texEscape(locator)}]` : '';
            return `\\${c.suppress ? 'autocite*' : 'autocite'}${options}{${c.key}}`;
          }).join('; ');
          break;
        }
        case 'note_link': case 'note_embed': {
          const target = targetPath(meta.target, path);
          const text = texEscape(meta.alias || meta.heading || meta.target || 'Note');
          if (t.type === 'note_embed' && !linked && target && !seen.has(target) && depth < 3) {
            const note = notes.get(target) || await input.read(target);
            const text = input.section(input.body(note.text), meta.heading || '');
            out += await renderNote(input.parse(text), target, new Set(seen).add(target), depth + 1, `${target}#embed-${++embedCount}`);
          } else if (target && (target === path || notes.has(target))) {
            out += `\\hyperref[${label(target === path ? scope : target, meta.heading ? headingId(meta.heading) : '')}]{${text}}`;
          } else {
            out += text;
            if (!target) warnings.add(`Unresolved note: ${meta.target || meta.heading}`);
          }
          break;
        }
        case 'image': out += await image(t.attrGet('src') || '', t.content.split('|')[0]); break;
        case 'link_open': {
          const href = t.attrGet('href') || '';
          const safe = /^(https?:|mailto:)/i.test(href) && !/[{}\\%\r\n]/.test(href);
          links.push(safe); if (safe) out += `\\href{${href.replace(/#/g, '\\#')}}{`;
          break;
        }
        case 'link_close': if (links.pop()) out += '}'; break;
        case 'bullet_list_open': out += '\n\\begin{itemize}\n'; break;
        case 'bullet_list_close': out += '\\end{itemize}\n'; break;
        case 'ordered_list_open': out += '\n\\begin{enumerate}\n'; break;
        case 'ordered_list_close': out += '\\end{enumerate}\n'; break;
        case 'list_item_open': out += '\\item '; break;
        case 'list_item_close': out += '\n'; break;
        case 'blockquote_open': out += '\n\\begin{quote}\n' + (meta.title ? `\\textbf{${texEscape(meta.title)}}\n` : ''); break;
        case 'blockquote_close': out += '\\end{quote}\n'; break;
        case 'hr': out += '\n\\par\\noindent\\rule{\\linewidth}{0.4pt}\n'; break;
        case 'table_open': {
          let columns = 0;
          for (let i = index + 1; i < ts.length && ts[i].type !== 'tr_close'; i++) if (ts[i].type === 'th_open') columns++;
          out += `\n\\begin{tabular}{${'l'.repeat(Math.max(1, columns))}}\n`; break;
        }
        case 'table_close': out += '\\end{tabular}\n'; break;
        case 'tr_open': cells = 0; break;
        case 'th_open': case 'td_open': if (cells++) out += ' & '; break;
        case 'tr_close': out += ' \\\\\n'; break;
        case 'figure_open':
          out += `\n\\begin{figure}[htbp]\n\\centering\n${await image(meta.src, meta.alt)}\n\\caption{`;
          break;
        case 'figure_close': {
          const open = [...ts.slice(0, index)].reverse().find(t => t.type === 'figure_open');
          out += `}${(open?.meta.labels || []).map((n: string) => `\\label{${label(scope, n)}}`).join('')}\n\\end{figure}\n`; break;
        }
        case 'gallery_open': out += '\n\\begin{figure}[htbp]\n\\centering\n'; break;
        case 'gallery_panel_open':
          out += `\\begin{subfigure}{${Math.min(0.95, 0.95 / Math.max(1, meta.count)).toFixed(3)}\\linewidth}\n${await image(meta.src, meta.alt)}\n\\caption{`;
          break;
        case 'gallery_panel_close': {
          const open = [...ts.slice(0, index)].reverse().find(t => t.type === 'gallery_panel_open');
          out += `}${(open?.meta.labels || []).map((n: string) => `\\label{${label(scope, n)}}`).join('')}\n\\end{subfigure}\n`; break;
        }
        case 'gallery_caption_open': out += '\\caption{'; break;
        case 'gallery_caption_close': out += '}\n'; break;
        case 'gallery_close': {
          const open = [...ts.slice(0, index)].reverse().find(t => t.type === 'gallery_open');
          out += `${(open?.meta.labels || []).map((n: string) => `\\label{${label(scope, n)}}`).join('')}\n\\end{figure}\n`; break;
        }
        case 'task_ref': out += texEscape(`Task ${meta.id}`); warnings.add('Task references are exported as IDs.'); break;
        case 'tag': out += texEscape(`#${t.content}`); break;
        default:
          if (t.children) out += await convert(t.children, path, seen, depth, scope);
          else if (!t.nesting && t.content) out += texEscape(t.content);
      }
    }
    return out;
  }

  let body = '';
  for (const note of notes.values()) {
    const text = `\\phantomsection\\label{${label(note.path)}}\n${note.path === root.path ? '' : `\\section*{${texEscape(note.title)}}\n`}${await renderNote(tokens.get(note.path)!, note.path, new Set([note.path]))}`;
    if (note.path === root.path) body = text;
    else { files.set(noteFile(note.path), text); body += `\n\\input{${noteFile(note.path)}}\n`; }
  }
  const bib = await input.bibliography([...keys]);
  for (const key of bib.missing) warnings.add(`Reference not in the library: ${key}`);
  files.set('references.bib', bib.text);
  files.set('main.tex', String.raw`% Compile with LuaLaTeX, Biber, LuaLaTeX, LuaLaTeX.
\documentclass{article}
\usepackage{fontspec}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{subcaption}
\usepackage[normalem]{ulem}
\usepackage{hyperref}
\usepackage{cleveref}
\usepackage[backend=biber,style=authoryear]{biblatex}
\addbibresource{references.bib}
` + (input.math?.physics ? '\\usepackage{physics}\n' : '') + definitions(input.math?.macros || '').commands + `\\title{${texEscape(root.title)}}\n\\date{}\n\\begin{document}\n\\maketitle\n${body}\n\\printbibliography\n\\end{document}\n`);
  files.set('README.txt', 'Synapse LaTeX export\n\nCompile main.tex with LuaLaTeX, Biber, LuaLaTeX, LuaLaTeX.\nKeep figures/, notes/ and references.bib beside main.tex.\n'
    + (warnings.size ? `\nExport notes:\n${[...warnings].map(w => `- ${w}`).join('\n')}\n` : ''));
  return { files, warnings: [...warnings] };
}

/** Resolve names with the vault index's shortest-path preference. */
export function noteResolver(paths: string[]): (target: string) => string | null {
  const ordered = [...paths].sort((a, b) => a.split('/').length - b.split('/').length || a.length - b.length || (a < b ? -1 : a > b ? 1 : 0));
  return target => {
    const name = norm(target);
    return ordered.find(path => name.includes('/') ? norm(path) === name || norm(path).endsWith(`/${name}`) : norm(path.split('/').pop()!) === name) || null;
  };
}
