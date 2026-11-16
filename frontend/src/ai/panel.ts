// The "Ask AI" panel: a drawer on the right with the question box, the streamed answer, what the AI
// is reading, a stop button and follow-ups in the same conversation. One component for the app and
// for the PDF viewer (a tab of the app or a page of its own): each opens it in its own document,
// with a ``host`` that knows how to open a PDF there. See docs/companion.md.
//
// The answer is untrusted model output. It goes through the notes' renderer (markdown-it with raw
// HTML off, KaTeX), so [@key, p. N] citations and math work; images that are not the app's own
// are dropped, judged on the resolved address and before insertion (a remote image would leak the conversation in its URL); links open in a new tab
// with no opener. Until the renderer has loaded the text is shown escaped (textContent).
import type { CitePdf, CiteEntry } from '../types.js';
import { icon } from '../icons.js';
import { createRenderer, type MdLib, type Renderer } from '../views/notes/render.js';
import { AiError, MAX_CONTEXT, MAX_PROMPT, type AskContext, type JobEvent, type JobState, ask, cancelJob, follow, isFinal } from './client.js';
import { refreshAiStatus } from './status.js';

/** What differs between the app and the viewer. */
export interface AskHost {
  /** Open a PDF of a cited item at a page. */
  openPdf(item: { id: string; title: string }, pdf: CitePdf, page: number | null): void;
  /** Show an item that has no PDF (optional). */
  showItem?(id: string): void;
}

interface Turn { question: string; text: string; tool: string; state: JobState; error: string; box: HTMLElement; body: HTMLElement; status: HTMLElement }

const THROTTLE_MS = 120;
const START_COMMAND = 'python synapse_companion.py run';

let el: HTMLElement | null = null;
let teardown: (() => void) | null = null;

/** Open the panel (or point the open one at a new context). */
export function openAskAi(context: AskContext | undefined, host: AskHost): void {
  if (el) {
    const same = JSON.stringify(context) === JSON.stringify(current?.context);
    if (!same) current?.reset(context);
    current?.focus();
    return;
  }
  mount(context, host);
}

export const askAiOpen = (): boolean => !!el;
export function closeAskAi(): void { teardown?.(); }

let current: { context: AskContext | undefined; reset(c: AskContext | undefined): void; focus(): void } | null = null;

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
const snippet = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** The chip's text: where the question comes from. */
export function contextLabel(c: AskContext): string {
  if (c.kind === 'note') {
    const name = c.path.split('/').pop()!.replace(/\.md$/i, '');
    return c.selection ? `${name} — “${snippet(c.selection.replace(/\s+/g, ' ').trim(), 60)}”` : name;
  }
  const page = c.page ? `p. ${c.page}` : 'PDF';
  return c.quote ? `${page} — “${snippet(c.quote.replace(/\s+/g, ' ').trim(), 60)}”` : page;
}

/** The context as the server accepts it: long selections are cut. */
function limited(c: AskContext): AskContext {
  if (c.kind === 'note') return c.selection ? { ...c, selection: c.selection.slice(0, MAX_CONTEXT) } : c;
  return c.quote ? { ...c, quote: c.quote.slice(0, MAX_CONTEXT) } : c;
}

function mount(initial: AskContext | undefined, host: AskHost): void {
  const prevFocus = document.activeElement as HTMLElement | null;
  const root = document.createElement('aside');
  root.className = 'ai-panel';
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-label', 'Ask AI');
  root.innerHTML = `
    <header class="ai-head"><span class="ai-title">${icon('sparkle')} Ask AI</span>
      <select class="ai-provider" aria-label="AI" hidden></select>
      <button class="ai-icon" data-new title="New conversation" aria-label="New conversation">${icon('refresh')}</button>
      <button class="ai-icon" data-close title="Close (Esc)" aria-label="Close">${icon('close')}</button></header>
    <div class="ai-context" hidden></div>
    <div class="ai-thread" aria-live="polite"></div>
    <div class="ai-help" hidden></div>
    <form class="ai-form">
      <textarea class="ai-input" rows="2" placeholder="Ask about this…" aria-label="Question" maxlength="${MAX_PROMPT}"></textarea>
      <div class="ai-actions"><span class="ai-hint">Enter to send, Shift+Enter for a new line</span>
        <button type="button" class="ai-btn" data-stop hidden>${icon('close', 'icon-sm')} Stop</button>
        <button type="submit" class="ai-btn primary" data-send>Ask</button></div>
    </form>`;
  const q = <T extends HTMLElement>(s: string) => root.querySelector<T>(s)!;
  const thread = q('.ai-thread'), input = q<HTMLTextAreaElement>('.ai-input'), help = q('.ai-help'), chip = q('.ai-context');
  const stop = q<HTMLButtonElement>('[data-stop]'), send = q<HTMLButtonElement>('[data-send]'), provider = q<HTMLSelectElement>('.ai-provider');

  let context = initial;
  let conversation: string | undefined;
  let turn: Turn | null = null;
  let abort: AbortController | null = null;
  let job: string | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let lib: MdLib | null = null, renderer: Renderer | null = null;
  let cites = new Map<string, CiteEntry>();
  let closed = false;

  // markdown-it + KaTeX and the citable papers load once the panel is open; answers shown before
  // that are plain text and are drawn again when these arrive.
  import('../../vendor/md/md.js').then((m) => {
    lib = m;
    renderer = createRenderer(m, { citeInfo: (k) => cites.get(k) || null });
    paint();
  }).catch(() => { /* the answers stay plain text */ });
  fetch('/api/notes/cite_index').then((r) => (r.ok ? r.json() : null)).then((d: { items: CiteEntry[] } | null) => {
    if (d) { cites = new Map(d.items.map((i) => [i.key, i])); paint(); }
  }).catch(() => {});

  function draw(t: Turn): void {
    if (renderer && lib) {
      // built in an inert template and filtered before it is inserted: an image there is not fetched
      const tpl = document.createElement('template');
      tpl.innerHTML = renderer.render(t.text);
      for (const img of tpl.content.querySelectorAll('img')) {
        let local = false;
        try {
          const u = new URL(img.getAttribute('src') || '', location.href);   // ../ segments resolved
          local = u.origin === location.origin && u.pathname.startsWith('/api/notes/');
        } catch { /* not an address */ }
        if (!local) img.replaceWith(document.createTextNode(img.getAttribute('alt') || ''));
      }
      for (const a of tpl.content.querySelectorAll<HTMLAnchorElement>('a[href]')) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
      t.body.replaceChildren(tpl.content);
    } else t.body.textContent = t.text;
    t.body.classList.toggle('plain', !renderer);
    t.box.classList.toggle('streaming', t.state === 'queued' || t.state === 'running');
    t.status.textContent = t.error || (t.state === 'cancelled' ? 'Stopped.' : t.state === 'queued' ? 'Waiting for your computer…'
      : t.state === 'running' && t.tool ? `Reading… (${t.tool})` : t.state === 'running' && !t.text ? 'Thinking…' : '');
    t.status.classList.toggle('error', !!t.error);
  }
  const paint = () => { for (const b of thread.querySelectorAll<HTMLElement>('.ai-turn')) { const t = turns.get(b); if (t) draw(t); } };
  const turns = new WeakMap<HTMLElement, Turn>();
  const schedule = (t: Turn) => { if (!timer) timer = setTimeout(() => { timer = undefined; draw(t); thread.scrollTop = thread.scrollHeight; }, THROTTLE_MS); };

  const busy = () => !!turn && !isFinal(turn.state);
  function sync(): void {
    stop.hidden = !busy();
    send.hidden = busy();
    chip.querySelector<HTMLElement>('[data-drop]')?.toggleAttribute('hidden', !!conversation);
  }
  function paintContext(): void {
    chip.hidden = !context;
    chip.innerHTML = context ? `<span class="ai-chip" title="${esc(contextLabel(context))}"><span class="ai-chip-text">${esc(contextLabel(context))}</span>
      <button type="button" class="ai-icon" data-drop title="Ask without it" aria-label="Remove context">${icon('close', 'icon-sm')}</button></span>` : '';
    sync();
  }

  function showHelp(message?: string): void {
    help.hidden = false;
    help.innerHTML = `<div class="ai-help-title">${esc(message || 'No companion is connected.')}</div>
      <p>The companion runs on a computer where your AI app is installed. Start it there with</p>
      <pre class="ai-code">${esc(START_COMMAND)}</pre>
      <p>The first time, make a companion token in Settings → AI Access and follow its commands.</p>
      <button type="button" class="ai-btn" data-recheck>Check again</button>`;
  }
  async function check(): Promise<void> {
    const st = await refreshAiStatus();
    if (closed) return;
    const providers = new Map((st?.companions || []).flatMap((c) => c.providers).map((p) => [p.id, p.label]));
    const keep = provider.value;
    provider.innerHTML = [...providers].map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join('');
    if (providers.has(keep)) provider.value = keep;
    provider.hidden = providers.size < 2;
    if (!st) showHelp('Cannot reach the AI service.');
    else if (!st.companions.length) showHelp();
    else help.hidden = true;
  }

  function addTurn(question: string): Turn {
    const box = document.createElement('div');
    box.className = 'ai-turn';
    box.innerHTML = `<div class="ai-q"></div><div class="ai-answer prose"></div><div class="ai-status" role="status"></div>`;
    box.querySelector('.ai-q')!.textContent = question;
    const t: Turn = { question, text: '', tool: '', state: 'queued', error: '', box, body: box.querySelector('.ai-answer')!, status: box.querySelector('.ai-status')! };
    turns.set(box, t);
    thread.appendChild(box);
    thread.scrollTop = thread.scrollHeight;
    return t;
  }

  function finish(t: Turn, state: JobState, error = ''): void {
    t.state = state;
    if (error) t.error = error;
    clearTimeout(timer);
    timer = undefined;
    draw(t);
    job = null;
    abort = null;
    sync();
    if (!closed) input.focus();
  }

  async function submit(): Promise<void> {
    const prompt = input.value.trim();
    if (!prompt || busy()) return;
    input.value = '';
    help.hidden = true;
    const t = addTurn(prompt);
    turn = t;
    sync();
    const ctl = new AbortController();
    abort = ctl;
    try {
      const reply = await ask(prompt, { context: conversation ? undefined : context && limited(context), conversation, provider: provider.hidden ? undefined : provider.value });
      conversation = reply.conversation;
      job = reply.job;
      if (ctl.signal.aborted) { cancelJob(reply.job).catch(() => {}); return; }
      sync();
      await follow(reply.job, (events: JobEvent[], state: JobState) => {
        for (const e of events) {
          if (e.type === 'text') { t.text += e.text; t.tool = ''; }
          else if (e.type === 'tool') t.tool = e.name.replace(/^mcp__synapse__/, '').replace(/_/g, ' ');
          else if (e.type === 'error') t.error = e.message;
        }
        t.state = state;
        if (isFinal(state)) finish(t, state, state === 'error' && !t.error ? 'The AI could not answer.' : '');
        else schedule(t);
      }, ctl.signal);
    } catch (e) {
      if (ctl.signal.aborted) return;
      if (e instanceof AiError && e.status === 409) { showHelp(e.message); t.box.remove(); input.value = prompt; turn = null; job = null; sync(); return; }
      finish(t, 'error', (e as Error).message);
    }
  }

  async function stopNow(): Promise<void> {
    const t = turn, id = job, ctl = abort;
    if (!t || !busy()) return;
    ctl?.abort();
    if (id) await cancelJob(id).catch(() => {});
    finish(t, 'cancelled');
  }

  function reset(next: AskContext | undefined): void {
    void stopNow();
    context = next;
    conversation = undefined;
    turn = null;
    thread.innerHTML = '';
    paintContext();
    check();
    input.focus();
  }

  function close(): void {
    closed = true;
    ctlCancel();
    root.remove();
    document.removeEventListener('keydown', onKey, true);
    el = null;
    current = null;
    teardown = null;
    if (prevFocus?.isConnected) prevFocus.focus();
  }
  function ctlCancel(): void {
    abort?.abort();
    if (job) cancelJob(job).catch(() => {});   // nobody is reading it any more: free the companion
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && root.contains(document.activeElement)) { e.preventDefault(); e.stopPropagation(); close(); }
  };

  thread.addEventListener('click', (e) => {
    const a = (e.target as Element).closest<HTMLAnchorElement>('a');
    if (!a) return;
    e.preventDefault();
    if (a.classList.contains('cite')) {
      const it = cites.get(a.dataset.key || '');
      const pdfs = it?.pdfs?.length ? it.pdfs : it?.pdf ? [it.pdf] : [];
      const pdf = pdfs.find((p) => p.id === Number(a.dataset.att)) || pdfs[0];
      const page = a.dataset.page ? Number(a.dataset.page) : null;
      if (it && pdf) host.openPdf({ id: it.id, title: it.title }, pdf, page);
      else if (it && host.showItem) host.showItem(it.id);
      return;
    }
    if (/^https?:\/\//i.test(a.href) && !a.classList.contains('wikilink')) window.open(a.href, '_blank', 'noopener,noreferrer');
  });
  help.addEventListener('click', (e) => { if ((e.target as Element).closest('[data-recheck]')) check(); });
  chip.addEventListener('click', (e) => { if ((e.target as Element).closest('[data-drop]')) { context = undefined; paintContext(); input.focus(); } });
  q('[data-close]').addEventListener('click', close);
  q('[data-new]').addEventListener('click', () => reset(context));
  stop.addEventListener('click', () => void stopNow());
  q('.ai-form').addEventListener('submit', (e) => { e.preventDefault(); void submit(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); void submit(); }
  });
  document.addEventListener('keydown', onKey, true);

  document.body.appendChild(root);
  el = root;
  teardown = close;
  current = { get context() { return context; }, reset, focus: () => input.focus() };
  paintContext();
  void check();
  input.focus();
}
