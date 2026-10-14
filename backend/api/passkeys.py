"""Passkeys (Settings → Devices) and signing a device in with one. See backend/access/passkeys.py."""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from ..access.guard import set_device_cookie
from ..access.passkeys import PasskeyError, relying_party
from . import body, error, svc

bp = Blueprint("passkeys", __name__, url_prefix="/api/passkeys")


def _site() -> tuple[str, str]:
    return relying_party(request.host, request.scheme)


def _credential() -> dict:
    cred = body().get("credential")
    if not isinstance(cred, dict):
        raise PasskeyError("The passkey answer is missing.")
    return cred


@bp.get("")
def overview():
    """The passkeys, and whether this page can add or use one (``unavailable`` says why not)."""
    try:
        rp_id, why = _site()[0], None
    except PasskeyError as e:
        rp_id, why = None, str(e)
    return jsonify(
        this_computer=bool(g.get("this_computer")),
        rp_id=rp_id,
        unavailable=why,
        passkeys=svc().passkeys.list(),
    )


@bp.post("/register/options")
def register_options():
    try:
        rp_id, origin = _site()
        return jsonify(svc().passkeys.registration_options(rp_id, origin, str(body().get("name") or "")))
    except PasskeyError as e:
        return error(str(e), e.status)


@bp.post("/register")
def register():
    try:
        rp_id, origin = _site()
        passkey = svc().passkeys.register(rp_id, origin, _credential(), request.remote_addr or "")
    except PasskeyError as e:
        return error(str(e), e.status)
    return jsonify(passkey=passkey), 201


@bp.post("/login/options")
def login_options():
    try:
        rp_id, origin = _site()
        return jsonify(svc().passkeys.login_options(rp_id, origin))
    except PasskeyError as e:
        return error(str(e), e.status)


@bp.post("/login")
def login():
    """Sign this device in with a passkey: it becomes a device, as if it had been paired."""
    try:
        rp_id, origin = _site()
        device, token, _ = svc().passkeys.sign_in(
            rp_id,
            origin,
            _credential(),
            str(body().get("name") or ""),
            request.headers.get("User-Agent", ""),
            request.remote_addr or "",
        )
    except PasskeyError as e:
        return error(str(e), e.status)
    resp = jsonify(device=device)
    resp.status_code = 201
    set_device_cookie(resp, token)
    return resp


@bp.delete("/<pk_id>")
def remove(pk_id):
    if not g.get("this_computer"):
        return error("Passkeys can only be removed on the computer running Synapse.", 403)
    if not svc().passkeys.remove(pk_id):
        return error("No such passkey.", 404)
    return jsonify(ok=True, passkeys=svc().passkeys.list())
