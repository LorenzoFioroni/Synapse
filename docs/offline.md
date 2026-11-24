# Offline use (Chrome)

A service worker (`frontend/sw.js`, registered by `frontend/src/offline.ts`) keeps Synapse usable on a bad connection.

- **Reading.** Every page, script and data request that succeeds is saved in the browser's Cache Storage. Pages and data come from the network first (6 s timeout) and fall back to the saved copy. PDFs come from the saved copy first. **A PDF is available offline once it has been opened online** (the viewer now fetches whole files, `disableRange`, so they can be saved).
- **Annotations.** A create, change or delete that can't reach the server gets a `202` reply straight away. It is queued in IndexedDB (`synapse-offline` / `outbox`) and also applied to the saved annotation list, so a reload while offline still shows it. The queue is sent in order when the browser comes back online, after any successful request, and every 30 s while items wait. If the server refuses an entry (4xx other than 401/403), that entry is dropped and reported.
- A pill at the bottom says when you're offline and how many changes are waiting.
- `window.synapseOffline.keep([...urls])` saves a list of URLs ahead of time.
- Not covered: notes editing offline, library edits, iOS/Safari storage limits.
