// Settings → Autocompletion: whether Ollama and the model are there (and what to do if not), the
// model (from those installed), Ollama's address. The switch and the length are plain rows.
import { api } from '../../api.js';
import { icon } from '../../icons.js';
import { emit, state } from '../../store.js';
import { toast } from '../../ui/toast.js';
import type { AssistStatus } from '../../types.js';
import { escapeHtml } from '../../util.js';
import { assistStatusKnown } from '../notes/assist.js';
import { flashSaved } from './page.js';

const pull = (model: string) => `ollama pull ${model}`;

function statusHtml(st: AssistStatus): string {
  if (st.ok) {
    return `<div class="ai-state ok">${icon('check', 'icon-sm')}<span>Ready: <span class="mono">${escapeHtml(st.model)}</span> in Ollama ${escapeHtml(st.version)}${st.fim ? ', fills in the middle.' : '.'}
      ${st.enabled ? '' : 'Turn on “Suggest as you type” to use it.'}</span></div>
      ${st.fim ? '' : `<div class="s-desc">${escapeHtml(st.message.replace(/^Ready\.\s*/, ''))}</div>`}`;
  }
  const steps: string[] = [];
  if (st.reason === 'unreachable') {
    steps.push('Install Ollama from <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com</a> and start it (on a Mac it runs in the menu bar).');
  }
  if (st.reason === 'unreachable' || st.reason === 'no_model') {
    steps.push(`Install the model in Terminal: <span class="mono">${escapeHtml(pull(st.model))}</span>
      <button class="btn btn-ghost btn-sm" data-ai-copy="${escapeHtml(pull(st.model))}">${icon('copy', 'icon-sm')} Copy</button>`);
  }
  if (st.reason === 'unsupported') steps.push('Choose another model below.');
  steps.push('Then press Check Again.');
  // the steps below say how to install the model, so the message need not
  const message = st.message.replace(/\s*Install it with:.*$/, '');
  return `<div class="ai-state bad">${icon('alert', 'icon-sm')}<span>${escapeHtml(message)}</span></div>
    <ol class="ai-steps">${steps.map((s) => `<li>${s}</li>`).join('')}</ol>`;
}

/** Check Ollama and the model, and show the answer in the open Settings page ``view``. */
export async function refreshAssist(view: HTMLElement, { fresh = true } = {}): Promise<void> {
  const box = view.querySelector<HTMLElement>('[data-ai-state]');
  const list = view.querySelector<HTMLDataListElement>('#ai-models');
  if (!box || !list) return;
  box.innerHTML = '<div class="s-desc">Checking Ollama…</div>';
  try {
    const st = await api.assistStatus({ fresh });
    assistStatusKnown(st);
    if (!view.isConnected) return;
    box.innerHTML = statusHtml(st);
    list.innerHTML = st.models.map((m) => `<option value="${escapeHtml(m)}"></option>`).join('');
    const installed = view.querySelector<HTMLElement>('[data-ai-installed]')!;
    installed.innerHTML = st.models.length
      ? `Installed: ${st.models.map((m) => `<button class="link-btn mono" data-ai-pick="${escapeHtml(m)}">${escapeHtml(m)}</button>`).join(', ')}`
      : '';
  } catch (e) { box.innerHTML = `<div class="form-error">${escapeHtml((e as Error).message)}</div>`; }
}

export function bindAssist(view: HTMLElement): void {
  const model = view.querySelector<HTMLInputElement>('[data-ai-model]')!;
  const url = view.querySelector<HTMLInputElement>('[data-ai-url]')!;
  const err = view.querySelector<HTMLElement>('[data-ai-error]')!;
  const save = async (changes: Record<string, string>, el: HTMLElement) => {
    err.hidden = true;
    try {
      const res = await api.saveSettings(changes);
      state.settings = res.settings;
      url.value = res.settings.notes_ai_url;   // as the server cleaned it
      emit('settings');
      flashSaved(el.closest('.setting'));
      refreshAssist(view);
    } catch (e) { err.textContent = (e as Error).message; err.hidden = false; }
  };
  model.addEventListener('change', () => save({ notes_ai_model: model.value.trim() }, model));
  url.addEventListener('change', () => save({ notes_ai_url: url.value.trim() }, url));
  view.querySelector('#set-assist')!.addEventListener('click', async (e) => {
    const t = e.target as Element;
    if (t.closest('[data-ai-check]')) { refreshAssist(view); return; }
    const pick = t.closest<HTMLElement>('[data-ai-pick]');
    if (pick) { model.value = pick.dataset.aiPick!; save({ notes_ai_model: model.value }, model); return; }
    const copy = t.closest<HTMLElement>('[data-ai-copy]');
    if (copy) {
      try { await navigator.clipboard.writeText(copy.dataset.aiCopy!); toast('Copied. Paste it in Terminal.', { type: 'success' }); } catch { /* no clipboard */ }
    }
  });
}
