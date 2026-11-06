# Docker

Synapse can run in Docker on a server, reached only through Cloudflare Tunnel
([remote access](remote-access.md)). Docker Compose starts it with the computer and restarts it when
it stops, so no terminal has to stay open. The repository has what this needs:

| File | What it is |
|---|---|
| `Dockerfile` | The image: the frontend built with Node.js, then Python, Git, tini and esbuild. It starts `python server.py --container`. |
| `compose.yaml` | One Synapse, its tunnel (`cloudflared`) and, with the `ollama` profile, Ollama for [autocompletion](autocompletion.md). |
| `compose.nvidia.yaml` | Adds an NVIDIA GPU to Ollama. |
| `.env.example` | The settings `compose.yaml` reads. Copy it to `.env`, which git ignores. |

## Setting it up

You need Docker with Compose, a Cloudflare tunnel (Zero Trust → Networks → Tunnels → **Create a
tunnel** → Cloudflared) and, for Ollama on a GPU, the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

1. **Settings.** In the repository, `cp .env.example .env` and fill it in:
   - `SYNAPSE_PUBLIC_URL`, the address the tunnel serves (`https://synapse.example.com`);
   - `TUNNEL_TOKEN`, the token in the tunnel's install command;
   - `SYNAPSE_DATA`, `SYNAPSE_UID` and `SYNAPSE_GID`: the library folder and its owner (`id -u`, `id -g`);
   - `TZ`, the time zone that plugin schedules and backups follow (the container's is UTC otherwise).
2. **The data folder.** Make it before the first start (`mkdir -p data`), as the user who will own
   it. Docker would make a missing folder as root, and Synapse could not write to it. To move a
   library, stop Synapse and copy the whole folder.
3. **The tunnel's route.** In the dashboard, give the tunnel a public hostname (published
   application): your name, service type **HTTP**, URL `synapse:8000`, which is the Compose
   service, not `localhost`. Leave "HTTP Host Header" empty, since Synapse must see the public name.
   One tunnel can serve several hostnames, each with its own route.
4. **Start:** `docker compose up -d --build`, then `docker compose ps` until it says `healthy`.
5. **The first device**, from a terminal on the server:
   `docker compose exec synapse python server.py --pairing-link` prints a one-time link and a QR
   code. Open it on your phone, then add a passkey there ([remote access](remote-access.md#pairing-the-first-device---pairing-link)).
6. **Autocompletion (optional).** Put `COMPOSE_PROFILES=ollama` in `.env` (and
   `COMPOSE_FILE=compose.yaml:compose.nvidia.yaml` for the GPU), run `docker compose up -d`, then
   `docker compose exec ollama ollama pull qwen2.5-coder:1.5b`.

**Updating:** `git pull && docker compose up -d --build`. `docker compose logs -f synapse` shows the
server's output.

## What `--container` changes

In a container Synapse has to listen on `0.0.0.0`: the tunnel connects from a container of its
own. `--public-url` alone refuses that, because on a computer it would let the LAN bypass the tunnel.
`--container` (`server.py`, `container_mode`) allows it, with these rules:

- **It needs `--public-url`** and refuses `--lan`, `--host`, `--cert`/`--key`, `--allow-host`,
  `--debug`, `--window`, `--quit-when-closed` and `--make-shortcut`. The application factory also
  refuses a `Config` with `container` but no `public_url`, or with `lan`.
- **Only loopback and the public name** are accepted as `Host`, as with loopback listening
  (`config.lan` stays false). The service name (`synapse:8000`) and the container's IP are
  refused with 403.
- **Everyone signs in.** "This computer" is the container itself: its loopback, used by the health
  check and by `docker compose exec … --pairing-link`. The tunnel's requests come from
  cloudflared's container, and the host's come through the Docker network's gateway. Neither is
  this computer, so both need a signed-in device or a token, like any other device.
- **New files are private.** It sets `umask 077`, so what it writes to the bind-mounted data
  folder (and the notes history, plugin packages and bundles) is readable only by its owner.
- **The visitor's address.** Settings shows where each device was last used from. Outside a
  container that address is taken from `Cf-Connecting-Ip` only when the proxy is on this computer.
  In a container it is also taken from proxies on private networks (10/8, 172.16/12, 192.168/16,
  fc00::/7, `proxy.PRIVATE_NETWORKS`): the tunnel's container is one. The address is only shown,
  never used to decide anything.

## Ollama next to Synapse: local services

Autocompletion sends note text only to Ollama on "this computer", meaning loopback addresses
(`plugins/autocompletion/server/ollama.py`, `local_url`). In Docker, Ollama is another container:
`http://ollama:11434`. The deployment names it with `--local-service NAME=URL` (repeatable) or
`SYNAPSE_LOCAL_SERVICES="ollama=http://ollama:11434"` (entries separated by commas or spaces; a
flag wins over the variable for the same name). `compose.yaml` sets the variable.

- **Only from whoever starts the server.** A local service is never set in Settings. A device with
  a stolen cookie could otherwise point the server at addresses inside its network.
- **`plugin.http`** treats the exact origin of a local service (scheme, host and port) like
  loopback, for plugins with the `"loopback"` network permission. It skips proxies, has no rate
  gap and refuses embedded credentials. Other ports on that host stay out of reach, and so does the
  service for plugins without the permission. `plugin.http.local_service(name)` gives a plugin the
  address (plugin API 1.12, [plugins](plugins.md#reaching-the-web)).
- **Autocompletion** uses `local_service("ollama")` in place of its address setting. Settings →
  Autocompletion then shows it read-only, with where it comes from.

## The image

- **Two stages.** `node:22` runs `npm ci` and `npm run build`. The final image is `python:3.13-slim`
  with the locked requirements (`pip install --require-hashes --only-binary=:all:`), the built
  `frontend/js` and esbuild's own executable in `/usr/local/bin`. Bundled plugins' TypeScript
  frontends are bundled with it when they start (`backend/plugins/build.py`). `ensure_built` serves
  the built scripts without Node.js. Pillow is optional and not in the lock, so `view_image` sends
  large images unscaled.
- **Git** keeps the notes history. The container runs as the data folder's owner
  (`user:` in `compose.yaml`). Otherwise Git refuses the repository ("dubious ownership"), and the
  files would end up owned by another uid on the host.
- **One process.** tini starts `python server.py --container`, passes signals on and reaps the
  programs Synapse starts (git, esbuild, pip). `docker stop` sends SIGINT, like Ctrl+C. There is one
  server process with Werkzeug's threads, as `server.py` runs it. Passkey challenges, confirmations,
  the change feed and the plugins' jobs live in its memory, so it must never run as several worker
  processes.
- **Read-only.** The app folder belongs to root, and `compose.yaml` makes the container's
  filesystem read-only, apart from `/data` and a `/tmp` in memory (also `HOME`). It drops all
  capabilities and sets `no-new-privileges`. `PYTHONDONTWRITEBYTECODE` keeps Python from writing
  `__pycache__`.
- **Plugins' Python packages** are installed at runtime into `data/plugin-data/<id>/.python`, as
  outside Docker. They persist with the data and need the network the first time.
- **Health check:** `GET http://127.0.0.1:8000/manifest.webmanifest` from inside the container,
  which needs no sign-in on loopback.

## Networks

Compose publishes **no ports**: the tunnel is the only way in. Docker's published ports bypass the
host's firewall (ufw); if you ever publish one, bind it to `127.0.0.1`.

| Network | Who is on it | Why |
|---|---|---|
| `tunnel` | Synapse, cloudflared | the tunnel's requests; Synapse's own lookups on the web |
| `models` (`internal`) | Synapse, Ollama | suggestions; no way out to the internet, and the tunnel cannot reach Ollama (it has no authentication) |
| `downloads` | Ollama | `ollama pull` |

## Two libraries on one server

Each Synapse instance needs its own service, data folder, public hostname (`--public-url`) and
therefore its own passkeys, which belong to a hostname. Keep one Ollama for all of them: two would
each load the model into GPU memory. For a second instance, add a service like `synapse` with its
own `SYNAPSE_DATA`, `--public-url`, and a tunnel network of its own shared with cloudflared. Then
add a route for its hostname to `http://<service>:8000`. Joining the `models` network gives it the
same Ollama. Separate tunnel networks keep the instances from reaching each other. Even on a shared
network, every request between them would need a sign-in.

Keep a server's own setup (more instances, a published Ollama port, real hostnames) out of the
repository: in a `compose.override.yaml` next to `compose.yaml` (git ignores it and Compose reads
it), or in a folder of its own with its own `compose.yaml` that builds from the checkout.

## Backups and moving back

- **Back up the whole data folder** from the host, `pdfs/` included: Synapse's own backups
  (Settings → Backups) leave out the PDFs and `auth.db`. Stop the container first
  (`docker compose stop synapse`) for a consistent copy.
- **Moving back** to `python server.py --public-url …`: stop the stack, run Synapse from a checkout
  of the same version (so neither side migrates the databases in the other's back), and point the
  tunnel's route at `localhost:8000` again.

## When something is wrong

- **403 "Requests for host 'synapse:8000' are not accepted"**: the route rewrites the Host. Clear
  "HTTP Host Header" in the route (or set it to the public name).
- **The container restarts and the log says "dubious ownership" or "Permission denied"**:
  `SYNAPSE_UID`/`SYNAPSE_GID` are not the data folder's owner.
- **`could not select device driver "nvidia"`**: install the NVIDIA Container Toolkit, run
  `sudo nvidia-ctk runtime configure --runtime=docker` and restart Docker.
- **From the host**, `curl http://<container-ip>:8000/api/items` answers 403 (the Host is the
  container's IP), and with `-H 'Host: 127.0.0.1'` 401: the host is not "this computer". Use
  `docker compose exec synapse …` for what needs this computer.

Tests: `tests/test_container.py` (`--container`, the guard behind a tunnel container, local
services), `tests/plugins/test_http.py` and `tests/plugins/test_autocompletion.py` (local services
in `plugin.http` and Autocompletion).
