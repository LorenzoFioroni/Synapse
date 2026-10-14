// Modal dialogs replacing window.alert/confirm/prompt.
import { escapeHtml, h } from '../util.js';

type KeyHandler = (e: KeyboardEvent) => void;
const stack: KeyHandler[] = [];

export type CloseDialog = (value?: unknown) => void;

export interface DialogButton {
  label: string;
  kind?: 'primary' | 'danger' | '';
  /** What the dialog resolves with when pressed (default: the label). */
  value?: unknown;
  /** Runs first; returning false keeps the dialog open. */
  onClick?: (close: CloseDialog, button: HTMLButtonElement) => unknown;
}

export interface DialogOptions {
  title: string;
  subtitle?: string;       // HTML
  wide?: boolean;
  render?: (body: HTMLElement, close: CloseDialog, buttons: HTMLButtonElement[]) => void;
  buttons?: DialogButton[];
  footerLeft?: Node | string | null;
  dismissable?: boolean;
}

/**
 * Open a modal. `render(body, close)` fills the body; `buttons` are
 * [{label, kind: 'primary'|'danger'|'', value, onClick?(close) }].
 * Resolves with the value of the button pressed (or null on Esc/backdrop).
 */
export function openDialog({ title, subtitle = '', wide = false, render, buttons = [], footerLeft = null, dismissable = true }: DialogOptions): Promise<any> {
  return new Promise((resolve) => {
    const back = h(`<div class="dialog-backdrop">
      <div class="dialog ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
        <div class="dialog-header"><h2 class="dialog-title">${escapeHtml(title)}</h2>
          ${subtitle ? `<p class="dialog-subtitle">${subtitle}</p>` : ''}</div>
        <div class="dialog-body"></div>
        <div class="dialog-footer"><div class="left"></div></div>
      </div></div>`);
    const dlg = back.querySelector<HTMLElement>('.dialog')!;
    const body = back.querySelector<HTMLElement>('.dialog-body')!;
    const footer = back.querySelector<HTMLElement>('.dialog-footer')!;
    const prevFocus = document.activeElement as HTMLElement | null;
    let done = false;
    const close: CloseDialog = (value = null) => {
      if (done) return;
      done = true;
      back.remove();
      stack.splice(stack.indexOf(onKey), 1);
      document.removeEventListener('keydown', onKey, true);
      if (prevFocus && prevFocus.focus) prevFocus.focus();
      resolve(value);
    };
    const btnEls = buttons.map((b) => {
      const el = h<HTMLButtonElement>(`<button class="btn ${b.kind === 'primary' ? 'btn-primary' : ''} ${b.kind === 'danger' ? 'btn-danger solid' : ''}">${escapeHtml(b.label)}</button>`);
      el.onclick = async () => {
        if (b.onClick) {
          const r = await b.onClick(close, el);
          if (r === false) return;
        }
        close(b.value === undefined ? b.label : b.value);
      };
      footer.appendChild(el);
      return el;
    });
    if (footerLeft) footer.querySelector('.left')!.append(footerLeft);
    if (!buttons.length) footer.remove();
    const onKey: KeyHandler = (e) => {
      const target = e.target as HTMLElement;
      if (stack[stack.length - 1] !== onKey) return;
      if (e.key === 'Escape' && dismissable) { e.preventDefault(); e.stopPropagation(); close(null); }
      if (e.key === 'Enter' && !e.shiftKey && target.tagName !== 'TEXTAREA' && !target.closest('.no-enter')) {
        const primary = btnEls.find((_, i) => buttons[i].kind === 'primary' || buttons[i].kind === 'danger');
        if (primary && !primary.disabled) { e.preventDefault(); primary.click(); }
      }
    };
    stack.push(onKey);
    document.addEventListener('keydown', onKey, true);
    back.addEventListener('mousedown', (e) => { if (e.target === back && dismissable) close(null); });
    render?.(body, close, btnEls);
    document.body.appendChild(back);
    const first = dlg.querySelector<HTMLElement>('[autofocus], input, textarea, select') || btnEls[btnEls.length - 1];
    setTimeout(() => first?.focus(), 30);
  });
}

export function confirmDialog(message: string, { title = 'Are you sure?', confirm = 'OK', danger = false, detail = '' } = {}): Promise<boolean> {
  return openDialog({
    title,
    render: (b) => { b.innerHTML = `<p style="margin:0">${escapeHtml(message)}</p>${detail ? `<p class="muted" style="margin:8px 0 0">${escapeHtml(detail)}</p>` : ''}`; },
    buttons: [{ label: 'Cancel', value: false }, { label: confirm, kind: danger ? 'danger' : 'primary', value: true }],
  }).then((v) => v === true);
}

/** Ask for a line of text; resolves with it (trimmed), or null when cancelled. */
export function promptDialog(label: string, { title = '', value = '', placeholder = '', confirm = 'OK', hint = '' } = {}): Promise<string | null> {
  let input: HTMLInputElement;
  return openDialog({
    title: title || label,
    render: (b) => {
      b.innerHTML = `${title ? `<label class="field-label">${escapeHtml(label)}</label>` : ''}
        <input class="input" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(value)}">
        ${hint ? `<div class="field-hint">${escapeHtml(hint)}</div>` : ''}`;
      input = b.querySelector('input')!;
      setTimeout(() => input.select(), 40);
    },
    buttons: [{ label: 'Cancel', value: null }, { label: confirm, kind: 'primary', onClick: () => {} }],
  }).then((v) => (v === confirm ? input.value.trim() : null));
}
