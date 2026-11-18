// The card shown when hovering a link to a PDF page of the notes folders ([[folder/file.pdf#p=4]]):
// its name, the page and the start of that page's text, and a button to open it. Styled like the
// citation card (.cite-card).
import { icon } from '../../icons.js';
import { escapeHtml } from '../../util.js';
import { type CiteTarget, citeTargetInDom } from './citeTarget.js';
import { pdfPage } from './mdscan.js';
import { findPdf, openPdfLink } from './pdfLinks.js';
import { resolveNote } from './workspace.js';

const HOVER = '.cm-lp-chip.wikilink.pdf, .prose a.wikilink.pdf';
let card: HTMLElement | null = null;
let showTimer: ReturnType<typeof setTimeout> | undefined;
let hideTimer: ReturnType<typeof setTimeout> | undefined;
let anchor: HTMLElement | null = null;

function ensure(): HTMLElement {
  if (card) return card;
  card = document.createElement('div');
  card.className = 'cite-card pdf-card';
  card.hidden = true;
  card.addEventListener('mouseenter', () => clearTimeout(hideTimer));
  card.addEventListener('mouseleave', () => scheduleHide());
  card.addEventListener('pointerdown', (e) => e.preventDefault());
  document.body.appendChild(card);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
  document.addEventListener('pointerdown', (e) => { if (card && !card.hidden && !card.contains(e.target as Node)) hide(); }, true);
  return card;
}

function hide(): void {
  clearTimeout(showTimer);
  if (card) card.hidden = true;
  anchor = null;
}
const scheduleHide = () => { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 250); };

async function show(el: HTMLElement): Promise<void> {
  const target = el.dataset.note || '', heading = el.dataset.heading || '';
  const path = resolveNote(target);
  const pdf = path ? findPdf(path) : undefined;
  const c = ensure();
  anchor = el;
  const page = pdfPage(heading);
  c.innerHTML = pdf
    ? `<div class="cc-item"><div class="cc-key">${icon('pdf', 'icon-sm')} ${escapeHtml(pdf.name)}</div>
        <div class="cc-title">${escapeHtml(pdf.path)}</div>
        <div class="cc-sub">${page ? `Page ${page}` : 'The whole PDF'}</div>
        <div class="cc-excerpt" hidden></div>
        <div class="cc-actions"><button class="btn btn-sm" data-cc="open">${icon('pdf', 'icon-sm')} Open${page ? ` p. ${page}` : ''}</button></div></div>`
    : `<div class="cc-item missing"><div class="cc-key">${escapeHtml(target)}</div><div class="cc-title">Not in your notes folders</div>
        <div class="cc-sub">No PDF of this name was found.</div></div>`;
  c.querySelector('[data-cc="open"]')?.addEventListener('click', () => {
    const spot: CiteTarget | null = el.classList.contains('cm-lp-chip') ? null : citeTargetInDom(el);
    hide();
    void openPdfLink(pdf!.path, heading, { spot });
  });
  const r = el.getBoundingClientRect();
  c.style.left = '0px'; c.style.top = '0px'; c.hidden = false;
  const place = () => {
    const w = c.offsetWidth, h = c.offsetHeight;
    c.style.left = `${Math.max(8, Math.min(r.left, innerWidth - w - 8))}px`;
    c.style.top = `${r.bottom + 6 + h > innerHeight - 8 ? Math.max(8, r.top - h - 6) : r.bottom + 6}px`;
  };
  place();
  if (pdf && page) {
    try {
      const res = await fetch(`/api/note-pdfs/${pdf.id}/page/${page}`);
      const d = res.ok ? await res.json() as { excerpt?: string } : null;
      const box = c.querySelector<HTMLElement>('.cc-excerpt');
      if (d?.excerpt && box && anchor === el) { box.textContent = d.excerpt; box.hidden = false; place(); }
    } catch { /* the card stays without the text */ }
  }
}

/** Hover cards for links to PDF pages inside ``container`` (the notes view). */
export function attachPdfCards(container: HTMLElement): void {
  const at = (e: Event) => (e.target as Element).closest?.<HTMLElement>(HOVER);
  container.addEventListener('mouseover', (e) => {
    const el = at(e);
    if (!el || el === anchor) { if (el) clearTimeout(hideTimer); return; }
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    showTimer = setTimeout(() => { if (el.isConnected) void show(el); }, 350);
  });
  container.addEventListener('mouseout', (e) => {
    if (!at(e)) return;
    clearTimeout(showTimer);
    if (!e.relatedTarget || !card?.contains(e.relatedTarget as Node)) scheduleHide();
  });
}
