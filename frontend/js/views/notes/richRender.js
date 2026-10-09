// DOM helpers for rendered notes: Mermaid diagrams, code highlighting, embedded notes.
import { api } from '../../api.js';
import { escapeHtml } from '../../util.js';
import { highlightStyle, loadCM, loadMD } from './editor.js';
import { splitFrontmatter } from './mdscan.js';
import { createRenderer, slugHeading } from './render.js';

/* --------------------------------------------------------------- Mermaid */
let mermaidLoading = null;
let mermaidTheme = null;
let mermaidSeq = 0;

function loadMermaid() {
  mermaidLoading ||= new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'vendor/mermaid/mermaid.min.js';
    s.onload = () => resolve(window.mermaid);
    s.onerror = () => { mermaidLoading = null; reject(new Error('Could not load Mermaid')); };
    document.head.appendChild(s);
  });
  return mermaidLoading;
}

/** Render a Mermaid diagram into `el` (shows the error and the source if it is invalid). */
export async function renderMermaid(el, code) {
  el.classList.add('loading');
  el.textContent = 'Rendering diagram…';
  try {
    const mm = await loadMermaid();
    const theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'default';
    if (mermaidTheme !== theme) {
      // 'strict': no scripts, no HTML labels, links sanitised
      mm.initialize({ startOnLoad: false, securityLevel: 'strict', theme });
      mermaidTheme = theme;
    }
    const { svg } = await mm.render(`zc-mermaid-${++mermaidSeq}`, code);
    el.innerHTML = svg;
  } catch (e) {
    el.innerHTML = `<div class="mermaid-error">Diagram error: ${escapeHtml(String(e?.message || e).split('\n')[0])}</div><pre>${escapeHtml(code)}</pre>`;
    document.getElementById(`dzc-mermaid-${mermaidSeq}`)?.remove();   // mermaid leaves an error svg behind
  } finally {
    el.classList.remove('loading');
  }
}

/* ------------------------------------------------------- code highlighting */
export async function highlightCodeBlocks(root) {
  const blocks = [...root.querySelectorAll('.code-block[data-lang]:not(.hl)')];
  if (!blocks.length) return;
  const cm = await loadCM();
  for (const block of blocks) {
    block.classList.add('hl');
    const code = block.querySelector('code');
    const desc = cm.LanguageDescription.matchLanguageName(cm.languages, block.dataset.lang, true);
    if (!code || !desc) continue;
    try {
      const support = await desc.load();
      const text = code.textContent;
      const tree = support.language.parser.parse(text);
      const frag = document.createDocumentFragment();
      cm.highlightCode(text, tree, highlightStyle(cm), (t, cls) => {
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

export function renderMermaidBlocks(root) {
  for (const b of root.querySelectorAll('.mermaid-block:not(.done)')) {
    b.classList.add('done');
    const code = b.querySelector('.mermaid-src')?.textContent || '';
    const target = document.createElement('div');
    b.replaceChildren(target);
    renderMermaid(target, code);
  }
}

/* --------------------------------------------------------------- embeds */
const MAX_EMBED_DEPTH = 3;

/** Text of one section (from a heading to the next heading of the same or higher level). */
function section(body, heading) {
  if (!heading) return body;
  const lines = body.split('\n');
  const want = slugHeading(heading);
  let start = -1, level = 0;
  for (let i = 0; i < lines.length; i++) {
    const m = /^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$/.exec(lines[i]);
    if (!m) continue;
    if (start < 0 && slugHeading(m[2]) === want) { start = i; level = m[1].length; continue; }
    if (start >= 0 && m[1].length <= level) return lines.slice(start, i).join('\n');
  }
  return start >= 0 ? lines.slice(start).join('\n') : `*Heading “${heading}” not found.*`;
}

/** Fill every ![[embed]] under `root` (recursively, at most 3 levels, never in a cycle). */
export async function renderEmbeds(root, seen = new Set(), depth = 1) {
  const lib = await loadMD();
  for (const el of root.querySelectorAll(':scope .note-embed[data-path]:not(.filled)')) {
    el.classList.add('filled');
    const path = el.dataset.path;
    const body = el.querySelector('.embed-body');
    if (seen.has(path) || depth > MAX_EMBED_DEPTH) {
      body.innerHTML = `<p class="muted">${seen.has(path) ? 'Not shown again (the note embeds itself).' : 'Embedded too deeply.'}</p>`;
      continue;
    }
    try {
      const res = await api.getNote(path);
      const text = res.text.replace(/\r\n?/g, '\n');
      const src = section(text.slice(splitFrontmatter(text).bodyStart), el.dataset.heading);
      body.innerHTML = createRenderer(lib).render(src);
      const next = new Set(seen).add(path);
      await renderEmbeds(body, next, depth + 1);
      highlightCodeBlocks(body);
      renderMermaidBlocks(body);
    } catch (e) {
      body.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    }
  }
}
