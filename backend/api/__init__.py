"""HTTP API (JSON). One blueprint per area; all business logic lives outside this package."""
from __future__ import annotations

from flask import Flask, current_app, jsonify, request

from ..services import Services


def svc() -> Services:
    return current_app.extensions["zclone"]


def body() -> dict:
    return request.get_json(silent=True) or {}


def error(message: str, status: int = 400):
    return jsonify(error=message), status


def ids_from_body() -> list[str]:
    data = body()
    ids = data.get("ids")
    if ids is None and data.get("id"):
        ids = [data["id"]]
    return [str(i) for i in (ids or [])]


def register_blueprints(app: Flask) -> None:
    from . import attachments, connector, io, items, metadata, notes, organize, references, settings

    for module in (items, attachments, settings, io, metadata, organize, connector, notes, references):
        app.register_blueprint(module.bp)
