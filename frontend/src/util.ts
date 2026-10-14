// Small, dependency-free helpers shared by the UI modules.

const HTML_ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };

export function escapeHtml(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

export interface Debounced<A extends unknown[]> {
  (...args: A): void;
  /** Run now (with these arguments) instead of after the delay. */
  flush(...args: A): void;
  cancel(): void;
}

export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number): Debounced<A> {
  let t: ReturnType<typeof setTimeout> | undefined;
  const d = ((...args: A) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); }) as Debounced<A>;
  d.flush = (...args: A) => { clearTimeout(t); fn(...args); };
  d.cancel = () => clearTimeout(t);
  return d;
}

/** Create an element from an HTML string (single root). The caller names its type when it is not
 * a plain HTMLElement, e.g. ``h<HTMLInputElement>('<input>')``. */
export function h<T extends HTMLElement = HTMLElement>(html: string): T {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild as T;
}

export function formatDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const opts: Intl.DateTimeFormatOptions = { year: 'numeric', month: 'short', day: 'numeric' };
  if (withTime) Object.assign(opts, { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString(undefined, opts);
}

export function formatBytes(n: number | null | undefined): string {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function plural(n: number, word: string, pluralWord?: string): string {
  return `${n} ${n === 1 ? word : (pluralWord || word + 's')}`;
}

export function shortAuthors(authors: string | null | undefined): string {
  const fams = String(authors || '').split(';').map((a) => a.split(',')[0].trim()).filter(Boolean);
  if (!fams.length) return '';
  if (fams.length === 1) return fams[0];
  if (fams.length === 2) return `${fams[0]} and ${fams[1]}`;
  return `${fams[0]} et al.`;
}

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
export const modKey = (e: KeyboardEvent | MouseEvent): boolean => (isMac ? e.metaKey : e.ctrlKey);
export const modLabel = isMac ? '⌘' : 'Ctrl+';

/** A value saved with ``store`` (JSON in localStorage), or ``fallback``. */
export function storage<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : JSON.parse(v);
  } catch { return fallback; }
}
export function store(key: string, value: unknown): void {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}

export async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    const ok = document.execCommand('copy'); ta.remove(); return ok;
  }
}

export function downloadBlob(blob: Blob, filename: string): void {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/* --------------------------------------------------------- word diff */
function tokens(s: unknown): string[] { return String(s ?? '').match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) || []; }

/** Word-level diff of two texts as HTML: ``old`` with <del> marks, ``new`` with <ins> marks. */
export function diffWords(a: string, b: string): { old: string; new: string } {
  const A = tokens(a), B = tokens(b);
  if (A.length * B.length > 250000) return { old: `<del>${escapeHtml(a)}</del>`, new: `<ins>${escapeHtml(b)}</ins>` };
  const n = A.length, m = B.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let i = 0, j = 0, o = '', nw = '', da: string[] = [], ib: string[] = [];
  const flush = (arr: string[], tag: string) => (arr.length ? `<${tag}>${escapeHtml(arr.join(''))}</${tag}>` : '');
  while (i < n || j < m) {
    if (i < n && j < m && A[i] === B[j]) {
      o += flush(da, 'del'); nw += flush(ib, 'ins'); da = []; ib = [];
      o += escapeHtml(A[i]); nw += escapeHtml(B[j]); i++; j++;
    } else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) ib.push(B[j++]);
    else da.push(A[i++]);
  }
  o += flush(da, 'del'); nw += flush(ib, 'ins');
  return { old: o, new: nw };
}
