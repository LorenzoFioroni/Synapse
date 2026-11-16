// The question box of the Ask AI panel: the context chip, the text, the agent / model / effort
// selectors and Send / Stop. The selectors show only what the connected companions advertise; the
// last choice is remembered per device. Strings from the server (names, labels, ids) are only ever
// set as text.
import { icon } from '../icons.js';
import { type AiCompanion, type AiProvider, type AskContext, MAX_PROMPT } from './client.js';
import { load, save } from './prefs.js';
import { contextLabel } from './context.js';

export interface Choice { companion?: string; provider?: string; model?: string; effort?: string }
interface Agent { companion: string; computer: string; provider: AiProvider }

const KEY = 'zc.ai.choice';
const DEFAULT = '';

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
}

function option(value: string, label: string): HTMLOptionElement {
  const o = document.createElement('option');
  o.value = value;
  o.textContent = label;
  return o;
}

export function createComposer(h: { onSend(text: string): void; onStop(): void; onDropContext(): void }): Composer {
  const el = document.createElement('form');
  el.className = 'ai-composer';
  el.innerHTML = `
    <div class="ai-chiprow" hidden></div>
    <textarea class="ai-input" rows="2" aria-label="Question" maxlength="${MAX_PROMPT}"></textarea>
    <div class="ai-controls">
      <select class="ai-select" data-agent aria-label="Agent" hidden></select>
      <select class="ai-select" data-model aria-label="Model" hidden></select>
      <select class="ai-select" data-effort aria-label="Effort" hidden></select>
      <span class="ai-grow"></span>
      <button type="button" class="ai-btn" data-stop hidden>${icon('close', 'icon-sm')} Stop</button>
      <button type="submit" class="ai-btn primary" data-send title="Send (Enter)">Send</button>
    </div>`;
  const q = <T extends HTMLElement>(s: string) => el.querySelector<T>(s)!;
  const input = q<HTMLTextAreaElement>('.ai-input'), chipRow = q('.ai-chiprow');
  const agent = q<HTMLSelectElement>('[data-agent]'), model = q<HTMLSelectElement>('[data-model]'), effort = q<HTMLSelectElement>('[data-effort]');
  const stop = q<HTMLButtonElement>('[data-stop]'), send = q<HTMLButtonElement>('[data-send]');
  let agents: Agent[] = [];

  const current = (): Agent | undefined => agents.find((a) => `${a.companion}|${a.provider.id}` === agent.value);
  function fillChoices(): void {
    const p = current()?.provider;
    const fill = (sel: HTMLSelectElement, items: [string, string][], title: string) => {
      const keep = sel.value;
      sel.replaceChildren(option(DEFAULT, `${title}: default`), ...items.map(([v, l]) => option(v, l)));
      if (items.some(([v]) => v === keep)) sel.value = keep;
      sel.hidden = items.length === 0;
    };
    fill(model, (p?.models || []).map((m) => [m.id, m.label || m.id]), 'Model');
    fill(effort, (p?.efforts || []).map((e) => [e, e.charAt(0).toUpperCase() + e.slice(1)]), 'Effort');
  }
  function restore(): void {
    const saved = load<Choice>(KEY, {});
    const i = agents.findIndex((a) => a.provider.id === saved.provider);
    if (i >= 0) agent.value = `${agents[i].companion}|${agents[i].provider.id}`;
    fillChoices();
    if (saved.provider === current()?.provider.id) {
      if (saved.model && [...model.options].some((o) => o.value === saved.model)) model.value = saved.model;
      if (saved.effort && [...effort.options].some((o) => o.value === saved.effort)) effort.value = saved.effort;
    }
  }
  const remember = () => save(KEY, { provider: current()?.provider.id, model: model.value, effort: effort.value });
  agent.addEventListener('change', () => { fillChoices(); remember(); });
  model.addEventListener('change', remember);
  effort.addEventListener('change', remember);

  const submit = () => { const t = input.value.trim(); if (t) { input.value = ''; h.onSend(t); } };
  el.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
  stop.addEventListener('click', () => h.onStop());
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  chipRow.addEventListener('click', (e) => { if ((e.target as Element).closest('[data-drop]')) h.onDropContext(); });

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
      const was = agent.value;
      agents = companions.flatMap((c) => c.providers.map((p) => ({ companion: c.id, computer: c.name, provider: p })))
        .filter((a) => !locked || a.provider.id === locked);
      const manyComputers = new Set(agents.map((a) => a.companion)).size > 1;
      agent.replaceChildren(...agents.map((a) => option(`${a.companion}|${a.provider.id}`, manyComputers ? `${a.provider.label} · ${a.computer}` : a.provider.label)));
      if (agents.some((a) => `${a.companion}|${a.provider.id}` === was)) agent.value = was; else restore();
      fillChoices();
      agent.hidden = agents.length < 2;
      agent.disabled = !!locked;
    },
    choice() {
      const a = current();
      return { companion: a?.companion, provider: a?.provider.id, model: model.value || undefined, effort: effort.value || undefined };
    },
    setText: (t) => { input.value = t; },
    placeholder: (t) => { input.placeholder = t; },
  };
}
