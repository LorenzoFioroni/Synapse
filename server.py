"""Synapse — a local, web-based reference manager.

Run:   python server.py                 (http://127.0.0.1:8000, opens your browser)
       python server.py --port 5050 --no-browser
       python server.py --data-dir ~/MyLibrary
       python server.py --lan            (also reachable from your iPad/phone; pair them in Settings → Devices)

Your library (database, PDFs, full-text index, settings) lives in ./data
unless --data-dir or the SYNAPSE_DATA_DIR environment variable say otherwise.
"""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

from backend import create_app
from backend.config import Config
from backend.frontend import BuildError, ensure_built


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
    ap.add_argument("--data-dir", default=None, help="folder for the library data (default: ./data)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument(
        "--debug", action="store_true", help="Flask debug mode with auto-reload (this computer only: not with --lan)"
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

    config = Config.from_env(a.data_dir)
    config.allowed_hosts = set(a.allow_host)
    config.lan = not loopback_only
    if not wildcard and not loopback_only:
        config.allowed_hosts.add(a.host)  # started on a specific address: accept requests for it
    try:
        ensure_built(config.frontend_dir)
    except BuildError as e:
        raise SystemExit(str(e)) from e
    app = create_app(config)
    url = f"http://{'127.0.0.1' if wildcard else a.host}:{a.port}/"
    print(f"Synapse running at {url}  (library: {config.data_dir})  — Ctrl+C to stop")
    if config.lan:
        from backend.access import lan

        names = [n for n in [lan.local_hostname(), *lan.own_addresses()] if n]
        if names:
            print("On the network:  " + "  ".join(f"http://{n}:{a.port}/" for n in names))
        print("Other devices must be paired first: Settings → Devices → Add a Device (on this computer).")
        try:
            import segno  # noqa: F401
        except ImportError:
            print("Tip: pip install segno  (shows the pairing QR code; without it only the link works)")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if config.lan:
        # Several devices and long PDF downloads: a real WSGI server rather than Flask's development one.
        try:
            from waitress import serve
        except ImportError:
            print("Tip: pip install waitress  (a sturdier server for use from several devices)")
        else:
            logging.getLogger("waitress.queue").setLevel(logging.ERROR)
            serve(app, host=a.host, port=a.port, threads=12, channel_timeout=300, ident="Synapse")
            return
    app.run(host=a.host, port=a.port, debug=a.debug, threaded=True, use_reloader=a.debug)


if __name__ == "__main__":
    main()
