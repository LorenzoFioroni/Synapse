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
# pdf.js is served as ES modules and WebAssembly; browsers refuse modules sent as text/plain, and
# older Pythons do not know these types
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")


def create_app(config: Config | None = None, *, start_indexer: bool = True) -> Flask:
    """The app for ``config``. Tests pass ``start_indexer=False`` to skip the background work."""
    config = config or Config.from_env()
    if config.public_url and config.lan:
        raise ValueError("public_url requires loopback-only listening; LAN access would bypass the tunnel")
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024
    services = Services(config)
    app.extensions["synapse"] = services

    from .access import guard
    from .access.proxy import ProxyMiddleware

    app.wsgi_app = ProxyMiddleware(app.wsgi_app, config.public_url)  # type: ignore[method-assign]
    guard.install(app, config.allowed_hosts, config.lan, public_url=config.public_url)

    from flask import g

    from .library.agent import reset_device, set_device

    @app.before_request
    def _device():  # after the guard: the notes history credits edits to the device making them
        dev = g.get("device")
        g.device_token = set_device(dev.get("name") if dev else None)

    @app.teardown_request
    def _device_done(_exc):
        token = g.pop("device_token", None)
        if token is not None:
            try:
                reset_device(token)
            except ValueError:  # set in another context (should not happen); nothing to undo
                pass

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

    # Plugins that are on start now (none with --safe-mode); see backend/plugins/.
    services.plugins.start("app")

    if start_indexer and services.settings.get("fulltext_auto_index"):
        services.indexer.sync()
    if start_indexer:
        services.attachments.start()
        services.backups.start()
        services.note_service.history.start()  # checkpoints of idle edits, changes made outside
    return app
