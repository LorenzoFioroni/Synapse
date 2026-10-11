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

    from .api import register_blueprints
    register_blueprints(app)

    frontend = config.frontend_dir

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

    if start_indexer and services.settings.get("fulltext_auto_index"):
        services.indexer.sync()
    if start_indexer:   # background work (tests create the app without it)
        services.attachments.start()
        services.backups.start()
    return app
