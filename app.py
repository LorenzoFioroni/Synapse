"""Zotero Library Cleaner — review-before-write metadata & PDF clean-up.

Run:  python app.py            (opens http://127.0.0.1:5000 in your browser)
      python app.py --port 5050 --no-browser
"""
from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

from zcleaner.server import create_app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--debug", action="store_true", help="verbose logging")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.debug else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    web = create_app(a.port)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Zotero Library Cleaner running at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    web.run(host="127.0.0.1", port=a.port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
