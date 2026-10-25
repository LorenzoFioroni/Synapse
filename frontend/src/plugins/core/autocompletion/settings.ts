// Autocompletion's part of its section in Settings: whether Ollama and the model are there (and what to do if
// not), the model (from those installed), the length, Ollama's address. Turning autocompletion on
// and off is the plugin's switch in Settings → Plugins.
import type { PluginContext } from '../../api.js';
import { icon } from '../../../icons.js';
import { toast } from '../../../ui/toast.js';
import { escapeHtml, isMac, modLabel } from '../../../util.js';
import type { AssistStatus, Source } from './source.js';

const altLabel = isMac ? '⌥' : 'Alt+';
const pull = (model: string) => `ollama pull ${model}`;
const saved = `<span class="saved-pill">${icon('check', 'icon-sm')} Saved</span>`;

function statusHtml(st: AssistStatus): string {
  if (st.ok) {
    return `<div class="ai-state ok">${icon('check', 'icon-sm')}<span>Ready: <span class="mono">${escapeHtml(st.model)}</span> in Ollama ${escapeHtml(st.version)}${st.fim ? ', fills in the middle.' : '.'}</span></div>
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
  if (st.reason === 'bad_url') steps.push('Correct Ollama’s address below.');
  steps.push('Then press Check Again.');
  // the steps below say how to install the model, so the message need not
  const message = st.message.replace(/\s*Install it with:.*$/, '');
  return `<div class="ai-state bad">${icon('alert', 'icon-sm')}<span>${escapeHtml(message)}</span></div>
    <ol class="ai-steps">${steps.map((s) => `<li>${s}</li>`).join('')}</ol>`;
}

/** Draw the section into ``el`` (each time Settings opens). */
export function mountSettings(p: PluginContext, el: HTMLElement, src: Source): () => void {
  let alive = true;
  let statusRequest = 0;
  let saveRequest = 0;
  el.innerHTML = `
    <p class="lead"><kbd>Tab</kbd> accepts a suggestion, <kbd>Esc</kbd> dismisses it, <kbd>${modLabel}→</kbd> takes one word,
      <kbd>${altLabel}\\</kbd> asks for one now. The model runs in <a href="https://ollama.com" target="_blank" rel="noopener">Ollama</a>.
      Turn autocompletion off with its switch in Plugins.</p>
    <div class="card">
      <div class="setting stack"><div style="display:flex;align-items:center;gap:12px"><div class="s-text"><div class="s-title">Status</div></div>
        <span class="spacer" style="flex:1"></span><button class="btn btn-sm" data-ai-check>${icon('refresh', 'icon-sm')} Check Again</button></div>
        <div data-ai-state><div class="s-desc">Checking Ollama…</div></div></div>
      <div class="setting stack"><div class="s-text"><div class="s-title">Model ${saved}</div>
        <div class="s-desc">A model that fills in the middle also sees the text after the cursor: <span class="mono">qwen2.5-coder:1.5b</span> is small and fast,
          <span class="mono">qwen2.5-coder:7b</span> writes better and needs about 8 GB of memory. <span data-ai-installed></span></div></div>
        <input class="input mono" type="text" list="ai-models" data-ai-model spellcheck="false" autocomplete="off"><datalist id="ai-models"></datalist></div>
      <div class="setting"><div class="s-text"><div class="s-title">Suggestion length</div><div class="s-desc">How far a suggestion goes.</div></div>
        <div class="s-control">${saved}<div class="segmented" data-ai-length><button data-v="line">Rest of the line</button><button data-v="paragraph">Paragraph</button></div></div></div>
      <div class="setting stack"><div class="s-text"><div class="s-title">Ollama address ${saved}</div>
        <div class="s-desc">Where Ollama listens; only addresses on this computer are accepted.</div></div>
        <input class="input mono" type="text" data-ai-url spellcheck="false" autocomplete="off" placeholder="http://127.0.0.1:11434"></div>
    </div>`;
  const model = el.querySelector<HTMLInputElement>('[data-ai-model]')!;
  const url = el.querySelector<HTMLInputElement>('[data-ai-url]')!;
  const length = el.querySelector<HTMLElement>('[data-ai-length]')!;
  const showValues = (v: Record<string, unknown>) => {
    model.value = String(v.model ?? '');
    url.value = String(v.url ?? '');
    length.querySelectorAll<HTMLElement>('button').forEach((b) => b.classList.toggle('active', b.dataset.v === v.length));
  };

  async function refresh(fresh = true): Promise<void> {
    const request = ++statusRequest;
    const box = el.querySelector<HTMLElement>('[data-ai-state]')!;
    box.innerHTML = '<div class="s-desc">Checking Ollama…</div>';
    try {
      const st = await p.backend.get<AssistStatus>('/status', { fresh: fresh ? 1 : undefined });
      if (!alive || request !== statusRequest) return;
      src.known(st);
      box.innerHTML = statusHtml(st);
      el.querySelector('#ai-models')!.innerHTML = st.models.map((m) => `<option value="${escapeHtml(m)}"></option>`).join('');
      el.querySelector('[data-ai-installed]')!.innerHTML = st.models.length
        ? `Installed: ${st.models.map((m) => `<button class="link-btn mono" data-ai-pick="${escapeHtml(m)}">${escapeHtml(m)}</button>`).join(', ')}`
        : '';
    } catch (e) { if (!alive || request !== statusRequest) return; box.innerHTML = `<div class="form-error">${escapeHtml((e as Error).message)}</div>`; }
  }

  async function save(changes: Record<string, unknown>, at: Element): Promise<void> {
    const request = ++saveRequest;
    ++statusRequest;   // a probe started with the old settings must not replace the new status
    try {
      const values = await p.saveSettings(changes);
      if (!alive || request !== saveRequest) return;
      showValues(values);
      const pill = at.closest('.setting')?.querySelector('.saved-pill');
      if (pill) { pill.classList.add('show'); setTimeout(() => pill.classList.remove('show'), 1200); }
      src.reset();
      refresh();
    } catch (e) { if (alive && request === saveRequest) toast((e as Error).message, { type: 'error' }); }
  }

  model.addEventListener('change', () => save({ model: model.value.trim() }, model));
  url.addEventListener('change', () => save({ url: url.value.trim() }, url));
  el.addEventListener('click', async (e) => {
    const t = e.target as Element;
    if (t.closest('[data-ai-check]')) { refresh(); return; }
    const seg = t.closest<HTMLElement>('[data-ai-length] button');
    if (seg) { save({ length: seg.dataset.v }, seg); return; }
    const pick = t.closest<HTMLElement>('[data-ai-pick]');
    if (pick) { model.value = pick.dataset.aiPick!; save({ model: model.value }, model); return; }
    const copy = t.closest<HTMLElement>('[data-ai-copy]');
    if (copy) {
      try { await navigator.clipboard.writeText(copy.dataset.aiCopy!); toast('Copied. Paste it in Terminal.', { type: 'success' }); } catch { /* no clipboard */ }
    }
  });
  p.settings().then((v) => { if (alive && saveRequest === 0) showValues(v); });
  refresh(false);
  return () => { alive = false; };
}
