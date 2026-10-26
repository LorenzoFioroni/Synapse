// What is shown around the open note: its save status, the breadcrumbs above it (with links back
// to the paper for a paper note), and the details panel on the right (cited papers, backlinks,
// unresolved links, outline, note info).
import { icon } from '../../icons.js';
import { escapeHtml, formatDate, plural } from '../../util.js';
import { shortAuthors } from './library.js';
import type { SaveStatus } from './noteSession.js';
import { itemInfo, stemOf, taskHooks, ws } from './workspace.js';

const STATUS: Record<SaveStatus, [string, string]> = {
  saved: ['Saved', 'ok'],
  dirty: ['Edited', ''],
  saving: ['Saving…', ''],
  offline: ['Not saved yet — retrying', 'warn'],
  conflict: ['Changed elsewhere', 'bad'],
};

export function paintStatus(status: SaveStatus): void {
  const el = ws.root.querySelector<HTMLElement>('.note-status')!;
  const [label, cls] = STATUS[status] || ['', ''];
  el.className = `note-status ${cls}`;
  el.innerHTML = status === 'conflict'
    ? `${icon('alert', 'icon-sm')} ${label} <button class="btn btn-sm" data-act="show-conflict">Resolve…</button>`
    : `${status === 'saved' ? icon('check', 'icon-sm') : status === 'saving' ? '<span class="spinner"></span>' : ''} ${label}`;
}

export function paintCrumbs(): void {
  const { session, info } = ws;
  const p = session?.path || '';
  const parts = p.split('/');
  // A paper note links straight back to its item and PDF.
  const it = info?.type === 'paper' && info.paper ? itemInfo(info.paper) : null;
  const paper = it ? `<span class="note-paper">
      <button class="btn btn-sm btn-ghost" data-act="paper-item" title="Show “${escapeHtml(it.title)}” in the library">${icon('library', 'icon-sm')} Show in library</button>
      ${it.pdf ? `<button class="btn btn-sm btn-ghost" data-act="paper-pdf" title="Open the PDF">${icon('pdf', 'icon-sm')} PDF</button>` : ''}</span>` : '';
  ws.root.querySelector('.note-crumbs')!.innerHTML = parts.map((x, i) => (i === parts.length - 1
    ? `<button class="crumb last" data-act="rename" title="Rename (F2 in the list)">${escapeHtml(x.replace(/\.md$/i, ''))}</button>`
    : `<span class="crumb">${escapeHtml(x)}</span><span class="sep">/</span>`)).join('') + paper;
}

export function paintSide(): void {
  const { session, info, aiHist } = ws;
  const side = ws.root.querySelector('.notes-side .ns-details')!;   // the local graph above it stays
  if (!info) { side.innerHTML = ''; return; }
  const heads = info.headings || [];
  const minLevel = Math.min(...heads.map((x) => x.level), 6);
  const L = ws.linkResp && ws.linkResp.path_for === session?.path ? ws.linkResp : null;
  const cited = L?.cited || [];
  const back = L?.backlinks || [];
  const unresolved = (L?.unresolved || []).filter((u, i, a) => a.findIndex((x) => x.kind === u.kind && x.target === u.target) === i);
  const citedHtml = cited.length ? `<div class="ns-list">${cited.map((c) => {
    const it = itemInfo(c.key);
    return `<button class="ns-row cite-row${it ? '' : ' missing'}${it?.trashed ? ' trashed' : ''}" data-cite-keys="${escapeHtml(c.key)}" data-goto-line="${c.lines[0]}" title="${it?.trashed ? 'This item is in the Trash' : ''}">
      <span class="ns-main">${escapeHtml(it ? [shortAuthors(it.authors), it.year].filter(Boolean).join(' ') || c.key : `@${c.key}`)}${c.count > 1 ? ` <span class="count">×${c.count}</span>` : ''}</span>
      <span class="ns-sub">${escapeHtml(it ? it.title : 'not in your library')}</span></button>`;
  }).join('')}</div>` : '<div class="side-empty">Cite papers with [@key] — type [@ to search your library.</div>';
  const backHtml = back.length ? `<div class="ns-list">${back.map((b) => `
    <button class="ns-row" data-open-path="${escapeHtml(b.path)}" data-open-line="${b.line}">
      <span class="ns-main">${escapeHtml(b.source_title || stemOf(b.path))}</span>
      <span class="ns-sub">${escapeHtml(b.context)}</span></button>`).join('')}</div>` : '<div class="side-empty">No other note links here yet.</div>';
  const unresolvedHtml = unresolved.length ? `<div class="ns-list">${unresolved.map((u) => `
    <button class="ns-row warn" ${u.kind === 'cite' ? `data-cite-keys="${escapeHtml(u.target)}"` : `data-create-note="${escapeHtml(u.target)}"`} data-goto-line="${u.line}">
      <span class="ns-main">${u.kind === 'cite' ? `@${escapeHtml(u.target)}` : `[[${escapeHtml(u.target)}]]`}</span>
      <span class="ns-sub">${u.kind === 'cite' ? 'not in your library' : 'no such note — click to create'}</span></button>`).join('')}</div>` : '';
  side.innerHTML = `
    <div class="ns-section ns-tasks" hidden></div>
    <div class="ns-section"><div class="ns-title">Cited papers ${cited.length ? `<span class="count">${cited.length}</span>` : ''}</div>${citedHtml}</div>
    <div class="ns-section"><div class="ns-title">Linked from ${back.length ? `<span class="count">${back.length}</span>` : ''}</div>${backHtml}</div>
    ${unresolvedHtml ? `<div class="ns-section"><div class="ns-title">Unresolved <span class="count">${unresolved.length}</span></div>${unresolvedHtml}</div>` : ''}
    <div class="ns-section"><div class="ns-title">Outline</div>
      ${heads.length ? `<div class="outline">${heads.map((x) => `<button class="ol-item" data-outline-line="${x.line}" style="--l:${x.level - minLevel}">${escapeHtml(x.text)}</button>`).join('')}</div>`
        : '<div class="side-empty">Headings (# Title) appear here.</div>'}
    </div>
    <div class="ns-section"><div class="ns-title">Note</div>
      <dl class="ns-meta">
        ${info.type === 'paper' ? `<dt>Paper</dt><dd class="mono">${escapeHtml(info.paper || '')}</dd>` : ''}
        ${(info.tags || []).length ? `<dt>Tags</dt><dd>${info.tags.map((t) => `<span class="tag-pill sm">${escapeHtml(t)}</span>`).join(' ')}</dd>` : ''}
        ${info.created ? `<dt>Created</dt><dd>${escapeHtml(formatDate(info.created, true))}</dd>` : ''}
        ${info.mtime ? `<dt>Modified</dt><dd>${escapeHtml(formatDate(new Date(info.mtime * 1000).toISOString(), true))}</dd>` : ''}
        ${aiHist?.path === session?.path && aiHist?.entries.length ? `<dt>AI edits</dt><dd><button class="btn btn-sm btn-ghost ai-edits-btn" data-act="ai-edits">${icon('sparkle', 'icon-sm')} ${plural(aiHist.entries.length, 'edit')}…</button></dd>` : ''}
        <dt>File</dt><dd class="mono path">${escapeHtml(info.path)}</dd>
      </dl>
    </div>`;
  taskHooks.paintSide?.(side.querySelector<HTMLElement>('.ns-tasks')!);
}
