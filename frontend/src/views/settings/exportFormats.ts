// Default-export choices follow running providers; keep a disabled saved choice for reactivation.
import { api } from '../../api.js';
import { state } from '../../store.js';
import { escapeHtml } from '../../util.js';

const requests = new WeakMap<HTMLElement, number>();
export async function refreshExportFormats(page: HTMLElement): Promise<void> {
  const request = (requests.get(page) || 0) + 1;
  requests.set(page, request);
  try {
    const { exports: formats } = await api.formats();
    if (!page.isConnected || requests.get(page) !== request) return;
    const select = page.querySelector<HTMLSelectElement>('[data-key="export_format"]');
    if (!select) return;
    const saved = state.settings.export_format;
    const missing = !formats.some((f) => f.key === saved);
    select.innerHTML = (missing ? `<option value="${escapeHtml(saved)}" disabled>${escapeHtml(saved)} (provider off)</option>` : '')
      + formats.map((f) => `<option value="${escapeHtml(f.key)}">${escapeHtml(f.label)}</option>`).join('');
    select.value = saved;
    select.disabled = !formats.length;
  } catch { /* keep the existing preference while the server is unreachable */ }
}
