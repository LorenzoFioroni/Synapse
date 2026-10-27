// Due dates: reading what people type ("fri", "tomorrow", "27 oct", "in 3 days") and showing a
// date as people say it ("Today", "Fri", "Tue 27 Oct"). No DOM (tested in tests/js/tasks.test.mjs).
// Dates are local calendar days written YYYY-MM-DD, as the backend stores them.

const DAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const pad = (n: number) => String(n).padStart(2, '0');
export const isoDate = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const todayIso = (now: Date = new Date()): string => isoDate(now);

/** The local date of a YYYY-MM-DD string (noon, so daylight-saving changes never move the day). */
export function dateOf(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d, 12);
}
const addDays = (d: Date, n: number) => { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, 12); return x; };

/** Days from today to ``iso`` (negative: in the past). */
export function daysUntil(iso: string, now: Date = new Date()): number {
  return Math.round((dateOf(iso).getTime() - dateOf(isoDate(now)).getTime()) / 864e5);
}

/** A day name or its start ("fri", "thurs") → 0–6, or -1. */
function dayIndex(word: string): number {
  if (word.length < 2) return -1;
  return DAYS.findIndex((d) => d.startsWith(word));
}
function monthIndex(word: string): number {
  if (word.length < 3) return -1;
  return MONTHS.findIndex((m) => m.startsWith(word));
}

/** A valid date for day/month (and year, or the next time that day comes). */
function dayMonth(day: number, month: number, year: number | null, now: Date): string | null {
  if (month < 0 || month > 11 || day < 1 || day > 31) return null;
  let y = year ?? now.getFullYear();
  if (year != null && year < 100) y = 2000 + year;
  let d = new Date(y, month, day, 12);
  if (d.getMonth() !== month) return null;   // 31 Feb
  if (year == null && daysUntil(isoDate(d), now) < 0) d = new Date(y + 1, month, day, 12);
  return isoDate(d);
}

/**
 * What a typed date means, as YYYY-MM-DD, or null when it is not a date. Understands today,
 * tomorrow, yesterday, a weekday ("fri": the next Friday, today included), "next week" (Monday),
 * "next month" (the 1st), "in 3 days" / "3d" / "2w", "27 oct", "oct 27", "27/10", "27.10.2026"
 * (day first) and 2026-10-27.
 */
export function parseDue(text: string, now: Date = new Date()): string | null {
  const t = text.trim().toLowerCase().replace(/\s+/g, ' ').replace(/[,.]$/, '');
  if (!t) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) {
    const [y, m, d] = t.split('-').map(Number);
    return dayMonth(d, m - 1, y, now);
  }
  if (['today', 'tod', 'tdy', 'now'].includes(t)) return isoDate(now);
  if (['tomorrow', 'tom', 'tmr', 'tmrw'].includes(t)) return isoDate(addDays(now, 1));
  if (t === 'yesterday') return isoDate(addDays(now, -1));
  if (t === 'next week' || t === 'nw') return isoDate(addDays(now, ((8 - now.getDay()) % 7) || 7));
  if (t === 'next month') return isoDate(new Date(now.getFullYear(), now.getMonth() + 1, 1, 12));
  let m = /^(?:in )?(\d{1,3}) ?(d|days?|w|weeks?|m|months?)$/.exec(t);
  if (m) {
    const n = Number(m[1]);
    if (m[2].startsWith('d')) return isoDate(addDays(now, n));
    if (m[2].startsWith('w')) return isoDate(addDays(now, 7 * n));
    return isoDate(new Date(now.getFullYear(), now.getMonth() + n, now.getDate(), 12));
  }
  m = /^(next |this )?([a-z]+)$/.exec(t);
  if (m) {
    const di = dayIndex(m[2]);
    if (di >= 0) {
      let ahead = (di - now.getDay() + 7) % 7;
      if (m[1] === 'next ' && ahead === 0) ahead = 7;
      return isoDate(addDays(now, ahead));
    }
  }
  m = /^(\d{1,2}) ?([a-z]+)\.?(?: (\d{2,4}))?$/.exec(t);   // 27 oct (2026)
  if (m && monthIndex(m[2]) >= 0) return dayMonth(Number(m[1]), monthIndex(m[2]), m[3] ? Number(m[3]) : null, now);
  m = /^([a-z]+)\.? (\d{1,2})(?:,? (\d{2,4}))?$/.exec(t);   // oct 27 (2026)
  if (m && monthIndex(m[1]) >= 0) return dayMonth(Number(m[2]), monthIndex(m[1]), m[3] ? Number(m[3]) : null, now);
  m = /^(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\.?$/.exec(t);   // 27/10, 27.10.2026
  if (m) return dayMonth(Number(m[1]), Number(m[2]) - 1, m[3] ? Number(m[3]) : null, now);
  return null;
}

/** "Today", "Tomorrow", "Yesterday", "Fri" (this week), "Tue 27 Oct", "Tue 27 Oct 2027". */
export function dueLabel(iso: string, now: Date = new Date()): string {
  const n = daysUntil(iso, now);
  const d = dateOf(iso);
  if (n === 0) return 'Today';
  if (n === 1) return 'Tomorrow';
  if (n === -1) return 'Yesterday';
  if (n > 1 && n < 7) return WD[d.getDay()];
  return `${WD[d.getDay()]} ${d.getDate()} ${MO[d.getMonth()]}${d.getFullYear() !== now.getFullYear() ? ` ${d.getFullYear()}` : ''}`;
}

/** How urgent an open task's date is: 'overdue', 'today', 'soon' (within two days) or ''. */
export function dueState(iso: string | null, now: Date = new Date()): '' | 'overdue' | 'today' | 'soon' {
  if (!iso) return '';
  const n = daysUntil(iso, now);
  return n < 0 ? 'overdue' : n === 0 ? 'today' : n <= 2 ? 'soon' : '';
}

/** Days of the month view for a date picker: 6 weeks starting on Monday. */
export function monthGrid(year: number, month: number): { iso: string; day: number; inMonth: boolean }[] {
  const first = new Date(year, month, 1, 12);
  const start = addDays(first, -((first.getDay() + 6) % 7));
  return Array.from({ length: 42 }, (_, i) => {
    const d = addDays(start, i);
    return { iso: isoDate(d), day: d.getDate(), inMonth: d.getMonth() === month };
  });
}
export const monthName = (month: number): string => MONTHS[month][0].toUpperCase() + MONTHS[month].slice(1);

export interface QuickTask { title: string; due: string | null }

/**
 * Quick add: "Draft the outlook fri" → title "Draft the outlook", due Friday. A date is read only
 * from the last words, so titles that mention a day keep it ("Call Ann about Friday's talk"); and
 * only when ``dates`` is on (the forms that have their own date field turn it off).
 */
export function parseQuick(text: string, now: Date = new Date(), dates = true): QuickTask {
  const words = text.trim().split(/\s+/).filter(Boolean);
  let due: string | null = null;
  if (dates) for (const n of [3, 2, 1]) {
    if (words.length <= n) continue;   // the title keeps at least one word
    const d = parseDue(words.slice(-n).join(' '), now);
    if (d) { due = d; words.splice(-n); break; }
  }
  if (due) {
    const last = words[words.length - 1]?.toLowerCase();
    if (last && ['by', 'on', 'due', 'until'].includes(last) && words.length > 1) words.pop();
  }
  return { title: words.join(' '), due };
}
