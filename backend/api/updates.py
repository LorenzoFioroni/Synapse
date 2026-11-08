"""Updates of Synapse itself (Settings → Updates). See backend/online/updates.py."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..online.updates import UpdateError
from . import admin, failure, svc

# Installing an update replaces the code that runs on the server, like changing plugins, so another
# device may start it only after confirming with a passkey (backend/access/confirm.py).
INSTALL_REFUSED = "Synapse can only be updated on the computer running it."

bp = Blueprint("updates", __name__, url_prefix="/api/updates")


@bp.get("")
def status():
    return jsonify(svc().updates.status(refresh=request.args.get("refresh") == "1"))


@bp.post("/install")
def install():
    if (refused := admin.refusal(INSTALL_REFUSED)) is not None:
        return refused
    try:
        started = svc().updates.install()
    except UpdateError as e:
        return failure(e, 409)
    return jsonify(started=started, **svc().updates.status()), 202
