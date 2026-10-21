"""The app's windows saying hello and goodbye, so a server started by the desktop shortcut stops
with its last window (backend/desktop/presence.py)."""

from __future__ import annotations

from flask import Blueprint, jsonify

from . import body, svc

bp = Blueprint("window", __name__, url_prefix="/api/window")


def _client() -> str:
    return str(body().get("client") or "")[:64]


@bp.post("/ping")
def ping():
    """A window is open; ``watch`` says whether the server cares (otherwise the page stops asking)."""
    return jsonify(watch=svc().presence.ping(_client()))


@bp.post("/bye")
def bye():
    svc().presence.bye(_client())
    return "", 204
