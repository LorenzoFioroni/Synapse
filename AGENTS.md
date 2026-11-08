# Working on Synapse

Synapse is a local reference manager: Flask + SQLite in `backend/`, TypeScript in
`frontend/src/` (generated `frontend/js/` is not committed).
Use [Clara](.agents/roles/clara.md) as the default coordinator; delegate only when useful.
Shared roles and Codex/Claude adapters: [.agents/README.md](.agents/README.md).

## Always

- Keep changes small and focused. Reuse existing patterns; avoid unrelated refactoring,
  dependencies and abstractions. Comments explain why, not history.
- Keep the UI lean, modern and consistent. Reuse components and design tokens; preserve
  responsive layouts, keyboard access, touch support and both themes.
- This repository is public. Never commit credentials, real environment files, library data,
  private hostnames or personal configuration. Use safe examples; keep secrets out of logs,
  API responses and frontend code. Preserve authentication and administrative confirmations.
- Preserve standalone execution and Docker on remote servers behind Cloudflare Tunnel.
  Support independent instances on one host: configurable paths/addresses, separate data and
  authentication, and no unnecessary published ports or localhost assumptions.
- Offline first: no CDN or new mandatory runtime network dependency. Frontend libraries are
  vendored with licence, version and build instructions in `frontend/vendor/README.md`.
- Preserve data layouts, database schemas, notes and extension/MCP APIs. Migrate deliberately;
  never rewrite users' data. Keep API handlers thin and logic in its owning component.
- Update the matching `docs/` page with behaviour or structure changes, and `README.md` for
  user-facing changes. Split modules by responsibility, not just size.

## Read only what the task needs

| Task | Guidance |
|---|---|
| Package boundaries | [Architecture](docs/architecture.md) |
| UI | [Frontend](docs/frontend.md#styling); [phone](docs/phone.md) |
| Data or migrations | [Storage](docs/storage.md); the affected subsystem page |
| Deployment or access | [Docker](docs/docker.md), [remote access](docs/remote-access.md); [updates](docs/updates.md) for updater changes |
| Plugins | [Plugin authoring](docs/plugin-authoring.md) |
| Setup and verification | [Development](docs/development.md); [testing](docs/testing.md) for test details |

## Work and finish

Small tasks need only relevant files and checks. Set up tools once per environment; reinstall
only when dependencies change. During editing, run affected tests. At completion, run
`python tools/check.py` for code/workflow changes and browser checklists for UI touched.
Review the diff, available CI results and CodeQL/security feedback; fix issues caused by the
change and report any remaining failures or unavailable checks accurately.
