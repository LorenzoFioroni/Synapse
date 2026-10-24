"""Synapse — a local, web-based reference manager.

Run:   python server.py                 (http://127.0.0.1:8000, opens your browser)
       python server.py --port 5050 --no-browser
       python server.py --data-dir ~/MyLibrary
       python server.py --lan            (also reachable from your iPad/phone; pair them in Settings → Devices)
       python server.py --lan --cert mac.local.pem --key mac.local-key.pem
                                         (HTTPS, needed to sign devices in with passkeys)
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
import threading
import webbrowser
from pathlib import Path

from backend import create_app
from backend.config import Config
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

    if a.lan and a.host in ("127.0.0.1", "localhost"):
        a.host = "0.0.0.0"
    wildcard = a.host in ("0.0.0.0", "::")
    loopback_only = a.host in ("127.0.0.1", "localhost", "::1")
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

        parent = Path(a.dev_plugins).expanduser() if a.dev_plugins else Config.from_env(a.data_dir).plugins_dir
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

    config = Config.from_env(a.data_dir)
    config.allowed_hosts = set(a.allow_host)
    config.lan = not loopback_only
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
    if a.window:
        from backend.desktop.window import open_window

        threading.Timer(1.0, lambda: print(f"Window: {open_window(url)}", flush=True)).start()
    elif not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # Werkzeug's threaded server (one thread per connection) is plenty for one person's devices,
    # and it serves HTTPS too.
    app.run(host=a.host, port=a.port, debug=a.debug, threaded=True, use_reloader=a.debug, ssl_context=tls)


def make_shortcut(a: argparse.Namespace) -> None:
    """``--make-shortcut``: the shortcut starts the server with the options given with it."""
    import os

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
    try:
        made = make(Path(__file__).resolve().parent, args, path_env=os.environ.get("PATH", ""))
    except ShortcutError as e:
        raise SystemExit(str(e)) from e
    print("Made the Synapse shortcut:\n" + "\n".join(f"  {p}" for p in made))
    print("Double-click it to open Synapse in its own window; closing the window stops the server.")


if __name__ == "__main__":
    main()
