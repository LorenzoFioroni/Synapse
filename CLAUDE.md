# Working on Synapse

Synapse is a local reference manager: a Flask + SQLite backend (`backend/`) and a TypeScript
browser frontend (`frontend/src`, compiled to `frontend/js`, which is not committed). Start with
[`docs/architecture.md`](docs/architecture.md). It is the map of the code and says where new code
goes.

## Rules

- **Docs go with code.** Every change that adds a feature, or changes behaviour or structure,
  updates the matching page in `docs/` (and `README.md` for anything user-facing) in the same
  commit. If no page fits, extend `docs/architecture.md` or add a page and link it from there.
- **Keep the structure.**
  - New code goes in the package or module that `docs/architecture.md` assigns to its area.
  - HTTP endpoints only parse requests and shape responses. The logic lives outside
    `backend/api/`.
  - Split a module when it grows a second responsibility instead of letting it sprawl.
- **Offline first.**
  - The app must run without a network. Use a library only if it works offline once installed
    (Python) or vendored under `frontend/vendor/` (frontend, with its licence, version and build
    script listed in `frontend/vendor/README.md`).
  - Never load anything from a CDN. `tests/test_offline.py` enforces this.
- **Don't break users' data.** The `data/` layout, the database schemas (migrate, never rewrite),
  the notes format and the HTTP API used by the browser extension and the MCP server are
  contracts. Change them only deliberately, with a migration.
- **Comments explain why**, not history. Put the issue or PR reference in the commit message, not
  the code.

## Checks before committing

```bash
pip install -r requirements-dev.txt
(cd frontend && npm ci && npm run check)   # type-check the frontend (TypeScript in frontend/src)
ruff check .                 # lint (ruff check --fix . fixes most findings)
ruff format .                # format; CI runs ruff format --check .
python -m pytest -q          # includes the JavaScript unit tests when Node is installed
```

The browser checklists in `tests/e2e/` are run by hand (`python tests/e2e/<name>_checklist.py`,
which needs `pip install playwright`). Run the ones that cover UI you changed.

## Work in progress

The maintainability restructure (#51) follows [`docs/maintainability-plan.md`](docs/maintainability-plan.md).
