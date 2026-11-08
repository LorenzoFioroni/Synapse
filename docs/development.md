# Development workflow

Use the conventions in [AGENTS.md](../AGENTS.md). Read [architecture](architecture.md) when
choosing package boundaries and the affected subsystem page for its contracts; a small edit
does not require reading the entire documentation set.

## Setup

Run once for a new checkout/environment, using Python 3.10+ and Node.js 22 (as in CI):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools
python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
npm --prefix frontend ci
```

Activate the existing environment in later shells. Repeat the locked Python install when its
requirements change, and `npm ci` when the frontend lock changes, not before every check.
Installation needs the package registries; checks below do not install tools. See
[dependencies](dependencies.md) for lock maintenance and standalone runtime installation.

Optional setup:

- `pre-commit install` enables the existing local formatting/type-check hooks. They use installed
  tools and do not replace final verification.
- For browser checks, run `python -m pip install -r tools/requirements-e2e.txt` and
  `python -m playwright install chromium` (also `webkit` for portable coverage). See
  [browser checklists](testing.md#browser-checklists) for platform requirements.

## During development

Run the smallest relevant checks while editing, then broaden them if shared contracts change.
Examples, from the repository root with the environment active:

| Change | Useful checks |
|---|---|
| Python component or endpoint | `python -m pytest -q tests/test_<area>.py`; `python -m ruff check <changed paths>`; `python -m ruff format <changed paths>` |
| Backend interfaces | `python -m mypy`; `python -m pytest -q tests/test_layers.py` |
| Bundled Python plugin | Its tests under `tests/plugins/`; `python tools/typecheck_plugins.py <plugin-folder>` |
| Frontend or extension | `npm --prefix frontend run check`; `npm --prefix frontend run build`; affected `node --test tests/js/<area>.test.mjs` |
| UI behavior | `python tests/e2e/run.py <checklist> ...` using affected entries in [Testing](testing.md#browser-checklists) |
| Documentation only | Check accuracy, relative links and examples; application tests are unnecessary unless an executable workflow changed |

Do not rerun the full suite after every small edit. Tests use disposable data and fake external
services; keep them independent of production libraries and credentials. Concurrency tests should
coordinate work with events/barriers and bounded waits instead of sleeping to assume thread order.

## Final verification

For code or executable workflow changes, run once after the final edit:

```bash
python tools/check.py
```

This checks that generated agent adapters match their shared definitions, then runs Ruff lint and
format checks, Python and bundled-plugin type checks, frontend/extension
type checks, the frontend build, and the full Python/JavaScript unit suite. It stops on the first
failure, does not install dependencies or fix source files, and works from any working directory.
The build writes ignored `frontend/js/`; checks may write their normal caches. Use
`python tools/check.py --coverage` to include CI's coverage and slow-test diagnostics.

Run browser checklists for any UI touched separately; they are not part of the default command:

```bash
python tests/e2e/run.py notes render --output-dir browser-results
python tests/e2e/run.py --browser webkit <supported-checklist>
```

After a failure, fix issues introduced by the change and rerun affected checks. Reuse successful
results while their inputs remain unchanged. Verify a suspected baseline failure against the base
revision; do not silently skip it or report the suite as passing. Summarize checks, skips and
environment limitations in the PR. Inspect the final diff for unrelated changes and private data,
and inspect available CI results and CodeQL/review feedback after pushing. Address actionable
findings without repeatedly retrying unchanged failures. Merge policy remains a maintainer choice.

CI additionally validates lock reproducibility, isolated runtime installation and the OS/Python
matrix; the local command does not reproduce those environments. See [Testing](testing.md#ci).
Relevant browser coverage is selected automatically on PRs by `tools/browser_scope.py`; unknown
application areas fall back to full coverage. Nightly/manual runs remain full. Security scanners
run separately and need network access: see [Security checks](security-checks.md).

For the shared Clara/Brie/Cody/Archie/Seth/Vera/Iris roles, model profiles, adapter generation and
manually invoked triage with GitHub issues, see [Shared coding roles](../.agents/README.md).
