"""Serving the web app: the pages and static files of ``frontend/``.

Pages, scripts and styles are sent with ``Cache-Control: no-store`` so that a changed frontend
is picked up on the next reload, without a stale copy from the browser cache.
"""

from __future__ import annotations

from flask import Blueprint, current_app, send_from_directory

NO_CACHE = "no-store, no-cache, must-revalidate, max-age=0"

bp = Blueprint("frontend", __name__)


def _frontend():
    return current_app.extensions["synapse"].config.frontend_dir


def _page(name: str):
    resp = send_from_directory(_frontend(), name)
    resp.headers["Cache-Control"] = NO_CACHE
    return resp


@bp.get("/")
def index():
    return _page("index.html")


@bp.get("/pair")
def pair_page():
    """Opened from the pairing QR code on another device; see backend/access/devices.py."""
    return _page("pair.html")


@bp.get("/favicon.ico")
def favicon():
    return send_from_directory(_frontend() / "assets", "favicon.ico", mimetype="image/vnd.microsoft.icon")


@bp.get("/<path:path>")
def static_files(path):
    resp = send_from_directory(_frontend(), path)
    if path.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = NO_CACHE
    return resp
