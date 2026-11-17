// Previous conversations: search, continue, rename, delete. They are stored on the server
// (GET /api/ai/conversations); a conversation can be continued while the computer it started on is
// connected. Titles and names come from the server and are only set as text.
import { icon } from '../icons.js';
import { type Conversation, deleteConversation, listConversations, renameConversation } from './client.js';

export interface History {
  el: HTMLElement;
  /** Load the list again (the search box's text is kept). */
  refresh(): Promise<void>;
  focus(): void;
}

function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const same = d.toDateString() === new Date().toDateString();
  return same ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function createHistory(h: { onOpen(c: Conversation): void; onRenamed(id: string, title: string): void; onDeleted(id: string): void; label(provider: string): string }): History {
  const el = document.createElement('div');
  el.className = 'ai-history';
  el.innerHTML = `<div class="ai-search">${icon('search', 'icon-sm')}<input type="search" placeholder="Search conversations" aria-label="Search conversations" spellcheck="false" autocomplete="off"></div>
    <div class="ai-convs" role="list"></div>`;
  const input = el.querySelector<HTMLInputElement>('input')!;
  const list = el.querySelector<HTMLElement>('.ai-convs')!;
  let items: Conversation[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;

  function row(c: Conversation): HTMLElement {
    const r = document.createElement('div');
    r.className = 'ai-conv';
    r.setAttribute('role', 'listitem');
    r.dataset.id = c.id;
    r.innerHTML = `<button type="button" class="ai-conv-main" data-open><span class="ai-conv-title"></span>
        <span class="ai-conv-meta"></span></button>
      <span class="ai-conv-actions"><button type="button" class="ai-icon" data-rename title="Rename" aria-label="Rename">${icon('edit', 'icon-sm')}</button>
        <button type="button" class="ai-icon" data-delete title="Delete" aria-label="Delete">${icon('trash', 'icon-sm')}</button></span>`;
    r.querySelector('.ai-conv-title')!.textContent = c.title || 'Untitled';
    const meta = [h.label(c.provider), c.companion?.name, when(c.updated)].filter(Boolean).join(' · ');
    r.querySelector('.ai-conv-meta')!.textContent = meta;
    if (c.running) r.querySelector('.ai-conv-meta')!.append(' · answering…');
    if (!c.available) {
      const n = document.createElement('span');
      n.className = 'ai-conv-off';
      n.textContent = 'not connected';
      r.querySelector('.ai-conv-meta')!.append(' · ', n);
    }
    return r;
  }

  function paint(): void {
    list.replaceChildren(...items.map(row));
    if (!items.length) {
      const e = document.createElement('div');
      e.className = 'ai-conv-empty';
      e.textContent = input.value ? 'No conversation matches.' : 'No conversations yet. Ask something and it is kept here.';
      list.append(e);
    }
  }

  async function refresh(): Promise<void> {
    try { items = (await listConversations(input.value.trim())).conversations; paint(); }
    catch (e) {
      list.replaceChildren();
      const m = document.createElement('div');
      m.className = 'ai-conv-empty';
      m.textContent = (e as Error).message;
      list.append(m);
    }
  }

  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(refresh, 200); });
  list.addEventListener('click', async (e) => {
    const target = e.target as Element;
    const r = target.closest<HTMLElement>('.ai-conv');
    const c = items.find((x) => x.id === r?.dataset.id);
    if (!r || !c) return;
    if (target.closest('[data-open]')) h.onOpen(c);
    else if (target.closest('[data-rename]')) {
      const title = r.querySelector<HTMLElement>('.ai-conv-title')!;
      const field = document.createElement('input');
      field.className = 'ai-rename';
      field.value = c.title;
      field.setAttribute('aria-label', 'Conversation title');
      title.replaceWith(field);
      field.focus();
      field.select();
      let done = false;
      const finish = async (ok: boolean) => {
        if (done) return;
        done = true;
        const v = field.value.trim();
        if (ok && v && v !== c.title) {
          try { await renameConversation(c.id, v); c.title = v; h.onRenamed(c.id, v); } catch { /* shown unchanged */ }
        }
        paint();
      };
      field.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); void finish(true); }
        else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); void finish(false); }
      });
      field.addEventListener('blur', () => void finish(true));
    } else if (target.closest('[data-delete]')) {
      const actions = r.querySelector<HTMLElement>('.ai-conv-actions')!;
      actions.innerHTML = '<button type="button" class="ai-btn danger" data-yes>Delete</button><button type="button" class="ai-btn" data-no>Keep</button>';
      actions.querySelector<HTMLElement>('[data-no]')!.focus();
    } else if (target.closest('[data-no]')) { paint(); input.focus(); }
    else if (target.closest('[data-yes]')) {
      try { await deleteConversation(c.id); items = items.filter((x) => x.id !== c.id); h.onDeleted(c.id); } catch { /* kept */ }
      paint();
      input.focus();
    }
  });
  return { el, refresh, focus: () => input.focus() };
}
