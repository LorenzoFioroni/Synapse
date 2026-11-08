# Python dependencies

Synapse still runs from its checkout and installs with pip. Runtime and development installs
use generated requirements files with exact direct/transitive versions, SHA-256 hashes and
Python/platform markers. `uv` is needed only to maintain the files, not to run Synapse.

| File | Purpose |
|---|---|
| `requirements.in` | Direct runtime dependency ranges and comments about optional packages. |
| `requirements-dev.in` | Runtime inputs plus development tools; constrained by the runtime lock. |
| `requirements.txt` | Generated portable runtime lock. |
| `requirements-dev.txt` | Generated portable development lock, retaining the runtime pins. |
| `tools/requirements-uv.txt` | Exact uv release used to generate and check both locks. |
| `tools/lock_requirements.py` | The shared generation/check command. |

## Install

Use a fresh virtual environment for the server, then install the runtime lock:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
.venv/bin/python -m pip check
```

For development, install `requirements-dev.txt` instead. Both contain the same runtime pins;
development adds the test, coverage, lint and type-checking tools. Hash checking refuses changed
artifacts or dependencies missing from the lock. Wheel-only installation avoids running an
unlocked source-build toolchain. CI verifies these commands on macOS and Linux, with Python 3.10
and 3.13. Other supported Python versions use the corresponding markers in the same files.

Optional `pre-commit` hooks are installed only on Python 3.11+: their `virtualenv` dependency
otherwise selects an unpatched `filelock` on 3.10. Synapse, pytest, lint and typing still support
Python 3.10. Use those checks directly on 3.10, or a separate 3.11+ environment for hooks. PyYAML
is an explicit development dependency for agent-adapter tests, independent of the optional hooks.

Repeat the locked install after pulling a dependency change, before restarting the service.
Installing with pip does not remove unrelated packages already in an environment; recreate the
virtual environment when an exact clean environment is needed. Optional PyMuPDF/Pillow and browser
testing tools are outside these base locks. Add them to the inputs and regenerate if they must
be part of a reproducible host environment. Plugins manage their own packages separately from
the host lock. The app continues to work offline after dependencies are installed.

## Update

Install the pinned lock tool in a maintenance environment:

```bash
python -m pip install -r tools/requirements-uv.txt
python tools/lock_requirements.py
```

Edit the `.in` files, never the generated `.txt` files. The command preserves compatible existing
pins. To intentionally update all dependencies to their newest compatible versions:

```bash
python tools/lock_requirements.py --upgrade
```

The wrapper requires the exact pinned uv version, uses PyPI explicitly and ignores local uv/pip
configuration and resolver environment overrides. Resolution is universal, with Python 3.10 as
the lower bound, so conditional dependencies remain conditional instead of inheriting the
maintainer's OS and Python version. All published distribution hashes for each chosen version
are retained for platform portability. The development resolution is constrained by the newly
resolved runtime file. Both resolutions must succeed before either checked-in output is updated.

Review changes to versions, markers and hashes together with their inputs. Run the normal lint,
typing and test checks using the new development lock in a fresh environment. Commit inputs,
locks and any relevant docs together. The `--upgrade` flag is an intentional dependency update,
not part of routine installation or CI.

## Check

```bash
python tools/lock_requirements.py --check
```

This regenerates both files in a temporary directory, preferring their checked-in pins, reports
any difference and exits nonzero on drift. It never rewrites the checkout. CI runs this check,
then installs the runtime lock in isolation and the development lock for full checks on both
macOS/Linux and Python 3.10/3.13. Lock regeneration needs the package index; normal execution of
Synapse does not. Updating the uv pin is a separate reviewed tooling change and requires
regenerating both files with that version.

See the [uv lock/compile documentation](https://docs.astral.sh/uv/pip/compile/) and
[universal resolution](https://docs.astral.sh/uv/concepts/resolution/#universal-resolution) for resolver behavior.
