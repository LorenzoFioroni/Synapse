"""Serving the web app: the pages and static files of ``frontend/``, and building its scripts.

Pages, scripts and styles are sent with ``Cache-Control: no-store`` so that a changed frontend
is picked up on the next reload, without a stale copy from the browser cache.

The scripts are written in TypeScript (``frontend/src``) and compiled to ``frontend/js``, which
is not committed. ``ensure_built`` compiles them when ``js/`` is missing or older than the sources,
so ``python server.py`` keeps working after a ``git pull`` (it needs Node.js and, once,
``npm ci`` in ``frontend/``).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from flask import Blueprint, current_app, send_from_directory

log = logging.getLogger(__name__)

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


# ----------------------------------------------------------------------------- build
BUILD_INPUTS = ("src", "build.mjs", "package.json", "tsconfig.json")


def _newest(paths) -> float:
    newest = 0.0
    for p in paths:
        if p.is_dir():
            newest = max([newest] + [f.stat().st_mtime for f in p.rglob("*") if f.is_file()])
        elif p.exists():
            newest = max(newest, p.stat().st_mtime)
    return newest


def needs_build(frontend: Path) -> bool:
    """True when js/ is missing or older than anything it is built from."""
    stamp = frontend / "js" / ".build-stamp"
    if not stamp.exists():
        return True
    return _newest(frontend / name for name in BUILD_INPUTS) > stamp.stat().st_mtime


class BuildError(RuntimeError):
    """The scripts are missing or out of date and cannot be built here; the message says what to do."""


def ensure_built(frontend: Path) -> bool:
    """Build js/ if it is out of date. Returns True if it built. Raises BuildError when js/ is
    missing and cannot be built; when it is only out of date, logs a warning and serves it as is."""
    if not needs_build(frontend):
        return False
    how = f"cd {frontend} && npm ci && npm run build"
    node = shutil.which("node")
    if node and (frontend / "node_modules" / "esbuild").exists():
        log.info("Building the frontend (frontend/src changed)…")
        r = subprocess.run([node, "build.mjs"], cwd=frontend, capture_output=True, text=True)
        if r.returncode == 0:
            return True
        raise BuildError(f"Building the frontend failed:\n{r.stdout}{r.stderr}")
    reason = "Node.js is not installed" if not node else "its build tools are not installed"
    if (frontend / "js" / "main.js").exists():
        log.warning("frontend/src changed but %s, so the old build is served. To rebuild: %s", reason, how)
        return False
    raise BuildError(
        f"The web app's scripts have not been built yet, and {reason}. Install Node.js, then run:\n  {how}"
    )
