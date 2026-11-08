# Development security checks

Security checks report problems; they do not change the maintainer's optional merge policy.
The [Security workflow](../.github/workflows/security.yml) runs on PRs, pushes to main,
weekly advisory refreshes and manual dispatch. It uses a read-only token and persists no checkout
credentials. It does not use AI, production data, deployment credentials or package lifecycle scripts
from npm. Fork PRs use `pull_request`, never `pull_request_target`.

## Secrets

Gitleaks 8.30.1 is downloaded from its official release and verified against a checked-in SHA-256
digest before execution. PRs scan their new commits (including secrets added then removed); main
and scheduled runs scan available full history. Values are fully redacted in output. Do not add a
broad allowlist for tests/docs: investigate each result, rotate exposed credentials and remediate
history when needed. A clean scan does not prove that arbitrary private information is absent.
The checked-in `.gitleaks.toml` retains default rules and permits only exact known citation-key
fixture strings (including their historical paths) and one exact minified Mermaid expression,
each constrained to its file. Other content in those files remains scanned.

For local verification with that binary installed: `gitleaks git --redact=100 .`.
Before publishing, inspect staged changes for personal hostnames, paths and library content too.
GitHub's own secret scanning/push-protection settings remain separate and are not changed here.

## Dependencies

Python jobs run on 3.10 and 3.13 to cover the lock's conditional versions. They install the hashed
development lock (which includes runtime dependencies), plus pinned browser and resolver tools with
wheels only, then snapshot every resolved package/version. An isolated `pip-audit==2.10.1` audits
that snapshot without resolving it again or modifying Synapse. Known Python vulnerabilities fail
the job. The scanner itself runs on Python 3.13 outside the audited application environment, even
when the snapshot was resolved on 3.10. Optional pre-commit tooling is omitted on 3.10 because its
filelock dependency has no compatible patched release; application and test coverage remain.

Each of the four npm lockfiles (`frontend`, `tools/cm6`, `tools/graph`, `tools/md`) is audited with
`npm audit --package-lock-only --ignore-scripts --audit-level=high`, without installing packages.
High/critical npm findings fail; lower-severity findings remain visible. Audits need advisory-service
network access and failures remain failures; no blanket suppression or automatic upgrade is used.
These checks do not cover OS packages, live model weights or arbitrary separately installed plugins.

## CodeQL and containers

Keep the existing GitHub-managed CodeQL analysis; there is no second CodeQL workflow. Check both
analysis completion and actual findings/review comments. Shared policy and tests should address
recurring patterns (SSRF, HTML handling, permissions and credential/error redaction) rather than
just hiding scanner warnings.

The device-cookie renewal path intentionally reissues an authenticated token. Before renewal,
`Devices.check` requires its SHA-256 to match a registered device; pairing and passkey sign-in
generate new tokens on the server. `set_device_cookie` also rejects values outside a complete
URL-safe token match before constructing the header. A `py/cookie-injection` report on that
path needs this data-flow review: an unknown or tampered cookie must receive 401 without a
replacement cookie. `tests/test_devices.py` covers that refusal and checks renewal preserves
the token, lifetime, HttpOnly, SameSite, path and HTTPS Secure attributes.

The [container smoke workflow](../.github/workflows/container-smoke.yml) builds deployment PRs,
validates Compose with example values and starts the image with disposable tmpfs data, no published
ports and the normal hardening options. It waits for the health check and removes the container.
It does not contact a real Cloudflare tunnel, run an updater against production or scan OS CVEs.
