import type { History } from './model.js';

const OWNER_LOCK = 'synapse.notification.owner.';

/** Browser-owned locks survive timer throttling but are released when a window crashes. */
export function monitorOwners(owner: string, history: History, unavailable: () => void): boolean {
  if (!navigator.locks) return false;
  const locks = navigator.locks;
  const check = async () => {
    const candidates = new Set(history.entries.filter(n => n.status === 'running').map(n => n.id));
    const snapshot = await locks.query();
    const live = new Set([owner]);
    for (const lock of [...(snapshot.held ?? []), ...(snapshot.pending ?? [])]) {
      if (lock.name?.startsWith(OWNER_LOCK)) live.add(lock.name.slice(OWNER_LOCK.length));
    }
    // Read completed results before recovering: another window may have finished during query().
    history.reload();
    history.recover(live, candidates);
  };
  const refresh = () => { void check().catch(unavailable); };
  void locks.request(OWNER_LOCK + owner, async () => {
    refresh();
    window.setInterval(refresh, 10_000);
    window.addEventListener('focus', refresh);
    // The browser releases this lock even when pagehide never runs.
    await new Promise<void>(() => {});
  }).catch(unavailable);
  return true;
}
