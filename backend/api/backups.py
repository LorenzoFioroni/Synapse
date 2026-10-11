"""Backups of the library and notes (Settings → Backups). See backend/backup.py."""
from __future__ import annotations

from flask import Blueprint, jsonify

from ..backup import BackupError
from . import body, error, svc

bp = Blueprint("backups", __name__, url_prefix="/api/backups")


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
    name = str(body().get("name") or "")
    try:
        result = svc().backups.restore(name)
    except BackupError as e:
        return error(str(e))
    return jsonify(**result, **svc().backups.status())
