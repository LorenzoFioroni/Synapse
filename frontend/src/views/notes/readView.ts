// Read mode: the open note rendered as a page, and what its links, citations, equation
// references and task boxes do when clicked. Also following a [[link]] (from any mode) and
// going to a line or heading of the open note.
import { api } from '../../api.js';
import { confirmDialog } from '../../ui/dialog.js';
import { toast, toastError } from '../../ui/toast.js';
import { escapeHtml } from '../../util.js';
import { openCitation, showCard } from './citeCard.js';
import { type CiteTarget, citeTargetInDom, citeTargetInText } from './citeTarget.js';
import { labelId } from './equations.js';
import { fitEquations, fitSoon } from './mathFit.js';
import { splitFrontmatter } from './mdscan.js';
import { createRenderer, slugHeading } from './render.js';
import { highlightCodeBlocks, renderEmbeds, renderMermaidBlocks } from './richRender.js';
import { refreshTree } from './tree.js';
import { citeInfo, equationMode, hooks, noteMacros, parentOf, resolveNote, ws } from './workspace.js';

const readEl = () => ws.root.querySelector<HTMLElement>('.note-read')!;

let readTimer: ReturnType<typeof setTimeout> | undefined;
export function scheduleRead(): void { clearTimeout(readTimer); readTimer = setTimeout(renderRead, 300); }

/**
 * Render the open note into the read view. Top-level blocks whose HTML did not change keep their
 * DOM, so ticking a task redraws only its list, and formulas, diagrams, highlighted code and
 * embedded notes elsewhere are not drawn (or loaded) again. ``fresh`` redraws everything: when the
 * view opens, and when the theme or the math settings change.
 */
export function renderRead({ fresh = false } = {}): void {
  clearTimeout(readTimer);
  const { session, lib } = ws;
  if (!session || !lib) return;
  const art = ws.root.querySelector<HTMLElement>('.note-read .prose')!;
  const text = session.text.replace(/\r\n?/g, '\n');
  const fm = splitFrontmatter(text);
  const lineOffset = fm.bodyLine;
  const r = createRenderer(lib, { citeInfo, resolveNote, lineOffset, equations: equationMode, macros: noteMacros });
  const props = fm.header === null ? '' : `<details class="properties"><summary>Properties</summary><dl>${fm.header.split('\n').filter((l) => l.trim())
    .map((l) => { const i = l.indexOf(':'); return i > 0 ? `<dt>${escapeHtml(l.slice(0, i))}</dt><dd>${escapeHtml(l.slice(i + 1).trim())}</dd>` : `<dd class="raw">${escapeHtml(l)}</dd>`; }).join('')}</dl></details>`;
  const page = art.parentElement!;
  const scroll = page.scrollTop;
  const added = patchBlocks(art, props + r.render(text.slice(fm.bodyStart)), fresh);
  for (const el of added) if (el.querySelector('.katex-display')) { fitEquations(el); fitSoon(el); }
  page.scrollTop = scroll;
  highlightCodeBlocks(art);
  renderMermaidBlocks(art);
  renderEmbeds(art, { citeInfo, resolveNote, equations: equationMode, macros: noteMacros }, new Set([session.path]));
}

/** The HTML each top-level block of the read view was made from. */
const blockSource = new WeakMap<Element, string>();

/** Show ``html`` in ``root``, reusing the blocks already there whose HTML is the same. Returns the new ones. */
function patchBlocks(root: HTMLElement, html: string, fresh: boolean): HTMLElement[] {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const old = new Map<string, Element[]>();
  if (!fresh) {
    for (const el of root.children) {
      const src = blockSource.get(el);
      if (src !== undefined) (old.get(src) || old.set(src, []).get(src)!).push(el);
    }
  }
  const added: HTMLElement[] = [];
  const blocks = [...tpl.content.children].map((el) => {
    const src = el.outerHTML;
    const same = old.get(src)?.shift();
    if (same) return same;
    blockSource.set(el, src);
    added.push(el as HTMLElement);
    return el;
  });
  // change the DOM in place: blocks that stay are never detached, so the browser restyles only
  // what is new (replacing all children would make it lay out the whole note again)
  const keep = new Set<Node>(blocks);
  for (const n of [...root.childNodes]) if (!keep.has(n)) n.remove();
  let at = root.firstChild;
  for (const el of blocks) {
    if (el === at) at = at.nextSibling;
    else root.insertBefore(el, at);
  }
  return added;
}

/** Go to a [[target]] (and heading); offer to create the note when there is none. */
export async function followLink(target: string, heading: string): Promise<void> {
  const { session } = ws;
  const path = target ? resolveNote(target) : session?.path;
  if (path) {
    if (path !== session?.path) await hooks.openNote(path);
    if (heading) gotoHeading(heading);
    return;
  }
  if (!target) return;
  if (await confirmDialog(`There is no note called “${target}” yet.`, { title: 'Create note?', confirm: 'Create' })) {
    try {
      const res = await api.createNote({ title: target, folder: session ? parentOf(session.path) : '' });
      await refreshTree();
      await hooks.openNote(res.path);
    } catch (e) { toastError(e); }
  }
}

function gotoHeading(heading: string): void {
  const want = slugHeading(heading);
  const h = (ws.info?.headings || []).find((x) => slugHeading(x.text) === want);
  if (!h) return;
  if (ws.mode === 'read') ws.root.querySelector(`.note-read #h-${CSS.escape(want)}`)?.scrollIntoView({ block: 'start' });
  else ws.editor?.gotoLine(h.line);
}

/** Show a 1-based line of the open note, in whichever mode is active. */
export function gotoLine(line: number, block: ScrollLogicalPosition = 'center'): void {
  if (ws.mode === 'read') {
    const el = [...ws.root.querySelectorAll<HTMLElement>('.note-read [data-line]')].find((x) => Number(x.dataset.line) + 1 >= line);
    el?.scrollIntoView({ block });
  } else ws.editor?.gotoLine(line);
}

/** The source lines blocks of the read view start on (0-based) and where they are (px from the
 *  top of the page), in order. A block nested in one that starts on the same line is left out. */
function lineMarks(): { line: number; top: number }[] {
  const page = readEl();
  const base = page.getBoundingClientRect().top - page.scrollTop;
  const out: { line: number; top: number }[] = [];
  for (const el of page.querySelectorAll<HTMLElement>('.prose [data-line]')) {
    const line = Number(el.dataset.line);
    if (el.closest('.embed-body') || (out.length && line <= out[out.length - 1].line)) continue;
    out.push({ line, top: el.getBoundingClientRect().top - base });
  }
  return out;
}

/** The line at the top of the read view, like NoteEditor.topLine (1-based, with a fraction). */
export function readTopLine(): number {
  const y = readEl().scrollTop, marks = lineMarks();
  let i = -1;
  while (i + 1 < marks.length && marks[i + 1].top <= y) i++;
  if (i < 0) return 1;
  const a = marks[i], b = marks[i + 1];
  if (!b) return a.line + 1;
  return a.line + 1 + Math.min(1, (y - a.top) / Math.max(1, b.top - a.top)) * (b.line - a.line);
}

/** Scroll the read view so that ``at`` (as readTopLine gives it) is at the top. */
export function scrollReadTo(at: number): void {
  const page = readEl(), marks = lineMarks(), line = at - 1;
  let i = -1;
  while (i + 1 < marks.length && marks[i + 1].line <= line) i++;
  if (i < 0) { page.scrollTop = 0; return; }
  const a = marks[i], b = marks[i + 1];
  page.scrollTop = a.top + (b ? ((line - a.line) / (b.line - a.line)) * (b.top - a.top) : 0);
}

/** Where a citation element points in the PDF (a quote, a snapshot), for the hover card. */
export function citeTargetOf(el: HTMLElement): CiteTarget | null {
  const view = ws.editor?.view;
  if (view && view.dom.contains(el)) {
    try { return citeTargetInText(view.state.doc.toString(), view.posAtDOM(el)); } catch { return null; }
  }
  return citeTargetInDom(el);
}

function onReadClick(e: MouseEvent): void {
  const t = e.target as Element;
  const a = t.closest('a');
  const wl = t.closest<HTMLElement>('.wikilink');
  if (wl) { e.preventDefault(); followLink(wl.dataset.note || '', wl.dataset.heading || ''); return; }
  const et = t.closest('.note-embed .embed-title');
  if (et) { const em = et.closest<HTMLElement>('.note-embed')!; followLink(em.dataset.note || '', em.dataset.heading || ''); return; }
  const ref = t.closest<HTMLElement>('.eqref[data-label]');
  if (ref) {
    e.preventDefault();
    const target = ws.root.querySelector(`.note-read #${CSS.escape(labelId(ref.dataset.label!))}`)?.parentElement;
    if (target) {
      target.scrollIntoView({ block: 'center', behavior: 'smooth' });
      target.classList.remove('flash');
      void target.offsetWidth;
      target.classList.add('flash');
    }
    return;
  }
  const cite = t.closest<HTMLElement>('.cite');
  if (cite) {
    e.preventDefault();
    // the PDF's tab is reused; Cmd/Ctrl+click (or a middle click) opens a new one
    const newTab = e.metaKey || e.ctrlKey || e.button === 1;
    const att = cite.dataset.att ? Number(cite.dataset.att) : null;
    if (!openCitation(cite.dataset.key!, cite.dataset.page ? Number(cite.dataset.page) : null, citeTargetInDom(cite), { newTab, att })) showCard(cite);
    return;
  }
  const box = t.closest<HTMLElement>('.task-box[data-task-line]');
  if (box) { e.preventDefault(); if (ws.editor?.toggleTask(Number(box.dataset.taskLine))) renderRead(); return; }
  const copy = t.closest('.copy-code');
  if (copy) {
    const code = copy.parentElement?.querySelector('code')?.textContent || '';
    navigator.clipboard?.writeText(code).then(() => toast('Code copied.', { type: 'success' }), () => {});
    return;
  }
  const href = a?.getAttribute('href');
  if (a && href && !href.startsWith('#')) { e.preventDefault(); window.open(a.href, '_blank', 'noopener'); }
}

function onReadDblClick(e: MouseEvent): void {
  const t = e.target as Element;
  if (t.closest('a, input, button')) return;
  const block = t.closest<HTMLElement>('[data-line]');
  hooks.setMode(ws.editMode);
  if (block) ws.editor?.gotoLine(Number(block.dataset.line) + 1);
}

export function initReadView(): void {
  const read = readEl();
  read.addEventListener('click', onReadClick);
  read.addEventListener('auxclick', (e) => { if (e.button === 1 && (e.target as Element).closest('.cite')) onReadClick(e); });
  read.addEventListener('dblclick', onReadDblClick);
}
