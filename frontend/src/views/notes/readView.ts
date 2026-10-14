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

/** Render the open note into the read view. */
export function renderRead(): void {
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
  art.innerHTML = props + r.render(text.slice(fm.bodyStart));
  fitEquations(art);
  fitSoon(art);
  page.scrollTop = scroll;
  highlightCodeBlocks(art);
  renderMermaidBlocks(art);
  renderEmbeds(art, new Set([session.path]));
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
