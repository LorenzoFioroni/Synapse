// Reload the extension when its folder was replaced by a newer packaged build.
//
// The server's extension zip (GET /api/companion/extension, fetched by the companion into the
// folder that was loaded with "Load unpacked") holds a build.json {"build": "<hash>"}. Chrome keeps
// running the old code until the extension is reloaded, so the build seen at load time is recorded
// (onInstalled fires on every load and reload) and compared with the file on disk when the worker
// starts and when the popup opens. A repository checkout has no build.json and never reloads.

const LOADED = 'loadedBuild';       // the build that was loaded
const TRIED = 'reloadedForBuild';   // the build a reload was already asked for: never twice

/** The build of the packaged files, or null for a checkout (or an unreadable file). */
async function packagedBuild() {
  try {
    const res = await fetch(chrome.runtime.getURL('build.json'), { cache: 'no-store' });
    if (!res.ok) return null;
    const { build } = await res.json();
    return typeof build === 'string' && build ? build : null;
  } catch {
    return null;
  }
}

/** Remember which build is running now (call from chrome.runtime.onInstalled). */
export async function recordBuild() {
  const build = await packagedBuild();
  if (build) await chrome.storage.local.set({ [LOADED]: build });
  else await chrome.storage.local.remove([LOADED, TRIED]);
}

/** Reload when the files on disk are another build than the one that was loaded. */
export async function reloadIfUpdated() {
  const build = await packagedBuild();
  if (!build) return;
  const saved = await chrome.storage.local.get([LOADED, TRIED]);
  // Nothing recorded yet (onInstalled has not run), or the same build: nothing to do. A build a
  // reload was already asked for is not asked again, so a failing reload cannot loop.
  if (!saved[LOADED] || saved[LOADED] === build || saved[TRIED] === build) return;
  await chrome.storage.local.set({ [TRIED]: build });
  chrome.runtime.reload();
}
