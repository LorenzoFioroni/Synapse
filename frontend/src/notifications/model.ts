/** Serializable history. Each entry has its own key so windows cannot overwrite one another. */
export type Level = 'info' | 'success' | 'warning' | 'error';
export type Status = 'running' | 'done' | 'cancelled' | 'interrupted';
export interface Notice {
  id: string; message: string; level: Level; status: Status; time: number; read: boolean;
  desktop?: boolean; source?: string; target?: string; owner?: string; done?: number; total?: number;
}
export const PREFIX = 'synapse.notification.';
export const LIMIT = 100;
export interface Persistence { read(): Notice[]; write(n: Notice): void; remove(id: string): void }
export class History {
  entries: Notice[] = [];
  constructor(private disk: Persistence, private changed: () => void = () => {}) { this.reload(); }
  reload(): void {
    this.entries = this.disk.read().filter(valid).sort((a, b) => b.time - a.time || b.id.localeCompare(a.id));
    this.changed();
  }
  get unread(): number { return this.entries.filter(n => !n.read).length; }
  put(n: Notice): void {
    this.entries = [n, ...this.entries.filter(x => x.id !== n.id)].sort((a, b) => b.time - a.time || b.id.localeCompare(a.id));
    this.disk.write(n);
    for (const old of this.entries.filter(x => x.status !== 'running').slice(LIMIT)) this.remove(old.id, false);
    this.changed();
  }
  remove(id: string, announce = true, allowRunning = false): void {
    if (!allowRunning && this.entries.find(n => n.id === id)?.status === 'running') return;
    this.entries = this.entries.filter(n => n.id !== id); this.disk.remove(id);
    if (announce) this.changed();
  }
  markRead(id?: string): void {
    this.entries = this.entries.map(n => {
      if (n.read || (id && n.id !== id)) return n;
      const read = { ...n, read: true }; this.disk.write(read); return read;
    });
    this.changed();
  }
  clear(): void { for (const n of [...this.entries]) this.remove(n.id, false); this.changed(); }
  interrupt(owner: string): void {
    for (const n of [...this.entries]) if (n.status === 'running' && n.owner === owner)
      this.put({ ...n, status: 'interrupted', level: 'warning', read: false, message: `${n.message} — page reloaded; check the result before retrying` });
  }
  recover(liveOwners: Set<string>, candidates: Set<string>): void {
    for (const n of [...this.entries]) {
      if (n.status !== 'running' || !candidates.has(n.id) || (n.owner && liveOwners.has(n.owner))) continue;
      this.put({ ...n, status: 'interrupted', level: 'warning', read: false,
        message: `${n.message} — progress tracking interrupted; check the result before retrying` });
    }
  }
}
function valid(n: Notice): boolean {
  return !!n && typeof n.id === 'string' && typeof n.message === 'string' && Number.isFinite(n.time)
    && typeof n.read === 'boolean' && ['info', 'success', 'warning', 'error'].includes(n.level)
    && ['running', 'done', 'cancelled', 'interrupted'].includes(n.status)
    && (n.target === undefined || typeof n.target === 'string') && (n.source === undefined || typeof n.source === 'string');
}

/** Notification ids also work on paired HTTP/LAN pages without secure-context UUID support. */
export function newId(): string {
  return globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}
