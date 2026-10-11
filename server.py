"""Synapse — a local, web-based reference manager.

Run:   python server.py                 (http://127.0.0.1:8000, opens your browser)
       python server.py --port 5050 --no-browser
       python server.py --data-dir ~/MyLibrary

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data-dir", default=None, help="folder for the library data (default: ./data)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--debug", action="store_true", help="Flask debug mode with auto-reload")
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="also answer to this host name or address (e.g. your LAN IP with --host 0.0.0.0); "
                         "127.0.0.1 and localhost always work")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.INFO if a.debug else logging.WARNING)

    config = Config.from_env(a.data_dir)
    config.allowed_hosts = set(a.allow_host)
    if a.host not in ("0.0.0.0", "::", "127.0.0.1", "localhost", "::1"):
        config.allowed_hosts.add(a.host)   # started on a specific address: accept requests for it
    app = create_app(config)
    url = f"http://{a.host}:{a.port}/"
    print(f"Synapse running at {url}  (library: {config.data_dir})  — Ctrl+C to stop")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=a.host, port=a.port, debug=a.debug, threaded=True, use_reloader=a.debug)


if __name__ == "__main__":
    main()
