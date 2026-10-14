"""Flask application factory: builds the app from the services, the access guard, the JSON API
blueprints and the frontend, and starts the background work."""

from __future__ import annotations

import logging
import mimetypes

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .config import Config
from .library.repository import NotFound
from .services import Services

log = logging.getLogger(__name__)
mimetypes.add_type("application/manifest+json", ".webmanifest")


def create_app(config: Config | None = None, *, start_indexer: bool = True) -> Flask:
    """The app for ``config``. Tests pass ``start_indexer=False`` to skip the background work."""
    config = config or Config.from_env()
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024
    services = Services(config)
    app.extensions["synapse"] = services

    from .access import guard

    guard.install(app, config.allowed_hosts, config.lan)

    from . import frontend
    from .api import register_blueprints

    register_blueprints(app)
    app.register_blueprint(frontend.bp)  # last: its catch-all serves the static files

    @app.errorhandler(NotFound)
    def not_found(e):
        return jsonify(error="Item not found"), 404

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        if request.path.startswith("/api/"):
            return jsonify(error=e.description or e.name), e.code
        return e

    # Annotations saved by older versions as SVG markup become records (once, after a backup).
    try:
        from .library.annotations_legacy import migrate_legacy

        migrate_legacy(services)
    except Exception:
        log.exception("Converting old annotations failed; the old data is kept")

    if start_indexer and services.settings.get("fulltext_auto_index"):
        services.indexer.sync()
    if start_indexer:
        services.attachments.start()
        services.backups.start()
    return app
