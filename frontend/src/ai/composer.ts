// The question box of the Ask AI panel: the context chip, the text (with the "[@" / "[[" list of
// papers and notes, mentions.ts), the agent / model / effort pickers and Send / Stop. A picker is a
// compact button, an icon and a short value, that opens the app's menu of what the connected
// companions advertise; the row never wraps, the pickers shrink and truncate before Send does. The
// last choice is remembered per device. Strings from the server (names, labels, ids) are only ever
// set as text.
import { icon } from '../icons.js';
import { type MenuEntry, openMenu } from '../ui/menu.js';
import { type AiCompanion, type AiProvider, type AskContext, MAX_PROMPT } from './client.js';
import { load, save } from './prefs.js';
import { contextLabel } from './context.js';
import { type MentionData, attachMentions } from './mentions.js';

export interface Choice { companion?: string; provider?: string; model?: string; effort?: string }
interface Agent { companion: string; computer: string; provider: AiProvider }

const KEY = 'zc.ai.choice';
const DEFAULT = '';
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export interface Composer {
  el: HTMLElement;
  focus(): void;
  setContext(c: AskContext | undefined, removable: boolean): void;
  setBusy(busy: boolean): void;
  /** The connected agents; ``locked`` is the provider of the open conversation (then it cannot change). */
  setAgents(companions: AiCompanion[], locked: string | null): void;
  choice(): Choice;
  setText(text: string): void;
  placeholder(text: string): void;
  /** Close the list of papers and notes if it is open; true if it was. */
  closeMentions(): boolean;
}

/** The effort as a level glyph: ``of`` bars, ``on`` of them lit (none for the default). */
function levels(on: number, of: number): string {
  const n = Math.min(Math.max(of, 2), 5), w = 2, gap = 1.6, left = 8 - (n * w + (n - 1) * gap) / 2;
  let bars = '';
  for (let i = 0; i < n; i++) {
    const h = 4 + (i * 8) / (n - 1);
    bars += `<rect x="${(left + i * (w + gap)).toFixed(2)}" y="${(13 - h).toFixed(2)}" width="${w}" height="${h.toFixed(2)}" rx=".8" fill="currentColor" stroke="none"${i < on ? '' : ' opacity=".28"'}/>`;
  }
  return `<svg class="icon" viewBox="0 0 16 16" aria-hidden="true">${bars}</svg>`;
}

export function createComposer(h: {
  onSend(text: string): void; onStop(): void; onDropContext(): void; mentions: MentionData;
}): Composer {
  const el = document.createElement('form');
  el.className = 'ai-composer';
  el.innerHTML = `
    <div class="ai-chiprow" hidden></div>
    <textarea class="ai-input" rows="2" aria-label="Question" maxlength="${MAX_PROMPT}"></textarea>
    <div class="ai-controls">
      <button type="button" class="ai-pick ai-fixed" data-mention title="Cite a paper or note ([@ or [[)" aria-label="Cite a paper or note">${icon('at')}</button>
      <div class="ai-picks">
        <button type="button" class="ai-pick" data-agent aria-haspopup="menu" hidden></button>
        <button type="button" class="ai-pick" data-model aria-haspopup="menu" hidden></button>
        <button type="button" class="ai-pick ai-fixed" data-effort aria-haspopup="menu" hidden></button>
      </div>
      <button type="button" class="ai-btn ai-send" data-stop title="Stop" aria-label="Stop" hidden>${icon('stop')}</button>
      <button type="submit" class="ai-btn primary ai-send" data-send title="Send (Enter)" aria-label="Send">${icon('send')}</button>
    </div>`;
  const q = <T extends HTMLElement>(s: string) => el.querySelector<T>(s)!;
  const input = q<HTMLTextAreaElement>('.ai-input'), chipRow = q('.ai-chiprow');
  const agentBtn = q<HTMLButtonElement>('[data-agent]'), modelBtn = q<HTMLButtonElement>('[data-model]'), effortBtn = q<HTMLButtonElement>('[data-effort]');
  const stop = q<HTMLButtonElement>('[data-stop]'), send = q<HTMLButtonElement>('[data-send]');
  const mentions = attachMentions(input, el, h.mentions);
  let agents: Agent[] = [];          // what can be chosen (one provider when a conversation is open)
  let many = false;                  // more than one agent exists, so the picker shows
  let manyComputers = false;
  let lockedTo: string | null = null;
  let agent = '', model = DEFAULT, effort = DEFAULT;
  let pressClosed = false;           // the press on a picker closed its own menu: the click must not reopen it

  const keyOf = (a: Agent) => `${a.companion}|${a.provider.id}`;
  const current = (): Agent | undefined => agents.find((a) => keyOf(a) === agent);

  /** Keep the model and effort when the new agent offers them too, else back to the default. */
  function validate(): void {
    const p = current()?.provider;
    if (model && !(p?.models || []).some((m) => m.id === model)) model = DEFAULT;
    if (effort && !(p?.efforts || []).includes(effort)) effort = DEFAULT;
  }

  function paint(): void {
    const a = current(), p = a?.provider;
    const set = (b: HTMLElement, name: string, value: string, tip: string, html: string) => {
      b.innerHTML = html;
      b.title = tip;
      b.setAttribute('aria-label', `${name}: ${value}`);
    };
    const val = (text: string) => { const s = document.createElement('span'); s.className = 'ai-pick-val'; s.textContent = text; return s; };
    if (a) {
      const label = manyComputers ? `${a.provider.label} on ${a.computer}` : a.provider.label;
      set(agentBtn, 'Agent', label, lockedTo ? `Agent: ${label}. It cannot change inside a conversation; start a new one to use another.` : `Agent: ${label}. Click to change.`, icon('bot'));
      agentBtn.append(val(a.provider.label));
    }
    agentBtn.hidden = !many;
    agentBtn.classList.toggle('locked', !!lockedTo);
    agentBtn.setAttribute('aria-disabled', String(!!lockedTo));

    const models = p?.models || [], efforts = p?.efforts || [];
    const m = models.find((x) => x.id === model);
    const mLabel = m ? m.label || m.id : 'Default';
    set(modelBtn, 'Model', mLabel, `Model: ${m ? mLabel : 'default'}. Click to change.`, icon('chip'));
    modelBtn.append(val(mLabel));
    modelBtn.hidden = !models.length;
    const eLabel = effort ? cap(effort) : 'Default';
    set(effortBtn, 'Effort', eLabel, `Effort: ${effort ? eLabel.toLowerCase() : 'default'}. Click to change.`, levels(effort ? efforts.indexOf(effort) + 1 : 0, efforts.length));
    effortBtn.hidden = !efforts.length;
  }

  function restore(): void {
    const saved = load<Choice>(KEY, {});
    const i = agents.findIndex((a) => a.provider.id === saved.provider);
    agent = agents[i >= 0 ? i : 0] ? keyOf(agents[i >= 0 ? i : 0]) : '';
    model = saved.model || DEFAULT;
    effort = saved.effort || DEFAULT;
    if (saved.provider !== current()?.provider.id) { model = DEFAULT; effort = DEFAULT; }
    validate();
  }
  const remember = () => save(KEY, { provider: current()?.provider.id, model, effort });

  /** Open the menu under a picker with the current value checked and focused for the keys. */
  function pick(btn: HTMLElement, entries: MenuEntry[]): void {
    if (pressClosed) { pressClosed = false; return; }
    const menu = openMenu(entries, btn);
    btn.setAttribute('aria-expanded', 'true');
    const done = new MutationObserver(() => { if (!menu.isConnected) { btn.removeAttribute('aria-expanded'); done.disconnect(); } });
    done.observe(document.body, { childList: true });
    const on = [...menu.querySelectorAll<HTMLElement>('.menu-item')].find((b) => b.querySelector('.check')?.textContent);
    on?.classList.add('focused');
    on?.scrollIntoView({ block: 'nearest' });
  }
  const choose = (apply: () => void) => () => { apply(); validate(); paint(); remember(); input.focus(); };
  const items = (values: [string, string][], now: string, set: (v: string) => void): MenuEntry[] =>
    [['', 'Default'] as [string, string], ...values].map(([v, l]) => ({ label: l, checked: v === now, run: choose(() => set(v)) }));

  for (const b of [agentBtn, modelBtn, effortBtn]) {
    // menu.ts closes any open menu on a press elsewhere; seen first here, it tells whether this press only closes
    b.addEventListener('pointerdown', () => { pressClosed = !!document.querySelector('.menu') && b.getAttribute('aria-expanded') === 'true'; });
  }
  agentBtn.addEventListener('click', () => {
    if (lockedTo) return;
    const several = new Set(agents.map((a) => a.companion)).size > 1;
    const entries: MenuEntry[] = [];
    let computer = '';
    for (const a of agents) {
      if (several && a.computer !== computer) { computer = a.computer; entries.push({ section: a.computer }); }
      entries.push({ label: a.provider.label, checked: keyOf(a) === agent, run: choose(() => { agent = keyOf(a); }) });
    }
    pick(agentBtn, entries);
  });
  modelBtn.addEventListener('click', () => pick(modelBtn, items((current()?.provider.models || []).map((m) => [m.id, m.label || m.id]), model, (v) => { model = v; })));
  effortBtn.addEventListener('click', () => pick(effortBtn, items((current()?.provider.efforts || []).map((e) => [e, cap(e)]), effort, (v) => { effort = v; })));

  const submit = () => { const t = input.value.trim(); if (t) { input.value = ''; mentions.close(); h.onSend(t); } };
  el.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
  stop.addEventListener('click', () => h.onStop());
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !e.defaultPrevented) { e.preventDefault(); submit(); }
  });
  chipRow.addEventListener('click', (e) => { if ((e.target as Element).closest('[data-drop]')) h.onDropContext(); });
  const at = q<HTMLButtonElement>('[data-mention]');
  at.addEventListener('pointerdown', (e) => e.preventDefault());   // the caret stays in the question
  at.addEventListener('click', () => mentions.startCite());

  return {
    el,
    focus: () => input.focus(),
    setContext(c, removable) {
      chipRow.hidden = !c;
      chipRow.replaceChildren();
      if (!c) return;
      const chip = document.createElement('span');
      chip.className = 'ai-chip';
      chip.title = contextLabel(c);
      const text = document.createElement('span');
      text.className = 'ai-chip-text';
      text.textContent = contextLabel(c);
      chip.append(text);
      if (removable) chip.insertAdjacentHTML('beforeend', `<button type="button" class="ai-icon" data-drop title="Ask without it" aria-label="Remove context">${icon('close', 'icon-sm')}</button>`);
      chipRow.append(chip);
    },
    setBusy(busy) { stop.hidden = !busy; send.hidden = busy; },
    setAgents(companions, locked) {
      const was = agent;
      const all = companions.flatMap((c) => c.providers.map((p) => ({ companion: c.id, computer: c.name, provider: p })));
      agents = all.filter((a) => !locked || a.provider.id === locked);
      many = all.length > 1;
      manyComputers = new Set(all.map((a) => a.companion)).size > 1;
      lockedTo = locked;
      if (agents.some((a) => keyOf(a) === was)) validate(); else restore();
      paint();
    },
    choice() {
      const a = current();
      return { companion: a?.companion, provider: a?.provider.id, model: model || undefined, effort: effort || undefined };
    },
    setText: (t) => { input.value = t; },
    placeholder: (t) => { input.placeholder = t; },
    closeMentions: () => mentions.close(),
  };
}
