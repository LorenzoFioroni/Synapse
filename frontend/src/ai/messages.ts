// The conversation as shown: each question with its answer, the phase while it is being made, the
// tools used, a usage line and a copy button.
//
// The answer is untrusted model output (the question is the user's own text, but is treated the
// same, since a pasted one can carry anything). It goes through the notes' renderer (markdown-it with raw
// HTML off, KaTeX), so [@key, p. N] citations and math work, but only after it has been built in an
// inert <template> (images there are not fetched) and filtered: an image whose resolved address is
// not the app's own /api/notes/ is replaced by its alt text (a remote image would leak the
// conversation in its URL), links open in a new tab with no opener, and nothing is ever assigned
// as raw HTML to the page. Until the renderer has loaded the text is set with textContent.
// Strings from the server (the computer's name, tool names) are set as text.
import { icon } from '../icons.js';
import type { Renderer } from '../views/notes/render.js';
import type { JobState, Usage } from './client.js';

export type Phase = 'sending' | 'queued' | 'thinking' | 'writing';

export interface Turn {
  question: string;
  text: string;
  tools: string[];
  phase: Phase;
  state: JobState;
  error: string;
  usage?: Usage;
  computer: string;
  box: HTMLElement;
}

const THROTTLE_MS = 120;

const kilo = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));
export function usageText(u: Usage | undefined): string {
  if (!u) return '';
  const parts: string[] = [];
  if (u.input_tokens != null || u.output_tokens != null) parts.push(`${kilo(u.input_tokens || 0)} in · ${kilo(u.output_tokens || 0)} out`);
  if (u.cost_usd) parts.push(`$${u.cost_usd < 0.01 ? u.cost_usd.toFixed(4) : u.cost_usd.toFixed(2)}`);
  if (u.duration_ms) parts.push(`${(u.duration_ms / 1000).toFixed(u.duration_ms < 10000 ? 1 : 0)} s`);
  return parts.join(' · ');
}

const toolName = (n: string) => n.replace(/^mcp__synapse__/, '').replace(/_/g, ' ');

function statusText(t: Turn): string {
  if (t.error) return t.error;
  if (t.state === 'cancelled') return 'Stopped.';
  switch (t.state === 'done' ? 'done' : t.phase) {
    case 'sending': return 'Sending…';
    case 'queued': return t.computer ? `Waiting for ${t.computer}…` : 'Waiting for your computer…';
    case 'thinking': return t.tools.length ? 'Reading…' : 'Thinking…';
    default: return '';
  }
}

export class Thread {
  readonly el = document.createElement('div');
  private renderer: Renderer | null = null;
  private turns = new Map<HTMLElement, Turn>();
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    this.el.className = 'ai-thread';
    this.el.setAttribute('aria-live', 'polite');
    this.el.addEventListener('click', (e) => {
      const b = (e.target as Element).closest<HTMLElement>('[data-copy]');
      const t = b && this.turns.get(b.closest<HTMLElement>('.ai-turn')!);
      if (!t || !b) return;
      const mark = (name: string) => { b.innerHTML = icon(name, 'icon-sm'); };
      navigator.clipboard?.writeText(t.text).then(() => mark('check'), () => mark('alert'))
        .finally(() => setTimeout(() => mark('copy'), 1500));
    });
  }

  setRenderer(r: Renderer): void { this.renderer = r; this.paintAll(); }
  clear(): void { this.el.replaceChildren(); this.turns.clear(); }

  /** The friendly start of an empty conversation, with things to ask. */
  showEmpty(title: string, ideas: string[], pick: (text: string) => void): void {
    this.clear();
    const box = document.createElement('div');
    box.className = 'ai-empty';
    box.innerHTML = `<span class="ai-empty-icon">${icon('sparkle')}</span><div class="ai-empty-title"></div>
      <div class="ai-empty-text">Answers use your library and notes, through your own AI app.</div><div class="ai-ideas"></div>`;
    box.querySelector('.ai-empty-title')!.textContent = title;
    const list = box.querySelector('.ai-ideas')!;
    for (const idea of ideas) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ai-idea';
      b.textContent = idea;
      b.addEventListener('click', () => pick(idea));
      list.append(b);
    }
    this.el.append(box);
  }

  addTurn(question: string, phase: Phase = 'sending', state: JobState = 'queued'): Turn {
    this.el.querySelector('.ai-empty')?.remove();
    const box = document.createElement('div');
    box.className = 'ai-turn';
    box.innerHTML = `<div class="ai-q"></div>
      <div class="ai-a"><div class="ai-a-head"><span class="ai-a-who">${icon('sparkle', 'icon-sm')} AI</span>
        <button type="button" class="ai-icon ai-copy" data-copy title="Copy the answer" aria-label="Copy the answer" hidden>${icon('copy', 'icon-sm')}</button></div>
        <div class="ai-tools" hidden></div><div class="ai-answer prose"></div>
        <div class="ai-status" role="status"></div><div class="ai-usage-line"></div></div>`;
    const t: Turn = { question, text: '', tools: [], phase, state, error: '', computer: '', box };
    this.turns.set(box, t);
    this.drawQuestion(t);
    this.el.append(box);
    this.draw(t);
    this.toEnd();
    return t;
  }

  toEnd(): void { this.el.scrollTop = this.el.scrollHeight; }

  /** Draw soon (at most every 120 ms): streamed text arrives in small pieces. */
  schedule(t: Turn): void {
    if (this.timer) return;
    this.timer = setTimeout(() => { this.timer = undefined; this.draw(t); this.toEnd(); }, THROTTLE_MS);
  }

  paintAll(): void { for (const t of this.turns.values()) { this.drawQuestion(t); this.draw(t); } }

  /** The question as the user wrote it, with its [@key] citations and [[note]] links drawn as in an answer. */
  private drawQuestion(t: Turn): void {
    const q = t.box.querySelector<HTMLElement>('.ai-q')!;
    if (this.renderer) this.fill(q, this.renderer.renderInline(t.question));
    else q.textContent = t.question;
  }

  /** Put rendered Markdown into ``into``: built in an inert template and filtered before it is inserted, an image there is not fetched. */
  private fill(into: HTMLElement, html: string): void {
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    for (const img of tpl.content.querySelectorAll('img')) {
      let local = false;
      try {
        const u = new URL(img.getAttribute('src') || '', location.href);   // ../ segments resolved
        local = u.origin === location.origin && u.pathname.startsWith('/api/notes/');
      } catch { /* not an address */ }
      if (!local) img.replaceWith(document.createTextNode(img.getAttribute('alt') || ''));
    }
    for (const a of tpl.content.querySelectorAll<HTMLAnchorElement>('a[href]')) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    into.replaceChildren(tpl.content);
  }

  draw(t: Turn): void {
    clearTimeout(this.timer);
    this.timer = undefined;
    const body = t.box.querySelector<HTMLElement>('.ai-answer')!;
    if (this.renderer) {
      this.fill(body, this.renderer.render(t.text));
    } else body.textContent = t.text;
    body.classList.toggle('plain', !this.renderer);
    const running = t.state === 'queued' || t.state === 'running';
    t.box.classList.toggle('streaming', running);
    const status = t.box.querySelector<HTMLElement>('.ai-status')!;
    status.textContent = statusText(t);
    status.classList.toggle('error', !!t.error);
    const tools = t.box.querySelector<HTMLElement>('.ai-tools')!;
    tools.hidden = !t.tools.length;
    if (tools.childElementCount !== Math.min(t.tools.length, 8)) {
      tools.replaceChildren(...t.tools.slice(-8).map((n) => {
        const s = document.createElement('span');
        s.className = 'ai-tool';
        s.textContent = toolName(n);
        return s;
      }));
    }
    t.box.querySelector<HTMLElement>('.ai-usage-line')!.textContent = usageText(t.usage);
    t.box.querySelector<HTMLElement>('[data-copy]')!.hidden = running || !t.text;
  }
}
