"""HTTP API (JSON). One blueprint per area; all business logic lives outside this package."""

from __future__ import annotations

from flask import Flask, current_app, jsonify, request

from ..services import Services


def svc() -> Services:
    return current_app.extensions["synapse"]


def body() -> dict:
    return request.get_json(silent=True) or {}


def error(message: str, status: int = 400):
    return jsonify(error=message), status


GENERIC_FAILURE = "The request failed; the details are in Synapse's log."


def message_of(e: BaseException) -> str:
    """What one of Synapse's own errors says to the user: the one message it was raised with.

    Synapse's error types are raised with a single sentence written for the user. Anything else, such
    as an OSError's number and file name or an error raised without a message, is not shown: the
    answer can go to another device or an AI app, and the log has the details."""
    args = e.args
    if len(args) == 1 and isinstance(args[0], str) and args[0].strip():
        return args[0]
    return GENERIC_FAILURE


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
        tasks,
        window,
        plugins,
        updates,
    ):
        app.register_blueprint(module.bp)
