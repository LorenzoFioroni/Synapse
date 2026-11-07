"""Synapse — a local, web-based reference manager.

Run:   python server.py                 (http://127.0.0.1:8000, opens your browser)
       python server.py --port 5050 --no-browser
       python server.py --data-dir ~/MyLibrary
       python server.py --lan            (also reachable from your iPad/phone; pair them in Settings → Devices)
       python server.py --lan --cert mac.local.pem --key mac.local-key.pem
                                         (HTTPS, needed to sign devices in with passkeys)
       python server.py --public-url https://synapse.example.com
                                         (behind Cloudflare Tunnel or another HTTPS proxy; see docs/remote-access.md)
       python server.py --container --public-url https://synapse.example.com
                                         (in a container behind a tunnel: what the Docker image runs; see docs/docker.md)
       python server.py --pairing-link   (a one-time link that pairs a device with the running server)
       python server.py --window         (in a window of its own, like a program, not a browser tab)
       python server.py --make-shortcut  (an icon on the Desktop that does that; see docs/desktop.md)
       python server.py --new-plugin my-plugin --dev-plugins ~/plugins
                                         (a plugin to start from; then run with --dev-plugins ~/plugins)

Your library (database, PDFs, full-text index, settings) lives in ./data
unless --data-dir or the SYNAPSE_DATA_DIR environment variable say otherwise.
"""

from __future__ import annotations

import _thread
import argparse
import logging
import os
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from backend import create_app
from backend.config import Config, LocalServiceError, parse_local_service
from backend.frontend import BuildError, ensure_built


def app_url(host: str, port: int, *, tls: bool) -> str:
    """The address opened on this computer for a server listening on ``host``."""
    wildcard = host in ("0.0.0.0", "::")
    open_host = "127.0.0.1" if wildcard else host
    if wildcard and tls:
        from backend.access import lan

        # A LAN certificate is normally issued for the Bonjour name. Opening an IP would make
        # that valid certificate look broken and would also prevent passkeys from working.
        open_host = lan.local_hostname() or open_host
    if ":" in open_host and not open_host.startswith("["):
        open_host = f"[{open_host}]"
    return f"{'https' if tls else 'http'}://{open_host}:{port}/"


def _open_when_ready(
    url: str,
    opener: Callable[[str], object],
    *,
    ready: Callable[[str], bool | None] | None = None,
    attempts: int = 150,
    interval: float = 0.1,
) -> bool:
    """Open only after this Synapse server answers, so app mode does not keep a blank first load."""
    if ready is None:
        from backend.desktop.window import synapse_at

        ready = synapse_at
    for _ in range(attempts):
        if ready(url) is True:
            opener(url)
            return True
        time.sleep(interval)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument(
        "--lan",
        action="store_true",
        help="listen on the local network too (same as --host 0.0.0.0), so paired devices such as an "
        "iPad can use the library; pair them in Settings → Devices",
    )
    ap.add_argument(
        "--cert",
        metavar="FILE",
        help="serve over HTTPS with this certificate (PEM; with --key). Passkeys need HTTPS with a "
        "certificate your devices trust",
    )
    ap.add_argument("--key", metavar="FILE", help="the certificate's private key (PEM)")
    ap.add_argument(
        "--public-url",
        metavar="URL",
        help="the HTTPS address a tunnel or reverse proxy (such as Cloudflare Tunnel) serves Synapse at, "
        "e.g. https://synapse.example.com. Requests that come through it always need a signed-in "
        "device; see docs/remote-access.md",
    )
    ap.add_argument(
        "--container",
        action="store_true",
        help="run in a container behind a tunnel (the Docker image starts Synapse so; see docs/docker.md): "
        "listen on the container's network but answer only to loopback and the --public-url name, and keep "
        "new files private. Needs --public-url",
    )
    ap.add_argument(
        "--local-service",
        action="append",
        default=[],
        metavar="NAME=URL",
        help="a service running next to Synapse that plugins with the loopback permission may use as if it "
        "were on this computer, e.g. ollama=http://ollama:11434 for Autocompletion when Ollama runs in "
        "its own container (also SYNAPSE_LOCAL_SERVICES)",
    )
    ap.add_argument(
        "--pairing-link",
        action="store_true",
        help="ask the Synapse already running on this computer (same --port) for a one-time link that "
        "pairs a device, print it, then exit; for a server without a screen",
    )
    ap.add_argument("--data-dir", default=None, help="folder for the library data (default: ./data)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument(
        "--window",
        action="store_true",
        help="open the app in a window of its own, like a program (Synapse installed as an app by "
        "Chrome, else a Chrome/Edge/Brave app window); if Synapse runs already, just open the window",
    )
    ap.add_argument(
        "--quit-when-closed",
        action="store_true",
        help="stop the server once no Synapse window is open any more (the desktop shortcut uses this)",
    )
    ap.add_argument(
        "--make-shortcut",
        action="store_true",
        help="make a desktop shortcut that starts Synapse in its own window (with the other options "
        "given here, such as --port or --data-dir), then exit",
    )
    ap.add_argument(
        "--debug", action="store_true", help="Flask debug mode with auto-reload (this computer only: not with --lan)"
    )
    ap.add_argument(
        "--dev-plugins",
        metavar="DIR",
        help="also load the plugins in DIR (one folder each) and restart one whenever a file of it changes "
        "(for writing plugins; see docs/plugins.md)",
    )
    ap.add_argument(
        "--new-plugin",
        metavar="ID",
        help="make a new plugin to start from (in --dev-plugins DIR if given, else in data/plugins), then exit",
    )
    ap.add_argument(
        "--safe-mode",
        action="store_true",
        help="start without any plugins, e.g. to turn off one that keeps the app from working (Settings → Plugins)",
    )
    ap.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="NAME",
        help="also answer to this host name or address (e.g. your LAN IP with --host 0.0.0.0); "
        "127.0.0.1 and localhost always work",
    )
    a = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if a.debug else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("werkzeug").setLevel(logging.INFO if a.debug else logging.WARNING)

    if a.pairing_link:
        from backend.access.pairing_link import PairingLinkError, describe, request_pairing

        try:
            print(describe(request_pairing(a.port, tls=bool(a.cert))))
        except PairingLinkError as e:
            raise SystemExit(str(e)) from e
        return

    if a.container:
        container_mode(a)

    public_url = None
    if a.public_url:
        from backend.access.proxy import PublicUrlError, parse_public_url

        try:
            public_url = parse_public_url(a.public_url)
        except PublicUrlError as e:
            raise SystemExit(str(e)) from e
        if a.debug:
            # The tunnel puts this server on the internet; Flask's debugger console runs Python code.
            raise SystemExit("--debug cannot be combined with --public-url: the debugger would be on the internet.")

    if a.lan and a.host in ("127.0.0.1", "localhost"):
        a.host = "0.0.0.0"
    wildcard = a.host in ("0.0.0.0", "::")
    loopback_only = a.host in ("127.0.0.1", "localhost", "::1")
    if public_url and not a.container and (a.lan or not loopback_only):
        raise SystemExit(
            "--public-url cannot be combined with --lan or a non-loopback --host: "
            "direct network access would bypass the tunnel. Use --host 127.0.0.1, localhost or ::1."
        )
    if a.debug and not loopback_only:
        # Werkzeug's debugger answers before the guard's checks, and its console runs Python code:
        # on the network that would let anyone who reaches the port run code on this computer.
        raise SystemExit(
            "--debug cannot be combined with --lan or a --host that is not loopback: Flask's debugger "
            "would let anyone on the network run code on this computer. Use --debug on its own, or "
            "--lan without --debug."
        )

    if bool(a.cert) != bool(a.key):
        raise SystemExit("--cert and --key go together: give both, or neither.")
    tls = (a.cert, a.key) if a.cert and a.key else None
    scheme = "https" if tls else "http"
    url = app_url(a.host, a.port, tls=bool(tls))

    if a.new_plugin:
        from backend.plugins import PluginError
        from backend.plugins.scaffold import create

        parent = Path(a.dev_plugins).expanduser() if a.dev_plugins else make_config(a).plugins_dir
        try:
            folder = create(a.new_plugin, parent.resolve())
        except PluginError as e:
            raise SystemExit(str(e)) from e
        print(f"Made {folder}. Start Synapse with --dev-plugins {folder.parent} and turn it on in Settings → Plugins.")
        return
    if a.make_shortcut:
        make_shortcut(a)
        return
    if a.window:
        from backend.desktop.window import open_window, synapse_at

        running = synapse_at(url)
        if running:
            print(f"Synapse is already running at {url}: {open_window(url)}")
            return
        if running is False:
            raise SystemExit(f"Port {a.port} is used by another program. Choose another one with --port.")

    config = make_config(a)
    config.allowed_hosts = set(a.allow_host)
    # In a container the wildcard is the container's own network, which only the tunnel reaches:
    # no LAN names or addresses are accepted, as when listening on loopback.
    config.lan = not loopback_only and not a.container
    config.container = a.container
    config.public_url = public_url
    config.safe_mode = a.safe_mode
    config.dev_plugins_dir = Path(a.dev_plugins).expanduser().resolve() if a.dev_plugins else None
    if not wildcard and not loopback_only:
        config.allowed_hosts.add(a.host)  # started on a specific address: accept requests for it
    try:
        ensure_built(config.frontend_dir)
    except BuildError as e:
        raise SystemExit(str(e)) from e
    app = create_app(config)
    print(f"Synapse running at {url}  (library: {config.data_dir})  — Ctrl+C to stop", flush=True)
    if a.quit_when_closed:
        # Like Ctrl+C: the server finishes the requests it is answering and stops.
        app.extensions["synapse"].presence.watch(on_idle=_thread.interrupt_main)
        print("It stops by itself once no Synapse window is open any more.", flush=True)
    if config.lan:
        from backend.access import lan

        names = [n for n in [lan.local_hostname(), *lan.own_addresses()] if n]
        if names:
            print("On the network:  " + "  ".join(f"{scheme}://{n}:{a.port}/" for n in names))
        print("Other devices must be paired first: Settings → Devices → Add a Device (on this computer).")
        try:
            import segno  # noqa: F401
        except ImportError:
            print("Tip: pip install segno  (shows the pairing QR code; without it only the link works)")
    if public_url:
        print(f"Public address: {public_url}/  — requests through it need a signed-in device.", flush=True)
        port = f" --port {a.port}" if a.port != 8000 else ""
        if a.container:
            print(
                f"Pair the first device from the host with:  docker compose exec synapse python server.py --pairing-link{port}"
            )
        else:
            print(f"Pair the first device with:  python server.py --pairing-link{port}")
    if a.window:
        from backend.desktop.window import open_window

        opener = lambda u: print(f"Window: {open_window(u)}", flush=True)  # noqa: E731
        threading.Thread(target=_open_when_ready, args=(url, opener), daemon=True).start()
    elif not a.no_browser:
        threading.Thread(target=_open_when_ready, args=(url, webbrowser.open), daemon=True).start()
    # Werkzeug's threaded server (one thread per connection) is plenty for one person's devices,
    # and it serves HTTPS too.
    try:
        app.run(host=a.host, port=a.port, debug=a.debug, threaded=True, use_reloader=a.debug, ssl_context=tls)
    finally:
        app.extensions["synapse"].backups.stop()


# Options that make no sense, or would open a way around the tunnel, inside a container.
CONTAINER_REFUSES = (
    ("lan", "--lan"),
    ("cert", "--cert"),
    ("key", "--key"),
    ("window", "--window"),
    ("debug", "--debug"),
    ("allow_host", "--allow-host"),
    ("make_shortcut", "--make-shortcut"),
    ("quit_when_closed", "--quit-when-closed"),
)


def container_mode(a: argparse.Namespace) -> None:
    """``--container``: refuse what does not fit a container behind a tunnel, then listen on the
    container's network."""
    given = [flag for attr, flag in CONTAINER_REFUSES if getattr(a, attr)]
    if a.host != "127.0.0.1":
        given.append("--host")
    if given:
        raise SystemExit(
            f"--container cannot be combined with {', '.join(given)}: in a container Synapse is reached "
            "only through its tunnel, which needs none of them."
        )
    if not a.public_url:
        raise SystemExit(
            "--container needs --public-url, the HTTPS address the tunnel serves (like https://synapse.example.com)."
        )
    # The data folder is bind-mounted from the host: what Synapse writes there is readable only by its
    # owner, as the files in a home folder should be.
    os.umask(0o077)
    a.host = "0.0.0.0"  # the container's own network, which the tunnel's container connects through
    a.no_browser = True


def make_config(a: argparse.Namespace) -> Config:
    try:
        config = Config.from_env(a.data_dir)
        config.local_services.update(parse_local_service(s) for s in a.local_service)
    except LocalServiceError as e:
        raise SystemExit(str(e)) from e
    return config


def make_shortcut(a: argparse.Namespace) -> None:
    """``--make-shortcut``: the shortcut starts the server with the options given with it."""
    from backend.desktop.shortcut import ShortcutError
    from backend.desktop.shortcut import make_shortcut as make

    args: list[str] = []
    if a.port != 8000:
        args += ["--port", str(a.port)]
    if a.data_dir:
        args += ["--data-dir", str(Path(a.data_dir).expanduser().resolve())]
    if a.lan:
        args.append("--lan")
    elif a.host != "127.0.0.1":
        args += ["--host", a.host]
    if a.cert and a.key:
        args += ["--cert", str(Path(a.cert).resolve()), "--key", str(Path(a.key).resolve())]
    for h in a.allow_host:
        args += ["--allow-host", h]
    if a.public_url:
        args += ["--public-url", a.public_url]
    try:
        made = make(Path(__file__).resolve().parent, args, path_env=os.environ.get("PATH", ""))
    except ShortcutError as e:
        raise SystemExit(str(e)) from e
    print("Made the Synapse shortcut:\n" + "\n".join(f"  {p}" for p in made))
    print("Double-click it to open Synapse in its own window; closing the window stops the server.")


if __name__ == "__main__":
    main()
