// Exporting a note so it can be read outside the app: as a PDF (through the browser's print
// dialog, "Save as PDF"), as one self-contained HTML file, or as portable Markdown (portable.ts).
// HTML and PDF show the note as read mode does, in the light theme. Citations become text with a
// list of references (each citation links to its entry in HTML and PDF, and each entry to the
// work's DOI), [[links]] become text, and embeds, formulas, diagrams and images are included. Everything is built here from files the app already serves, so it works offline.
import { api } from '../../api.js';
import type { Item, NoteLinks } from '../../types.js';
import { openMenu } from '../../ui/menu.js';
import { toast, toastError } from '../../ui/toast.js';
import { downloadBlob, escapeHtml as esc } from '../../util.js';
import { loadMD } from './editor.js';
import { embedHtml, section } from './embed.js';
import { linkDataOf } from './linkData.js';
import { splitFrontmatter } from './mdscan.js';
import { type Portable, type RefInfo, portableBody, portableMarkdown, referenceHtml, referenceId, referenceKeys } from './portable.js';
import type { RendererOptions } from './render.js';
import { highlightCodeBlocks, renderEmbeds, renderMermaidBlocks } from './richRender.js';
import { equationMode, noteMacros, normTarget, stemOf, taskHooks, ws } from './workspace.js';

export type ExportFormat = 'pdf' | 'html' | 'md';

const FORMATS: { format: ExportFormat; label: string; icon: string }[] = [
  { format: 'pdf', label: 'PDF…', icon: 'pdf' },
  { format: 'html', label: 'HTML page', icon: 'globe' },
  { format: 'md', label: 'Markdown', icon: 'doc' },
];

/** The menu of formats for the note at ``path``, below an element or at a point. */
export function exportMenu(path: string, at: { x: number; y: number } | Element): void {
  openMenu([{ section: 'Export as' }, ...FORMATS.map((f) => ({ label: f.label, icon: f.icon, run: () => exportNote(path, f.format) }))], at);
}

const MAX_EMBED_DEPTH = 3;   // as in read mode (richRender.ts)

/** Export the note at ``path`` (the open note as it is in the editor, saved or not). */
export async function exportNote(path: string, format: ExportFormat): Promise<void> {
  try {
    const open = ws.session?.path === path;
    const res = open ? null : await api.getNote(path);
    const text = (open ? ws.session!.text : res!.text).replace(/\r\n?/g, '\n');
    const title = (open ? ws.info?.title : res!.info?.title) || stemOf(path);
    const links = await api.noteLinks(path).catch(() => null);
    const file = fileName(title);
    if (format === 'md') {
      const opts = await portableOptions(path, links, new Set([path]), 1);
      const note = portableBody(text, opts);
      const md = portableMarkdown(title, note, await fullItems(note.keys, opts.citeInfo));
      downloadBlob(new Blob([md], { type: 'text/markdown;charset=utf-8' }), `${file}.md`);
      return;
    }
    const html = await htmlDocument(path, title, text, links);
    if (format === 'html') downloadBlob(new Blob([html], { type: 'text/html;charset=utf-8' }), `${file}.html`);
    else printHtml(html);
  } catch (e) {
    toastError(e);
  }
}

/** A file name from a note title: no path separators or characters some systems refuse. */
function fileName(title: string): string {
  return title.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 120) || 'note';
}

/** Library info for a key: what the note's links resolved, else the library list. */
function infoFor(links: Pick<NoteLinks, 'outgoing'> | null): (key: string) => RefInfo | null {
  const data = links ? linkDataOf(links) : null;
  return (key) => data?.cites.get(key) || ws.library.byKey.get(key) || null;
}

/** The full library items of the cited keys (volume, issue, pages, DOI …) for the references, in
 *  one request; the citation index's entries when they cannot be loaded. */
async function fullItems(keys: string[], citeInfo: (key: string) => RefInfo | null): Promise<(key: string) => RefInfo | null> {
  const ids = [...new Set(keys.map((k) => citeInfo(k)?.id).filter((id): id is string => !!id))];
  const byId = new Map<string, Item>();
  if (ids.length) {
    try { for (const it of (await api.itemsByIds(ids)).items) byId.set(it.id, it); } catch { /* the short entries */ }
  }
  return (key) => {
    const base = citeInfo(key);
    const it = base?.id ? byId.get(base.id) : undefined;
    return base && it ? { ...base, ...(it as RefInfo) } : base;
  };
}

/* ------------------------------------------------------------- Markdown */
/** What portableMarkdown needs for one note: its citations, and its embeds converted beforehand. */
async function portableOptions(path: string, links: Pick<NoteLinks, 'outgoing'> | null, seen: Set<string>, depth: number) {
  const citeInfo = infoFor(links);
  const data = links ? linkDataOf(links) : null;
  const embeds = new Map<string, Portable | null>();
  const id = (target: string, heading: string) => `${normTarget(target)}#${heading}`;
  for (const l of links?.outgoing || []) {
    if (l.kind !== 'embed' || embeds.has(id(l.target, l.heading || ''))) continue;
    const to = l.target ? data?.notes.get(normTarget(l.target)) : path;
    let out: Portable | null = null;
    if (to && !seen.has(to) && depth <= MAX_EMBED_DEPTH) {
      try {
        const [res, sub] = await Promise.all([api.getNote(to), api.noteLinks(to).catch(() => null)]);
        const t = res.text.replace(/\r\n?/g, '\n');
        const body = section(t.slice(splitFrontmatter(t).bodyStart), l.heading || undefined);
        out = portableBody(body, await portableOptions(to, sub, new Set(seen).add(to), depth + 1));
      } catch { /* shown as its name */ }
    }
    embeds.set(id(l.target, l.heading || ''), out);
  }
  return { citeInfo, embed: (target: string, heading: string) => embeds.get(id(target, heading)) ?? null, task: (tid: string) => taskHooks.portable?.(tid) ?? null };
}

/* ---------------------------------------------------------------- HTML */
/** The note as a standalone HTML page: its styles, fonts and images inside. */
async function htmlDocument(path: string, title: string, text: string, links: Pick<NoteLinks, 'outgoing'> | null): Promise<string> {
  const citeInfo = infoFor(links);
  const opts: RendererOptions = { citeInfo, equations: equationMode, macros: noteMacros };
  const art = document.createElement('article');
  art.className = 'prose';
  // drawn off screen: Mermaid and the code highlighter work on elements in the page
  art.style.cssText = 'position:fixed;left:-10000px;top:0;width:760px;visibility:hidden';
  art.innerHTML = embedHtml(await loadMD(), path, text, undefined, links, opts);
  document.body.appendChild(art);
  try {
    await Promise.all([
      renderEmbeds(art, opts, new Set([path]), 1, 'default'),
      highlightCodeBlocks(art),
      renderMermaidBlocks(art, 'default'),
    ]);
    const keys = [...art.querySelectorAll<HTMLElement>('.cite[data-key]')].map((el) => el.dataset.key!);
    const refInfo = await fullItems(keys, citeInfo);
    const refs = referenceKeys(keys, refInfo);
    staticCopy(art, new Set(refs));
    await inlineImages(art);
    const body = (art.firstElementChild?.tagName === 'H1' ? '' : `<h1>${esc(title)}</h1>\n`) + art.innerHTML
      + (refs.length ? `\n<section class="references"><h2>References</h2><ol>${refs
        .map((k) => `<li id="${referenceId(k)}">${referenceHtml(k, refInfo(k)!)}</li>`).join('')}</ol></section>` : '');
    const css = await styles(!!art.querySelector('.katex'));
    return `<!doctype html>
<html lang="${esc(document.documentElement.lang || 'en')}" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="Synapse">
<title>${esc(title)}</title>
<style>
${css}
</style>
</head>
<body>
<article class="prose">
${body}
</article>
</body>
</html>
`;
  } finally {
    art.remove();
  }
}

/** Remove what only works inside the app: links into it, buttons, ticking tasks. A citation of
 *  a key in ``listed`` links to its entry in the References list instead. */
function staticCopy(root: HTMLElement, listed: Set<string>): void {
  root.querySelectorAll('.copy-code').forEach((el) => el.remove());
  root.querySelectorAll('[data-line]').forEach((el) => el.removeAttribute('data-line'));
  for (const el of root.querySelectorAll<HTMLElement>('a.wikilink, a.cite, .eqref.missing')) {
    const key = el.classList.contains('cite') ? el.dataset.key : undefined;
    const out = document.createElement(key && listed.has(key) ? 'a' : 'span');
    out.className = el.className;
    out.innerHTML = el.innerHTML;
    if (out.tagName === 'A') { out.setAttribute('href', `#${referenceId(key!)}`); out.title = el.title; }
    el.replaceWith(out);
  }
  root.querySelectorAll<HTMLInputElement>('.task-box').forEach((box) => {
    box.removeAttribute('data-task-line');
    box.setAttribute('disabled', '');
    box.toggleAttribute('checked', box.checked);
  });
  root.querySelectorAll('details.properties').forEach((el) => el.remove());
  // tasks: their checkbox and title as they are now, nothing to click
  root.querySelectorAll('.task-ref-actions').forEach((el) => el.remove());
  root.querySelectorAll('.task-ref [data-task-act], .task-ref [role="checkbox"]').forEach((el) => { el.removeAttribute('data-task-act'); el.removeAttribute('role'); });
}

/** Images served by the app become data: URLs, so the file shows them anywhere. */
async function inlineImages(root: HTMLElement): Promise<void> {
  await Promise.all([...root.querySelectorAll<HTMLImageElement>('img[src^="/"]')].map(async (img) => {
    try {
      const r = await fetch(img.getAttribute('src')!);
      if (r.ok) img.src = await dataUrl(await r.blob());
    } catch { /* left as it is */ }
  }));
}

function dataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result));
    fr.onerror = () => reject(fr.error);
    fr.readAsDataURL(blob);
  });
}

async function fetchText(url: string): Promise<string> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Could not load ${url}`);
  return r.text();
}

/** The app's look for rendered notes (tokens, notes, export.css) and, with math, KaTeX with its fonts. */
async function styles(math: boolean): Promise<string> {
  const css = await Promise.all(['css/tokens.css', 'css/notes.css', 'css/tasks.css', 'css/export.css'].map(fetchText));
  if (math) css.unshift(await katexCss());
  return css.join('\n');
}

let katexCache: Promise<string> | null = null;
function katexCss(): Promise<string> {
  katexCache ||= (async () => {
    const base = 'vendor/md/katex/';
    const css = await fetchText(`${base}katex.min.css`);
    const fonts = new Map<string, string>();
    await Promise.all([...new Set([...css.matchAll(/url\((fonts\/[^)]+\.woff2)\)/g)].map((m) => m[1]))].map(async (f) => {
      const r = await fetch(base + f);
      if (r.ok) fonts.set(f, await dataUrl(await r.blob()));
    }));
    // only the woff2 files are vendored (frontend/vendor/README.md); the other formats are dropped
    return css.replace(/src:url\((fonts\/[^)]+\.woff2)\) format\("woff2"\)[^;}]*/g,
      (m, f: string) => (fonts.has(f) ? `src:url(${fonts.get(f)}) format("woff2")` : m));
  })().catch((e) => { katexCache = null; throw e; });
  return katexCache;
}

/* ----------------------------------------------------------------- PDF */
/** Open the print dialog for ``html`` from a hidden frame; "Save as PDF" makes the file. */
function printHtml(html: string): void {
  const frame = document.createElement('iframe');
  frame.className = 'export-frame';
  frame.setAttribute('aria-hidden', 'true');
  frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0';
  frame.srcdoc = html;
  let printed = false;
  const remove = () => setTimeout(() => frame.remove(), 1000);
  frame.addEventListener('load', async () => {
    const win = frame.contentWindow;
    if (!win || printed) return;
    printed = true;
    try { await win.document.fonts.ready; } catch { /* print anyway */ }
    win.addEventListener('afterprint', remove);
    win.focus();
    win.print();
    setTimeout(() => frame.remove(), 60_000);   // in case afterprint never comes
  });
  document.body.appendChild(frame);
  toast('Choose “Save as PDF” in the print dialog.', { type: 'info' });
}
