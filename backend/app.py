"""Flask application factory."""
from __future__ import annotations

import logging

from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

from .config import Config
from .repository import NotFound
from .services import Services

log = logging.getLogger(__name__)

NO_CACHE = "no-store, no-cache, must-revalidate, max-age=0"


def create_app(config: Config | None = None, *, start_indexer: bool = True) -> Flask:
    config = config or Config.from_env()
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024
    services = Services(config)
    app.extensions["zclone"] = services

    from . import guard
    guard.install(app, config.allowed_hosts)   # refuse requests from other websites (#17)

    from .api import register_blueprints
    register_blueprints(app)

    frontend = config.frontend_dir

    # -- what changed, for the other windows and devices (#34; backend/changes.py) --------------
    from flask import request
    from .changes import CLIENT_HEADER

    @app.get("/api/changes")
    def changes():
        try:
            since = int(request.args["since"]) if request.args.get("since") not in (None, "") else None
        except ValueError:
            since = None
        return jsonify(services.changes.since(since, request.args.get("epoch") or None, request.headers.get(CLIENT_HEADER)))

    @app.after_request
    def _notes_changed(resp):
        if (request.method in ("POST", "PUT", "PATCH", "DELETE") and resp.status_code < 400
                and request.path.startswith("/api/notes")):
            services.changes.record({"notes"}, request.headers.get(CLIENT_HEADER))
        return resp

    @app.get("/")
    def index():
        resp = send_from_directory(frontend, "index.html")
        resp.headers["Cache-Control"] = NO_CACHE
        return resp

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(frontend / "assets", "favicon.ico", mimetype="image/vnd.microsoft.icon")

    @app.get("/<path:path>")
    def static_files(path):
        resp = send_from_directory(frontend, path)
        if path.endswith((".html", ".js", ".css")):
            resp.headers["Cache-Control"] = NO_CACHE
        return resp

    @app.errorhandler(NotFound)
    def not_found(e):
        return jsonify(error="Item not found"), 404

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        from flask import request
        if request.path.startswith("/api/"):
            return jsonify(error=e.description or e.name), e.code
        return e

    # Annotations saved by older versions as SVG markup become records (once, after a backup).
    try:
        from .annotations import migrate_legacy
        migrate_legacy(services)
    except Exception:
        log.exception("Converting old annotations failed; the old data is kept")

    if start_indexer and services.settings.get("fulltext_auto_index"):
        services.indexer.sync()
    if start_indexer:   # background work (tests create the app without it)
        services.attachments.start()
        services.backups.start()
    return app
