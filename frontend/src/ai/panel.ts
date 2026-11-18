// The "Ask AI" panel: header, the conversation (messages.ts) or the history of earlier ones
// (history.ts), and the question box (composer.ts). One component for the app and for the PDF
// viewer in a browser tab of its own (a viewer in an app tab uses the app's panel); each opens it in
// its own document with a ``host`` that knows how to open a PDF there, where its docked column is
// (placement.ts) and what is on screen to offer as context (context.ts). Context is added to the
// next question, not to the conversation: adding never starts a new one. The answer's
// handling of untrusted text is described in messages.ts. Closing hides the panel and keeps everything
// (the text being typed, the chips, the conversation, its scroll position, an answer on its way); the
// draft and the open conversation are also kept on this device across a reload (``session``). Its
// size, corner and width: layout.ts. See docs/companion.md and docs/frontend.md.
import type { CiteEntry, CitePdf } from '../types.js';
import { icon } from '../icons.js';
import { citeTargetInDom, viewerTarget } from '../views/notes/citeTarget.js';
import { pdfPage } from '../views/notes/mdscan.js';
import { createRenderer } from '../views/notes/render.js';
import {
  AiError, type AiStatus, type AskContext, type Conversation, type JobEvent, type JobState, type Usage,
  ask, cancelJob, follow, getConversation, isFinal, renameConversation,
} from './client.js';
import { createComposer } from './composer.js';
import { type Attached, MAX_ATTACHED, contextKey, describe, emptyTitle, suggestions } from './context.js';
import { createHistory } from './history.js';
import { MentionData } from './mentions.js';
import { Thread, type Turn, usageText } from './messages.js';
import { type Dock, place, remember } from './placement.js';
import {
  CORNER_EVENT, type Corner, DEFAULT_DOCK, MAX_DOCK, MIN_DOCK, type Size, clampDock, clampPopup, maxDock, resizedDock, resizedPopup,
  saveDock, savePopup, savedCorner, savedDock, savedPopup,
} from './layout.js';
import { load, save } from './prefs.js';
import { PANEL_EVENT, refreshAiStatus } from './status.js';

/** What differs between the app and the viewer. */
export interface AskHost {
  /** Open a PDF of a cited item at a page. */
  openPdf(item: { id: string; title: string }, pdf: CitePdf, page: number | null): void;
  /** Show an item that has no PDF (optional). */
  showItem?(id: string): void;
  /** Open a PDF of the notes folders a [[folder/file.pdf#p=4]] link names, at a page (and on quoted words). Optional. */
  openNotePdf?(pdf: { id: number; path: string }, page: number | null, quote?: string): void;
  /** Open a note a [[link]] names (optional; without it such links do nothing). */
  openNote?(path: string): void;
  /** The page's column for the docked panel (without it the panel is always a popup). */
  dock?: Dock;
  /** What is on screen now (the open note, the PDF page, the words selected), offered as context. */
  here?(): Attached[];
  /** Call ``fn`` when ``here`` may have changed; returns what stops it. */
  watch?(fn: () => void): () => void;
}

const START_COMMAND = 'python3 synapse_companion.pyz';

let el: HTMLElement | null = null;
let current: { add(a: Attached): void; focus(): void; show(): void } | null = null;

/** Open the panel (or bring the open one forward), with ``add`` added to the next question. */
export function openAskAi(host: AskHost, add?: Attached): void {
  if (!el) mount(host);
  else if (el.hidden) current?.show();
  if (add) current?.add(add);
  current?.focus();
}

/** What is kept on this device across a reload: the draft (text, caret, chips) and the conversation shown. */
interface Session { text: string; caret: number; context: AskContext[]; conversation?: string }
const SESSION = 'zc.ai.session';

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

function mount(host: AskHost): void {
  let prevFocus = document.activeElement as HTMLElement | null;
  const root = document.createElement('aside');
  root.className = 'ai-panel';
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-label', 'Ask AI');
  root.innerHTML = `
    <header class="ai-head"><button type="button" class="ai-title" data-title title="Rename"><span class="ai-title-icon">${icon('sparkle')}</span><span class="ai-title-text">Ask AI</span></button>
      <button type="button" class="ai-icon" data-usage title="Usage of this conversation" aria-label="Usage of this conversation" hidden>${icon('usage')}</button>
      <button type="button" class="ai-icon" data-history title="Earlier conversations" aria-label="Earlier conversations">${icon('restore')}</button>
      <button type="button" class="ai-icon" data-new title="New conversation" aria-label="New conversation">${icon('plus')}</button>
      <button type="button" class="ai-icon" data-place></button>
      <button type="button" class="ai-icon" data-close title="Close (Esc)" aria-label="Close">${icon('close')}</button></header>
    <div class="ai-total" hidden></div>
    <div class="ai-body"></div>
    <div class="ai-help" hidden></div>`;
  const q = <T extends HTMLElement>(s: string) => root.querySelector<T>(s)!;
  const body = q('.ai-body'), help = q('.ai-help'), total = q('.ai-total'), titleBtn = q('[data-title]');
  const titleText = q('.ai-title-text'), placeBtn = q('[data-place]'), usageBtn = q('[data-usage]');

  let attached: Attached[] = [];          // goes with the next question
  let offered: Attached[] = [];           // what is on screen and not added yet
  let emptyKey = '';                      // what the empty state shows now
  let conversation: string | undefined;
  let locked: string | null = null;        // the provider of the open conversation
  let status: AiStatus | null = null;
  let title = '';
  let turn: Turn | null = null;
  let turns: Turn[] = [];
  let abort: AbortController | null = null;
  let job: string | null = null;
  let shown = true;                       // false while the panel is hidden (it is kept, not torn down)
  let cites = new Map<string, CiteEntry>();
  const providerLabels = () => new Map((status?.companions || []).flatMap((c) => c.providers).map((p) => [p.id, p.label]));

  const thread = new Thread();
  const data = new MentionData();   // what a question can cite; fetched on first use, the library part now for the citations in answers
  data.onLoad = () => {
    cites = new Map(data.items().map((i) => [i.key, i]));
    thread.paintAll();
    if (attached.length) { attached = chipsOf(attached.map((a) => a.ctx)); paintContext(); }   // a restored paper chip gets its key
  };
  const composer = createComposer({
    mentions: data,
    onSend: (t) => void submit(t),
    onStop: () => void stopNow(),
    onDrop: (i) => { attached.splice(i, 1); paintContext(); composer.focus(); },
    onAdd: (i) => { if (offered[i]) add(offered[i]); },
    onInput: () => saveSoon(),
  });
  const history = createHistory({
    onOpen: (c) => void openConversation(c),
    onRenamed: (id, t) => { if (id === conversation) setTitle(t); },
    onDeleted: (id) => { if (id === conversation) reset(); },
    label: (id) => providerLabels().get(id) || id,
  });
  body.append(thread.el, history.el);
  root.append(composer.el);
  history.el.hidden = true;

  import('../../vendor/md/md.js').then((m) => thread.setRenderer(createRenderer(m, { citeInfo: (k) => cites.get(k) || null, resolveNote: data.resolveNote })))
    .catch(() => { /* the answers stay plain text */ });
  void data.load('cite');

  const busy = () => !!turn && !isFinal(turn.state);
  const showingHistory = () => !history.el.hidden;

  function setTitle(t: string): void {
    title = t;
    titleText.textContent = t || 'Ask AI';
    titleBtn.title = conversation ? 'Rename this conversation' : '';
    titleBtn.toggleAttribute('data-plain', !conversation);
  }
  function paintContext(): void {
    const have = new Set(attached.map((a) => contextKey(a.ctx)));
    offered = attached.length >= MAX_ATTACHED ? [] : (host.here?.() || []).filter((a) => !have.has(contextKey(a.ctx)));
    composer.setContext(attached, offered);
    paintEmpty();
    saveSoon();
  }
  function add(a: Attached): void {
    if (attached.length >= MAX_ATTACHED || attached.some((x) => contextKey(x.ctx) === contextKey(a.ctx))) return;
    attached.push(a);
    paintContext();
  }
  function paintEmpty(): void {
    if (conversation || turns.length) return;
    // offers are repainted on every selection change: redraw the ideas only when they change (a focused one keeps its focus)
    const ideas = suggestions(attached), key = [emptyTitle(attached), ...ideas].join('\n');
    if (key === emptyKey && thread.el.querySelector('.ai-empty')) return;
    emptyKey = key;
    thread.showEmpty(emptyTitle(attached), ideas, (t) => void submit(t));
    composer.placeholder(attached.length ? 'Ask about this…' : 'Ask anything about your library…');
  }
  /** A stored question's context as chips; a paper is named by its citation key once the library is known. */
  const chipsOf = (ctx: AskContext[] | undefined): Attached[] =>
    (ctx || []).map((c) => describe(c, c.kind === 'paper' ? data.items().find((i) => i.id === c.item)?.key : undefined));
  function paintTotal(): void {
    const sum: Usage = { input_tokens: 0, output_tokens: 0, cost_usd: 0, duration_ms: 0 };
    for (const t of turns) if (t.usage) {
      sum.input_tokens! += t.usage.input_tokens || 0; sum.output_tokens! += t.usage.output_tokens || 0;
      sum.cost_usd! += t.usage.cost_usd || 0; sum.duration_ms! += t.usage.duration_ms || 0;
    }
    const text = usageText(sum);
    usageBtn.hidden = !text;
    if (!text) total.hidden = true;
    total.textContent = text ? `This conversation: ${text}` : '';
  }
  const syncBusy = () => composer.setBusy(busy());

  function showHelp(message?: string): void {
    help.hidden = false;
    help.innerHTML = `<div class="ai-help-title">${esc(message || 'No companion is connected.')}</div>
      <p>The companion runs on a computer where your AI app is installed, and starts with it once set up. Open Synapse Companion there, or run</p>
      <pre class="ai-code">${esc(START_COMMAND)}</pre>
      <p>The first time, make a companion token in Settings → AI Access and follow its steps.</p>
      <button type="button" class="ai-btn" data-recheck>Check again</button>`;
  }
  async function check(): Promise<void> {
    status = await refreshAiStatus();
    if (!shown) return;
    composer.setAgents(status?.companions || [], locked);
    if (!status) showHelp('Cannot reach the AI service.');
    else if (!status.companions.length) showHelp();
    else help.hidden = true;
  }

  // ---- placement
  const view = () => ({ w: innerWidth, h: innerHeight });
  /** The popup's size and anchor corner (css/ai.css reads --ai-pop-w/-h and data-corner); clamped to the window. */
  function applyPopupBox(): void {
    root.dataset.corner = savedCorner();
    const s = clampPopup(savedPopup(), view());
    root.style.setProperty('--ai-pop-w', `${s.w}px`);
    root.style.setProperty('--ai-pop-h', `${s.h}px`);
  }
  /** The docked column's width, for the page that makes room for it (--ai-dock-w on the document). */
  function applyDockWidth(w = savedDock()): number {
    const c = clampDock(w, view());
    document.documentElement.style.setProperty('--ai-dock-w', `${c}px`);
    edge.setAttribute('aria-valuenow', String(c));
    edge.setAttribute('aria-valuemin', String(MIN_DOCK));
    edge.setAttribute('aria-valuemax', String(maxDock(view())));
    return c;
  }
  function applyPlacement(): void {
    if (!shown) return;
    const where = place(root, host.dock);
    const docked = where === 'dock';
    if (docked) { host.dock!.mount(root); applyDockWidth(); }
    edge.hidden = !docked;
    grip.hidden = docked;
    edge.dataset.side = host.dock?.side || 'right';
    applyPopupBox();
    placeBtn.hidden = !host.dock?.available() && !docked;
    placeBtn.innerHTML = icon(docked ? 'newWindow' : 'sidebar');
    const label = docked ? 'Move to a floating window' : 'Move to the sidebar';
    placeBtn.title = label;
    placeBtn.setAttribute('aria-label', label);
  }
  window.addEventListener('resize', applyPlacement);
  window.addEventListener(CORNER_EVENT, applyPopupBox);   // the button was moved: the popup opens from its corner

  // ---- resizing: the popup by its inner corner, the docked column by its inner edge
  const grip = document.createElement('div');
  grip.className = 'ai-grip';
  grip.setAttribute('aria-hidden', 'true');
  grip.title = 'Drag to resize';
  const edge = document.createElement('div');
  edge.className = 'ai-edge';
  edge.setAttribute('role', 'separator');
  edge.setAttribute('aria-orientation', 'vertical');
  edge.setAttribute('aria-label', 'Panel width: drag, or use the arrow keys, Home or End; double-click to reset');
  edge.tabIndex = 0;
  edge.title = 'Drag to change the width (double-click to reset)';
  root.append(grip, edge);
  /** Run ``onMove(dx, dy)`` while the pointer that pressed ``handle`` moves (mouse, pen or touch); ``onEnd`` when it ends. */
  function drag(handle: HTMLElement, onStart: () => void, onMove: (dx: number, dy: number) => void, onEnd: () => void): void {
    handle.addEventListener('pointerdown', (e) => {
      if (e.button > 0) return;
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      const x = e.clientX, y = e.clientY;
      onStart();
      const move = (ev: PointerEvent) => { if (ev.pointerId === e.pointerId) onMove(ev.clientX - x, ev.clientY - y); };
      const up = (ev: PointerEvent) => {
        if (ev.pointerId !== e.pointerId) return;
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', up);
        handle.removeEventListener('pointercancel', up);
        root.classList.remove('resizing');
        onEnd();
      };
      root.classList.add('resizing');
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', up);
      handle.addEventListener('pointercancel', up);
    });
  }
  let startSize: Size = savedPopup(), liveSize: Size = savedPopup();
  drag(grip, () => {
    const r = root.getBoundingClientRect();
    startSize = liveSize = { w: r.width, h: r.height };
  }, (dx, dy) => {
    liveSize = resizedPopup(startSize, root.dataset.corner as Corner, dx, dy, view());
    root.style.setProperty('--ai-pop-w', `${liveSize.w}px`);
    root.style.setProperty('--ai-pop-h', `${liveSize.h}px`);
  }, () => savePopup(liveSize));
  let startDock = DEFAULT_DOCK, liveDock = DEFAULT_DOCK;
  drag(edge, () => { startDock = liveDock = Math.round(root.getBoundingClientRect().width); }, (dx) => {
    liveDock = applyDockWidth(resizedDock(startDock, host.dock?.side || 'right', dx, view()));
  }, () => saveDock(liveDock));
  edge.addEventListener('dblclick', () => { saveDock(DEFAULT_DOCK); applyDockWidth(DEFAULT_DOCK); });
  edge.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 48 : 16, side = host.dock?.side || 'right';
    const grow = (e.key === 'ArrowLeft') === (side === 'right');
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const now = Math.round(root.getBoundingClientRect().width);
    const w = applyDockWidth(e.key === 'Home' ? MIN_DOCK : e.key === 'End' ? MAX_DOCK : now + (grow ? step : -step));
    saveDock(w);
  });

  // ---- views
  function showHistory(on: boolean): void {
    history.el.hidden = !on;
    thread.el.hidden = on;
    composer.el.hidden = on;
    q('[data-history]').classList.toggle('active', on);
    titleText.textContent = on ? 'Earlier conversations' : title || 'Ask AI';
    if (on) { void history.refresh(); history.focus(); } else composer.focus();
  }

  // ---- asking
  function follow_(t: Turn, id: string, ctl: AbortController): Promise<JobState> {
    return follow(id, (events: JobEvent[], state: JobState) => {
      for (const e of events) {
        if (e.type === 'text') { t.text += e.text; t.phase = 'writing'; }
        else if (e.type === 'tool') { t.tools.push(e.name); t.phase = 'thinking'; }
        else if (e.type === 'started') { if (t.phase !== 'writing') t.phase = 'thinking'; }
        else if (e.type === 'usage') t.usage = { input_tokens: e.input_tokens, output_tokens: e.output_tokens, cost_usd: e.cost_usd, duration_ms: e.duration_ms };
        else if (e.type === 'error') t.error = e.message;
      }
      t.state = state;
      if (state === 'running' && t.phase === 'queued') t.phase = 'thinking';
      if (isFinal(state)) finish(t, state, state === 'error' && !t.error ? 'The AI could not answer.' : '');
      else thread.schedule(t);
    }, ctl.signal);
  }

  function finish(t: Turn, state: JobState, error = ''): void {
    t.state = state;
    if (error) t.error = error;
    thread.draw(t);
    thread.toEnd();
    job = null;
    abort = null;
    syncBusy();
    paintTotal();
    if (shown && !showingHistory()) composer.focus();
  }

  async function submit(prompt: string): Promise<void> {
    if (busy()) return;
    if (showingHistory()) showHistory(false);
    help.hidden = true;
    const sent = attached;
    attached = [];
    composer.setText('');
    paintContext();
    const t = thread.addTurn(prompt, 'sending', 'queued', sent);
    turns.push(t);
    turn = t;
    syncBusy();
    const ctl = new AbortController();
    abort = ctl;
    const c = composer.choice();
    try {
      const reply = await ask(prompt, {
        context: sent.length ? sent.map((a) => limited(a.ctx)) : undefined, conversation,
        provider: conversation ? undefined : c.provider, companion: conversation ? undefined : c.companion, model: c.model, effort: c.effort,
      });
      // reset or another conversation took the panel meanwhile: leave its state alone
      if (ctl.signal.aborted) { cancelJob(reply.job).catch(() => {}); return; }
      if (!conversation) { conversation = reply.conversation; setTitle(prompt.replace(/\s+/g, ' ').slice(0, 60)); paintContext(); }
      locked = reply.provider;
      composer.setAgents(status?.companions || [], locked);
      job = reply.job;
      t.computer = reply.companion.name;
      t.phase = 'queued';
      thread.draw(t);
      await follow_(t, reply.job, ctl);
    } catch (e) {
      if (ctl.signal.aborted) return;
      if (e instanceof AiError && e.status === 409) {
        t.box.remove(); turns.pop(); turn = null; job = null; abort = null;
        attached = [...sent, ...attached].slice(0, MAX_ATTACHED);
        composer.setText(prompt); syncBusy(); showHelp(e.message); paintContext();
        return;
      }
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

  /** Stop following the answer in progress without stopping it: it stays in the history, running. */
  function detach(): void {
    abort?.abort();
    abort = null;
    job = null;
    turn = null;
    syncBusy();
  }

  /** Empty the panel for a new conversation. */
  function reset(): void {
    detach();
    attached = [];
    conversation = undefined;
    locked = null;
    turns = [];
    turn = null;
    thread.clear();
    setTitle('');
    composer.setAgents(status?.companions || [], null);
    paintContext();
    paintTotal();
    showHistory(false);
    void check();
    saveSoon();
  }

  async function openConversation(c: Conversation, preloaded?: Awaited<ReturnType<typeof getConversation>>): Promise<void> {
    let full;
    try { full = preloaded || await getConversation(c.id); } catch (e) { showHelp((e as Error).message); showHistory(false); return; }
    detach();
    conversation = c.id;
    locked = c.provider;
    turns = [];
    turn = null;
    thread.clear();
    setTitle(c.title);
    composer.setAgents(status?.companions || [], locked);
    paintContext();
    const msgs = full.messages;
    const pending = full.running && msgs.length && msgs[msgs.length - 1].role === 'user' ? msgs.pop()! : null;
    for (let i = 0; i < msgs.length; i++) {
      if (msgs[i].role !== 'user') continue;
      const a = msgs[i + 1]?.role === 'assistant' ? msgs[++i] : null;
      const q = msgs[i - (a ? 1 : 0)];
      const t = thread.addTurn(q.text, 'writing', (a?.state as JobState) || 'done', chipsOf(q.context));
      if (a) { t.text = a.text; t.tools = a.tools || []; t.usage = a.usage; t.error = a.error || ''; if (a.state === 'error' && !t.error) t.error = 'The AI could not answer.'; }
      turns.push(t);
      thread.draw(t);
    }
    if (pending && full.running) {
      const t = thread.addTurn(pending.text, 'thinking', 'running', chipsOf(pending.context));
      turns.push(t);
      turn = t;
      job = full.running;
      const ctl = new AbortController();
      abort = ctl;
      t.computer = c.companion?.name || '';
      void follow_(t, full.running, ctl).catch((e) => { if (!ctl.signal.aborted) finish(t, 'error', (e as Error).message); });
    }
    syncBusy();
    paintTotal();
    showHistory(false);
    thread.toEnd();
    if (!c.available) showHelp(`This conversation is on ${c.companion?.name || 'another computer'}, which is not connected.`);
    else help.hidden = true;
    saveSoon();
  }

  // ---- keeping the session: the draft and the conversation shown survive a reload on this device
  let saved = '';
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  function saveState(): void {
    clearTimeout(saveTimer);
    const s: Session = { text: composer.text(), caret: composer.caret(), context: attached.map((a) => a.ctx), ...(conversation ? { conversation } : {}) };
    const json = JSON.stringify(s);
    if (json === saved) return;
    saved = json;
    save(SESSION, s);
  }
  function saveSoon(): void { clearTimeout(saveTimer); saveTimer = setTimeout(saveState, 300); }
  window.addEventListener('pagehide', saveState);
  /** Put back what the last page left (the first time the panel opens after a load). */
  function restoreState(): void {
    const s = load<Partial<Session> | null>(SESSION, null);
    if (!s || typeof s !== 'object') return;
    const ctx = Array.isArray(s.context) ? s.context.filter((c) => c && typeof c === 'object' && ['note', 'paper', 'notepdf'].includes(c.kind)).slice(0, MAX_ATTACHED) : [];
    attached = chipsOf(ctx);
    if (typeof s.text === 'string') composer.setText(s.text.slice(0, 20000), typeof s.caret === 'number' ? s.caret : undefined);
    saved = JSON.stringify(s);
    if (typeof s.conversation === 'string') {
      const id = s.conversation;
      getConversation(id).then((full) => { if (!conversation && !turns.length) void openConversation(full, full); }, () => { /* gone: a new conversation */ });
    }
  }

  /** Hide the panel; everything in it stays as it is, and an answer on its way goes on. */
  let scrolled = 0;   // a hidden element forgets its scroll position
  function hide(): void {
    if (!shown) return;
    saveState();
    scrolled = thread.el.scrollTop;
    shown = false;
    if (root.dataset.placement === 'dock') host.dock?.unmount(root);
    root.hidden = true;
    window.dispatchEvent(new CustomEvent(PANEL_EVENT, { detail: false }));
    if (prevFocus?.isConnected) prevFocus.focus();
  }
  function show(): void {
    if (shown) return;
    shown = true;
    prevFocus = document.activeElement as HTMLElement | null;
    root.hidden = false;
    root.classList.add('opening');
    applyPlacement();
    window.dispatchEvent(new CustomEvent(PANEL_EVENT, { detail: true }));
    paintContext();
    void check();
    thread.el.scrollTop = scrolled;
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Escape' || !root.contains(document.activeElement)) return;
    if (document.querySelector('.menu')) return;   // a picker's menu closes itself, not the panel
    if (composer.closeMentions()) { e.preventDefault(); e.stopPropagation(); return; }
    e.preventDefault();
    e.stopPropagation();
    if (showingHistory()) showHistory(false); else hide();
  };

  thread.el.addEventListener('click', (e) => {
    const a = (e.target as Element).closest<HTMLAnchorElement>('a');
    if (!a) return;
    e.preventDefault();
    if (a.classList.contains('wikilink') && a.classList.contains('pdf')) {   // [[file.pdf#p=4]] in an answer
      const pdf = data.findPdf(a.dataset.path || a.dataset.note || '');
      if (!pdf || !host.openNotePdf) return;
      const page = pdfPage(a.dataset.heading || '');
      void viewerTarget(a.closest('blockquote') ? citeTargetInDom(a) : null).catch(() => ({} as { q?: string })).then((at) => host.openNotePdf!(pdf, page, at.q));
      return;
    }
    if (a.classList.contains('wikilink')) {
      const path = a.dataset.path || data.resolveNote(a.dataset.note || '');
      if (path) host.openNote?.(path);
      return;
    }
    if (a.classList.contains('cite')) {
      const it = cites.get(a.dataset.key || '');
      const pdfs = it?.pdfs?.length ? it.pdfs : it?.pdf ? [it.pdf] : [];
      const pdf = pdfs.find((p) => p.id === Number(a.dataset.att)) || pdfs[0];
      const page = a.dataset.page ? Number(a.dataset.page) : null;
      if (it && pdf) host.openPdf({ id: it.id, title: it.title }, pdf, page);
      else if (it && host.showItem) host.showItem(it.id);
      return;
    }
    if (/^https?:\/\//i.test(a.href)) window.open(a.href, '_blank', 'noopener,noreferrer');
  });
  help.addEventListener('click', (e) => { if ((e.target as Element).closest('[data-recheck]')) void check(); });
  q('[data-close]').addEventListener('click', hide);
  root.addEventListener('animationend', () => root.classList.remove('opening'));
  q('[data-new]').addEventListener('click', () => reset());
  q('[data-history]').addEventListener('click', () => showHistory(!showingHistory()));
  placeBtn.addEventListener('click', () => { remember(root.dataset.placement === 'dock' ? 'popup' : 'dock'); applyPlacement(); composer.focus(); });
  usageBtn.addEventListener('click', () => { total.hidden = !total.hidden; });
  titleBtn.addEventListener('click', () => {
    if (!conversation) return;
    const field = document.createElement('input');
    field.className = 'ai-rename';
    field.value = title;
    field.setAttribute('aria-label', 'Conversation title');
    titleBtn.hidden = true;
    titleBtn.after(field);
    field.focus();
    field.select();
    let done = false;
    const end = async (ok: boolean) => {
      if (done) return;
      done = true;
      const v = field.value.trim();
      field.remove();
      titleBtn.hidden = false;
      if (ok && v && v !== title && conversation) {
        try { await renameConversation(conversation, v); setTitle(v); } catch { /* unchanged */ }
      }
      composer.focus();
    };
    field.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); void end(true); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); void end(false); }
    });
    field.addEventListener('blur', () => void end(true));
  });
  document.addEventListener('keydown', onKey, true);

  // what is on screen changes while the panel is open; also seen when the pointer comes to the panel
  host.watch?.(paintContext);
  root.addEventListener('pointerenter', paintContext);

  root.classList.add('opening');
  document.body.appendChild(root);
  el = root;
  current = { add, focus: () => composer.focus(), show };
  window.dispatchEvent(new CustomEvent(PANEL_EVENT, { detail: true }));
  setTitle('');
  restoreState();
  applyPlacement();
  paintContext();
  void check();
  composer.focus();
}

/** The context as the server accepts it: long selections are cut. */
function limited(c: AskContext): AskContext {
  const cut = (s?: string) => (s ? s.slice(0, 8000) : s);
  return c.kind === 'note' ? { ...c, selection: cut(c.selection) } : { ...c, quote: cut(c.quote) };
}
