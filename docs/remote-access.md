# Remote access (a tunnel and `--public-url`)

Synapse can run on another computer (a Linux server, a desktop at work) and be used from your
iPhone, iPad and laptops anywhere, through an HTTPS tunnel such as **Cloudflare Tunnel**. Nothing is
installed on the devices and no port is opened on the server: a small program on the server
(`cloudflared`) keeps an outgoing connection to Cloudflare, and Cloudflare serves your address
(`https://synapse.example.com`) with a real certificate, so passkeys and the Home Screen app work.

Start Synapse with the address the tunnel serves:

```bash
python server.py --no-browser --public-url https://synapse.example.com
```

With `--public-url`, Synapse requires loopback-only listening (`127.0.0.1`, `localhost` or `::1`).
It refuses `--lan` and any other `--host` before starting: direct network access could bypass the
tunnel and treat a request from the server's own LAN address as this computer, without sign-in.
The application factory also rejects a configuration combining `public_url` with `lan`.
Without `--public-url`, LAN access works as before.

When Synapse is local-only, **Settings → AI Access** describes both setup options: `--lan`
for the same network, or `--public-url https://synapse.example.com` behind an HTTPS tunnel,
and says not to combine them. With the tunnel configured, the advertised MCP address and
copied AI-client commands use `https://synapse.example.com/mcp`. Token creation and removal
from another device still require passkey confirmation.

## How it works

`cloudflared` passes every visitor's request to `http://127.0.0.1:8000`. Judged by the connection's
address alone, all of them would come from this computer, which needs no sign-in and may make pairing
codes and access tokens. `backend/access/proxy.py` (`ProxyMiddleware`, in front of the Flask app)
prevents that. A request counts as **proxied** when

- its `Host` is the public name (`cloudflared` passes the visitor's `Host` on unchanged, and nobody
  on the internet can make it send `127.0.0.1`), or
- it carries a header proxies add: `X-Forwarded-For`, `Cf-Connecting-Ip`, `X-Real-Ip`, `Forwarded`,
  `X-Forwarded-Host` or `Cf-Ray` (`FORWARD_HEADERS`). This holds with or without `--public-url`:
  apps on this computer don't send them, and counting a request as proxied only asks for a sign-in.

The guard treats a proxied request as **another device** (`g.this_computer` is false): it needs a
paired or passkey-signed-in device's cookie, and `/mcp` an access token, as over the LAN
([`sync-and-devices.md`](sync-and-devices.md#the-guard)). Requests for the public name are also
made HTTPS (`wsgi.url_scheme`), because the visitor's browser speaks HTTPS to Cloudflare: passkeys
are made for the public name, the device cookie is `Secure`, and pairing links and the MCP address
use `https://synapse.example.com`. The visitor's address (from `Cf-Connecting-Ip`) is what
Settings shows as "last used from"; it is never used to decide anything.

The public name is accepted by the Host check, and changes must come from
`https://synapse.example.com` itself (the Origin check). `--debug` is refused with
`--public-url`: Flask's debugger would be on the internet.

## Administrative actions from your devices

On a server you don't sit at, the actions that were reserved for "the computer running Synapse" —
pairing a device, making or removing an access token, adding or removing passkeys, removing other
devices, and changing plugins (enabling, disabling or reconfiguring them, which runs or changes code
on the server) — can be done from a signed-in device, **after confirming with a passkey** (Face ID,
Touch ID). The server answers such a request with 403 and `confirm: true`; the app asks the browser
for one of Synapse's passkeys (`POST /api/passkeys/confirm/options`, then `/api/passkeys/confirm`)
and tries once more. The confirmation is remembered for that device for 5 minutes
(`backend/access/confirm.py`, in memory). A stolen device cookie alone can therefore not hand out new
access. Enrolling a passkey is the one bootstrap: a device may add a passkey only straight after it
pairs (`Devices.recently_paired`, a few minutes — the first passkey has nothing to confirm with yet)
or after confirming with a passkey it already holds. Because pairing codes are issued only on this
computer or over SSH (`--pairing-link`), a stale cookie is never "recently paired" and so cannot
enrol a passkey of its own.

This works the same over the LAN with `--cert`: any device signed in over HTTPS can confirm.

## Browser extension

The Save to Library extension can connect directly to the public HTTPS address. Update both
server and extension, then enter the address and a token from **Settings → AI Access** in the
extension’s Options, save, and allow Chrome’s server permission. This uses bearer authentication
instead of browser pairing cookies. Localhost remains available without a token. See
[browser extension setup](../browser_extension/README.md#remote-server).

## Pairing the first device: `--pairing-link`

With no screen on the server, ask the running Synapse for a one-time pairing link from a terminal
there (over SSH):

```bash
python server.py --pairing-link            # same --port (and --cert) as the running server
```

It prints the link (`https://synapse.example.com/pair#code=…`, valid once for a few minutes) and,
with `segno` installed, a QR code in the terminal. Open it on your phone, then in **Settings →
Devices** choose **Add a Passkey…** — do this soon after pairing, within the few-minute enrolment
window (`PASSKEY_ENROLL_GRACE`); leave it longer and you simply run `--pairing-link` and pair again.
Your other devices then sign in with that passkey (it syncs through iCloud Keychain or Google
Password Manager); no more pairing.

`--pairing-link` asks the server over loopback, like Settings → Devices does
(`backend/access/pairing_link.py`).

## Setting it up on Linux with Cloudflare Tunnel

You need a domain whose DNS is on Cloudflare (the free plan is enough).

1. **Synapse.** Clone the repository on the server, then as in the README:
   `python3 -m venv .venv && .venv/bin/pip install --require-hashes --only-binary=:all: -r requirements.txt` and
   `cd frontend && npm ci`. Node is needed once to build the frontend; Git is needed for the notes
   history. To move your library over, stop Synapse on both computers and copy the `data` folder
   (`rsync -a ~/Documents/Applications/Synapse/data/ server:Synapse/data/`). Use one copy from then
   on: the two would not stay in sync.
   The runtime lock includes transitive versions and hashes, and CI verifies the same pins on
   Linux and macOS with Python 3.10 and 3.13. After pulling a dependency update, rerun the locked
   install in the service's virtual environment before restarting. See [dependency maintenance](dependencies.md).
2. **Run it as a service** that starts with the computer. `~/.config/systemd/user/synapse.service`:

   ```ini
   [Unit]
   Description=Synapse

   [Service]
   WorkingDirectory=%h/Synapse
   ExecStart=%h/Synapse/.venv/bin/python server.py --no-browser --public-url https://synapse.example.com
   Restart=on-failure

   [Install]
   WantedBy=default.target
   ```

   Then `systemctl --user daemon-reload && systemctl --user enable --now synapse`, and
   `sudo loginctl enable-linger $USER` so it keeps running when you log out. `journalctl --user -u
   synapse -f` shows its output.
3. **The tunnel.** In the Cloudflare dashboard: Zero Trust → Networks → Tunnels → **Create a
   tunnel** → Cloudflared, name it `synapse`, pick the server's system and run the install command
   it shows (`sudo cloudflared service install <token>`: `cloudflared` then runs as a system service).
   Add a **public hostname** (published application): subdomain `synapse`, your domain, service
   type **HTTP**, URL `localhost:8000`. Leave "HTTP Host Header" empty: Synapse must see the public
   name.
4. **The first device.** Over SSH: `cd ~/Synapse && .venv/bin/python server.py --pairing-link`,
   scan the QR code with your phone, then add a passkey there. On the iPad and laptops open
   `https://synapse.example.com/` and choose **Sign In with a Passkey**. Add it to the Home Screen
   as described in the README.
5. **AI apps.** On any signed-in device: Settings → AI Access → **New Token…** (confirm with your
   passkey). The address is `https://synapse.example.com/mcp`.

Keep the server's own browser access for emergencies: over SSH, `ssh -L 8000:127.0.0.1:8000 server`
and open `http://127.0.0.1:8000/` on your laptop; that counts as the computer running Synapse.

### Things to know

- **Cloudflare sees the traffic.** It ends the HTTPS connection and passes the requests on, as any
  such tunnel does. Your sign-in still guards the library.
- **Large uploads.** Cloudflare's free plan limits a request to 100 MB, so a bigger PDF can't be
  dropped in through the tunnel.
- **Cloudflare Access** (an email code in front of the site) is optional. If you add it, bypass it
  for `/mcp` (AI apps can't do its login) and for `/manifest.webmanifest` and `/assets/` (the
  Home Screen icon is fetched without cookies).
- **Another tunnel or a reverse proxy** works the same if it serves HTTPS and passes the visitor's
  `Host` on. Bind Synapse to 127.0.0.1 (the default) so the proxy is the only way in.
