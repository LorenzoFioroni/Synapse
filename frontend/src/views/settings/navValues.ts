// The current value beside some navigation entries (shown on phones, where the navigation is the
// first list you see: General · System, Backups · Daily…).
import { state } from '../../store.js';

const LABELS: Record<string, (s: typeof state.settings) => string> = {
  general: (s) => ({ system: 'System', light: 'Light', dark: 'Dark' })[s.theme] || '',
  notes: (s) => (s.notes_enabled ? '' : 'Off'),
  viewer: (s) => (s.pdf_open_in === 'browser' ? 'Browser tabs' : 'Synapse tabs'),
  citekeys: (s) => (state.settingsMeta.citekeyPresets || []).find((p) => p.pattern === s.citekey_pattern)?.label.replace(/\s*\(.*$/, '') || 'Custom',
  backups: (s) => ({ off: 'Off', daily: 'Daily', weekly: 'Weekly' })[s.backup_interval] || '',
};

export function paintNavValues(view: ParentNode): void {
  for (const [id, label] of Object.entries(LABELS)) {
    const el = view.querySelector<HTMLElement>(`.settings-nav [data-nav="${id}"] [data-nav-value]`);
    if (el) el.textContent = label(state.settings);
  }
}
