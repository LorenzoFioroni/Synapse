"""HTTP API (JSON). One blueprint per area; all business logic lives outside this package."""

from __future__ import annotations

from flask import Flask, current_app, jsonify, request

from ..online.http import GENERIC_FAILURE, message_of  # noqa: F401  (re-exported for the blueprints)
from ..services import Services


def svc() -> Services:
    return current_app.extensions["synapse"]


def body() -> dict:
    return request.get_json(silent=True) or {}


def error(message: str, status: int = 400):
    return jsonify(error=message), status


def failure(e: BaseException, status: int = 400):
    """The answer for one of Synapse's own errors (see ``message_of``)."""
    return error(message_of(e), status)


def ids_from_body() -> list[str]:
    data = body()
    ids = data.get("ids")
    if ids is None and data.get("id"):
        ids = [data["id"]]
    return [str(i) for i in (ids or [])]


def register_blueprints(app: Flask) -> None:
    from . import (
        attachments,
        backups,
        changes,
        companion,
        connector,
        devices,
        io,
        items,
        mcp,
        metadata,
        notes,
        organize,
        passkeys,
        pdf_annotations,
        plugins,
        references,
        settings,
        tasks,
        updates,
        window,
    )

    for module in (
        items,
        attachments,
        settings,
        io,
        metadata,
        organize,
        connector,
        notes,
        references,
        backups,
        pdf_annotations,
        devices,
        passkeys,
        mcp,
        changes,
        companion,
        tasks,
        window,
        plugins,
        updates,
    ):
        app.register_blueprint(module.bp)
