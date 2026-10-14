# Maintainability plan (#51)

This is the working plan for issue #51. It is delivered as **one pull request with many small
commits**, and each commit leaves the tests green and the docs up to date. This file is deleted in
the last commit, once everything it lists is done or has moved into the permanent docs.

## Decisions

- **Restructure depth.** The work regroups packages, splits files and makes targeted
  simplifications where the tests cover them. Behaviour, the HTTP API, the database format and the
  `data/` layout stay unchanged. A deeper redesign is considered only at the end, in step 6.
- **Frontend in TypeScript.** The frontend gets a build step, using esbuild (already used for the
  vendored bundles) and `tsc` for type checking. Built output is still served from disk, so the app
  stays offline.
- **Scope.** Everything we wrote is in scope: `backend/`, `frontend/` (except `vendor/`),
  `browser_extension/`, `tests/`, `tools/`, `server.py` and `mcp_server.py`. Third-party libraries
  are out of scope.
- **Keeping docs current.** This is a convention in `CLAUDE.md` plus a checklist in the PR
  template. There is no CI check for it.
- **Libraries.** A library is welcome only if it works offline once installed or vendored.
  `tests/test_offline.py` guards the frontend.

## Steps

1. **Baseline and tooling** (done):
   - `pyproject.toml` (pytest and ruff) and `requirements-dev.txt`.
   - Lint fixes.
   - The offline test.
   - CI (`.github/workflows/ci.yml`).
   - `CLAUDE.md`, the PR template, and the `docs/architecture.md` skeleton.
2. **Backend restructure** (done):
   - Domain packages: `library/`, `pdf/`, `online/`, `references/`, `access/`.
   - Shared helpers extracted (`library/identifiers.py`, `online/http.py`), which removed the import
     cycles.
   - `mcp/tools.py`, `references`, `pdf/render.py` and `library/annotations.py` split along their
     seams.
   - The routes defined inside `create_app` moved into blueprints, and `zclone` renamed.
   - Issue numbers dropped from backend comments.
   - Lint rules tightened (import sorting, `raise … from`), and the code formatted with ruff
     format.
   - The package layering is documented and enforced by `tests/test_layers.py`.
3. **Frontend: TypeScript and module splits** (done):
   - A `frontend/` package builds `src/` (TypeScript) to `js/` with esbuild; `server.py` rebuilds
     it when needed, and CI runs `tsc --noEmit` (strict, no unused locals).
   - Every module is typed, with types for the API payloads (`src/types.ts`); no `@ts-nocheck`
     is left.
   - The inline script of `viewer.html` is a module (`viewer/viewer.ts`).
   - `notesView`, `itemlist`, `viewer/annotations` and `settingsView` are split along their seams.
   - The browser extension stays plain JavaScript (Chrome loads it without a build) and is
     type-checked from JSDoc by the same `npm run check`; it has a browser checklist now.
   - Issue numbers dropped from the frontend's and the tests' comments.
4. **Documentation** (done): a page per subsystem, linked from `docs/architecture.md`, the data
   flow of a typical change, `docs/adding-a-feature.md` and `docs/testing.md`.
5. **Further polish** (done):
   - pre-commit hooks (`.pre-commit-config.yaml`, local hooks: no network).
   - `tests/e2e/run.py` runs the browser checklists, each on its own port.
   - Coverage reporting (`pytest --cov`, in the CI log).
   - mypy over the backend, in CI and pre-commit.
6. **Redesign review (last).** Look over the restructured code for places where a deeper redesign
   would pay off, for example the layering, the service wiring or the frontend state model. Write
   up the candidates with cost, risk and benefit, and **ask the owner before doing any of them**.
