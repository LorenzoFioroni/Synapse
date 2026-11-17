// What the panel remembers on this device (placement, last agent/model/effort). localStorage can
// be blocked or full: every access is guarded and the panel works without it.
export function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch { return fallback; }
}

export function save(key: string, value: unknown): void {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* not remembered */ }
}
