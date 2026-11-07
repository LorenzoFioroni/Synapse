"""Local and remote backups (Settings → Backups). See backend/backup/."""

from __future__ import annotations

from flask import Blueprint, Response, g, jsonify, request

from ..backup import BackupError
from ..backup.remote import PROVIDERS
from ..online.backup_oauth import CALLBACK, AuthorizationError
from . import admin, body, error, svc

bp = Blueprint("backups", __name__, url_prefix="/api/backups")
REFUSED = "Remote backup connections can only be changed on the computer running Synapse."


def _callback() -> str:
    origin = svc().config.public_url or request.host_url.rstrip("/")
    return origin + CALLBACK


def _owner() -> str:
    device = g.get("device")
    return device["id"] if device else "this-computer"


@bp.errorhandler(BackupError)
@bp.errorhandler(AuthorizationError)
def remote_error(exc):
    return error(str(exc))


@bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.get("")
def status():
    return jsonify(svc().backups.status())


@bp.post("")
def create():
    try:
        entry = svc().backups.create("manual")
    except BackupError as e:
        return error(str(e), 500)
    return jsonify(backup=entry, **svc().backups.status()), 201


@bp.post("/restore")
def restore():
    data = body()
    name = str(data.get("name") or "")
    try:
        if data.get("source", "local") not in ("local", "remote"):
            return error("Choose a local or remote backup.")
        result = svc().backups.restore_remote(name) if data.get("source") == "remote" else svc().backups.restore(name)
    except BackupError as e:
        return error(str(e))
    return jsonify(**result, **svc().backups.status())


@bp.get("/remote")
def remote_configuration():
    return jsonify(configuration=svc().backups.remote.configuration(), providers=PROVIDERS, callback=_callback())


@bp.put("/remote")
def configure_remote():
    if (refused := admin.refusal(REFUSED)) is not None:
        return refused
    result = svc().backups.remote.save(body())
    svc().backups.delivery.retry()
    return jsonify(configuration=result)


@bp.post("/remote/test")
def test_remote():
    if (refused := admin.refusal(REFUSED)) is not None:
        return refused
    svc().backups.remote.test()
    return jsonify(ok=True)


@bp.post("/remote/authorize")
def authorize_remote():
    if (refused := admin.refusal(REFUSED)) is not None:
        return refused
    return jsonify(svc().backups.remote.authorize(_owner(), _callback()))


@bp.get("/remote/callback")
def authorization_callback():
    try:
        svc().backups.remote.finish_authorization(_owner(), request.args.to_dict())
        svc().backups.delivery.retry()
        message = "Account connected. You can close this tab and return to Settings → Backups."
        status_code = 200
    except (AuthorizationError, BackupError) as exc:
        message, status_code = str(exc), 400
    from markupsafe import escape

    return Response(
        '<!doctype html><meta charset="utf-8"><title>Synapse — Backup connection</title>'
        f'<h1>Backup connection</h1><p>{escape(message)}</p><p><a href="/">Return to Synapse</a></p>',
        status=status_code,
        mimetype="text/html",
    )


@bp.get("/remote/archives")
def remote_archives():
    return jsonify(backups=svc().backups.remote_list())


@bp.get("/remote/sets")
def remote_backup_sets():
    return jsonify(sets=svc().backups.remote.backup_sets())


@bp.post("/remote/retry")
def retry_remote():
    svc().backups.delivery.retry()
    return jsonify(ok=True)
