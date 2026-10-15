// DOM helpers for rendered notes: Mermaid diagrams, code highlighting, embedded notes.
import { api } from '../../api.js';
import type { Mermaid } from '../../globals.js';
import { escapeHtml } from '../../util.js';
import { highlightStyle, loadCM, loadMD } from './editor.js';
import { embedHtml } from './embed.js';
import type { RendererOptions } from './render.js';

/* --------------------------------------------------------------- Mermaid */
let mermaidLoading: Promise<Mermaid> | null = null;
let mermaidTheme: string | null = null;
let mermaidSeq = 0;

function loadMermaid(): Promise<Mermaid> {
  mermaidLoading ||= new Promise<Mermaid>((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'vendor/mermaid/mermaid.min.js';
    s.onload = () => resolve(window.mermaid!);
    s.onerror = () => { mermaidLoading = null; reject(new Error('Could not load Mermaid')); };
    document.head.appendChild(s);
  });
  return mermaidLoading;
}

/** Render a Mermaid diagram into `el` (shows the error and the source if it is invalid), in the
 *  app's theme unless `theme` says otherwise ('default' is Mermaid's light theme). */
export async function renderMermaid(el: HTMLElement, code: string, theme?: 'default' | 'dark'): Promise<void> {
  el.classList.add('loading');
  el.textContent = 'Rendering diagram…';
  try {
    const mm = await loadMermaid();
    theme ||= document.documentElement.dataset.theme === 'dark' ? 'dark' : 'default';
    if (mermaidTheme !== theme) {
      // 'strict': no scripts, no HTML labels, links sanitised
      mm.initialize({ startOnLoad: false, securityLevel: 'strict', theme });
      mermaidTheme = theme;
    }
    const { svg } = await mm.render(`zc-mermaid-${++mermaidSeq}`, code);
    el.innerHTML = svg;
  } catch (e) {
    el.innerHTML = `<div class="mermaid-error">Diagram error: ${escapeHtml(String((e as Error | null)?.message || e).split('\n')[0])}</div><pre>${escapeHtml(code)}</pre>`;
    document.getElementById(`dzc-mermaid-${mermaidSeq}`)?.remove();   // mermaid leaves an error svg behind
  } finally {
    el.classList.remove('loading');
  }
}

/* ------------------------------------------------------- code highlighting */
export async function highlightCodeBlocks(root: ParentNode): Promise<void> {
  const blocks = [...root.querySelectorAll<HTMLElement>('.code-block[data-lang]:not(.hl)')];
  if (!blocks.length) return;
  const cm = await loadCM();
  for (const block of blocks) {
    block.classList.add('hl');
    const code = block.querySelector('code');
    const desc = cm.LanguageDescription.matchLanguageName(cm.languages, block.dataset.lang!, true);
    if (!code || !desc) continue;
    try {
      const support = await desc.load();
      const text = code.textContent || '';
      const tree = support.language.parser.parse(text);
      const frag = document.createDocumentFragment();
      cm.highlightCode(text, tree, highlightStyle(cm), (t: string, cls: string) => {
        if (!cls) { frag.appendChild(document.createTextNode(t)); return; }
        const s = document.createElement('span');
        s.className = cls;
        s.textContent = t;
        frag.appendChild(s);
      }, () => frag.appendChild(document.createTextNode('\n')));
      code.replaceChildren(frag);
    } catch { /* keep plain text */ }
  }
}

export function renderMermaidBlocks(root: ParentNode, theme?: 'default' | 'dark'): Promise<void> {
  const done: Promise<void>[] = [];
  for (const b of root.querySelectorAll('.mermaid-block:not(.done)')) {
    b.classList.add('done');
    const code = b.querySelector('.mermaid-src')?.textContent || '';
    const target = document.createElement('div');
    b.replaceChildren(target);
    done.push(renderMermaid(target, code, theme));
  }
  return Promise.all(done).then(() => {});
}

/* --------------------------------------------------------------- embeds */
const MAX_EMBED_DEPTH = 3;

/** Fill every ![[embed]] under `root` (recursively, at most 3 levels, never in a cycle). `opts`
 *  is what the embedding note is rendered with (library info for citations, equation settings).
 *  Resolves once the embeds, their code and their diagrams (in `theme`, see renderMermaid) are drawn. */
export async function renderEmbeds(root: HTMLElement, opts: RendererOptions = {}, seen = new Set<string>(), depth = 1,
  theme?: 'default' | 'dark'): Promise<void> {
  const lib = await loadMD();
  const drawing: Promise<void>[] = [];
  for (const el of root.querySelectorAll<HTMLElement>(':scope .note-embed[data-path]:not(.filled)')) {
    el.classList.add('filled');
    const path = el.dataset.path!;
    const body = el.querySelector<HTMLElement>('.embed-body')!;
    if (seen.has(path) || depth > MAX_EMBED_DEPTH) {
      body.innerHTML = `<p class="muted">${seen.has(path) ? 'Not shown again (the note embeds itself).' : 'Embedded too deeply.'}</p>`;
      continue;
    }
    try {
      const [res, links] = await Promise.all([api.getNote(path), api.noteLinks(path).catch(() => null)]);
      body.innerHTML = embedHtml(lib, path, res.text, el.dataset.heading, links, opts);
      const next = new Set(seen).add(path);
      await renderEmbeds(body, opts, next, depth + 1, theme);
      drawing.push(highlightCodeBlocks(body), renderMermaidBlocks(body, theme));
    } catch (e) {
      body.innerHTML = `<p class="muted">${escapeHtml((e as Error).message)}</p>`;
    }
  }
  await Promise.all(drawing);
}
