# Updates

**Settings → Updates** shows which version of Synapse runs and the newer commits on the GitHub
branch it comes from. Where the server runs the updater ([Docker](docker.md)), **Update Now**
installs them. A persistent spinner notice covers checking, the backup before installation, installing
and reconnecting after the restart, even if you move away from Settings. Failure or a 15-minute
wait ends the notice with an explanation; reloading checks the server again. The bell also says
when there is an update, once per new version on each device.

## Where the code comes from

Only whoever starts the server says this, in the environment, never in Settings:

| Variable | What it is |
|---|---|
| `SYNAPSE_SOURCE` | The repository and branch, `https://github.com/owner/Synapse.git#main` (the branch defaults to `main`). In Docker it is also what the image is built from. Without it, Synapse does not check for updates. |
| `SYNAPSE_COMMIT` | The commit this copy was built from. The Docker image sets it, from the build argument the updater passes. Outside Docker, Synapse asks Git for the checkout's `HEAD`. |
| `SYNAPSE_UPDATER`, `SYNAPSE_UPDATER_TOKEN` | The updater's address and the token Synapse sends it. `compose.yaml` sets both once `SYNAPSE_UPDATER_TOKEN` is in `.env`. |

## Checking

`backend/online/updates.py` (`Updates`, wired in `Services` and started by `create_app`) works as
follows:

- **The question:** it asks GitHub's API for `compare/<commit>...<branch>`, which gives the number
  of new commits and the newest 20 of them. When the commit is unknown, it asks for the branch's
  newest commit instead. Requests go through the checked public transport of `online/http.py`.
- **How often:** once at start, then every 6 hours, and an hour later after a failure (offline, or
  GitHub's limit of 60 requests an hour without a key). **Check for Updates** asks again, except
  within 30 s of the last check. A failed check keeps the last answer and shows the error.
- **The answer:** `GET /api/updates` (`?refresh=1` for Check for Updates) returns:
  - `current`, `behind` and `commits` (newest first), `latest`, and `diverged` when the build has
    commits that are not on the branch;
  - `checked` and `error`;
  - `can_install`, and the updater's `install` state.
- **The bell** (`frontend/src/updates.ts`) asks 20 s after a page opens and every 6 hours after.
  It announces a new newest commit once per device (`localStorage`).

Without an updater, Settings says how to update by hand: `docker compose up -d --build` in Docker,
or `git pull` and a restart for a checkout.

## Installing

**Update Now** sends `POST /api/updates/install`:

1. **The gate.** Installing replaces the code that runs on the server, so it goes through the
   administrative check (`admin.refusal`): it is allowed on this computer, or from a device after a
   passkey confirmation, as for plugins ([remote access](remote-access.md#administrative-actions-from-your-devices)).
2. **A backup** of the library, settings and notes is made ("before updating Synapse"). If it
   fails, nothing is installed.
3. **The request.** Synapse asks the updater, `POST /update` with `Authorization: Bearer <token>`,
   and answers 202 at once. The page shows the updater's progress, waits until the server answers
   with the new commit, then reloads.

### The updater

`docker/updater/updater.py` runs in a container of its own (`docker/updater/Dockerfile`: the
official Docker CLI image with Compose, plus Python and Git, standard library only). It is the
only container with the Docker socket, which is as much as root on the host. So it trusts Synapse
with nothing but "now":

- **Its own source.** It reads the repository and branch from its own `SYNAPSE_SOURCE`, finds the
  branch's newest commit with `git ls-remote`, and builds exactly that commit. It runs
  `docker compose build` with `SYNAPSE_BUILD_SOURCE=<repo>#<commit>` and `SYNAPSE_COMMIT=<commit>`,
  which `compose.yaml` uses as the build context and argument. A request cannot name another
  repository, branch or commit, so a compromised Synapse could at most install what is already on
  the branch.
- **Its token.** Every request except `GET /health` needs the token, and it listens only on the
  `updates` network, an internal one it shares with Synapse.
- **The right folder.** It mounts the deployment folder read-only, at its path on the host
  (`UPDATER_PROJECT_DIR`), so that Compose inside it mounts the same data folders as Compose run
  by hand. Before anything else it checks that the Synapse containers were started from that
  folder (Compose's `working_dir` label). From any other path Docker would start Synapse on a new,
  empty data folder.
- **Nothing to do:** when the running image's `SYNAPSE_COMMIT` already is the branch's newest
  commit, it answers `current`.
- **Going back.** It tags the running image `<image>-previous`, builds, then restarts the services
  (`UPDATER_SERVICES`, `up -d --no-deps --no-build`). It waits up to 5 minutes for them to be
  healthy. If they are not, it tags the previous image back and restarts them again.
  - A failed build changes nothing.
  - `GET /status` reports the state (`idle`, `running`, `done`, `current`, `failed`) with a message
    and the last lines of the commands' output, which Settings shows under Details. Signed-in
    devices can read it, so it never holds a container's environment, where the token is: the
    updater asks Docker for `SYNAPSE_COMMIT` alone and leaves that command's output out of the log.
    Credentials in an address (a private repository's `SYNAPSE_SOURCE`, `https://user:token@…`)
    are shown as `https://***@…` in the message and the log; Synapse's own update check does not
    repeat an address it cannot use either.
- **Not itself.** The updater only updates the Synapse services. Rebuild it with
  `docker compose up -d --build`.

What going back does not cover:

- A version that starts but misbehaves.
- A database migration. An older version may not read a newer schema; the backup made before the
  update is the way back.

## Setting it up (Docker)

In `.env`:

```bash
SYNAPSE_SOURCE=https://github.com/owner/Synapse.git#main
SYNAPSE_UPDATER_TOKEN=<openssl rand -hex 32>
UPDATER_PROJECT_DIR=<this folder's absolute path: pwd>
COMPOSE_PROFILES=updater        # with ollama: ollama,updater
```

Then run `docker compose up -d --build`. The first build has no commit to name, so Settings shows
"a build that does not say which commit it is". The first Update Now installs the branch's newest
commit and names it. The same updater can update several Synapse services built from one image:
`UPDATER_SERVICES="synapse synapse-two"`.

## Tests

- `tests/test_updates.py` covers the sources, the check (new commits, up to date, diverged, unknown
  build, errors, rate limiting) and installing (backup first, the token, a busy or missing
  updater). It also covers the API: signed-in devices read it, and installing needs this computer
  or a passkey.
- `tests/test_updater.py` runs the updater against a stand-in for Docker, Compose and Git: it
  builds exactly the branch tip, does nothing when current, leaves a failed build unchanged, goes
  back from an unhealthy version, refuses another folder, runs one update at a time, and its HTTP
  API needs the token.
- `tests/e2e/operation_feedback_checklist.py` defers credential and update service stubs to check
  pending feedback, administrative cancellation without retry, backup failure, restart/reconnection
  and terminal update failure cleanup. No real passkeys or updater are used.
