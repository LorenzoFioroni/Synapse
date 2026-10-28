"""Passkeys (Settings → Devices) and signing a device in with one. See backend/access/passkeys.py."""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from ..access.guard import set_device_cookie
from ..access.passkeys import PasskeyError, relying_party
from ..access.proxy import client_addr
from . import admin, body, error, svc

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
        **admin.state(),
        rp_id=rp_id,
        unavailable=why,
        passkeys=svc().passkeys.list(),
    )


def _may_add():
    """Who may enrol a passkey. The computer running Synapse always may. Another device may enrol a
    passkey only straight after pairing (``Devices.recently_paired`` — the pairing code is issued on
    this computer, or over SSH with ``--pairing-link``, so this means a deliberate pairing just
    happened) or after confirming with a passkey it already holds, like the other administrative
    actions. A stale device cookie can neither pair itself nor confirm, so a stolen cookie cannot turn
    itself into a passkey and gain lasting access."""
    if admin.allowed():
        return None
    device = g.get("device")
    if device is not None and svc().devices.recently_paired(device.get("id")):
        return None
    return admin.refusal("Passkeys can only be added on the computer running Synapse, or on a device you just paired.")


@bp.post("/register/options")
def register_options():
    try:
        rp_id, origin = _site()
        if (refused := _may_add()) is not None:
            return refused
        return jsonify(svc().passkeys.registration_options(rp_id, origin, str(body().get("name") or "")))
    except PasskeyError as e:
        return error(str(e), e.status)


@bp.post("/register")
def register():
    try:
        rp_id, origin = _site()
        if (refused := _may_add()) is not None:
            return refused
        passkey = svc().passkeys.register(rp_id, origin, _credential(), client_addr(request.environ))
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
            client_addr(request.environ),
        )
    except PasskeyError as e:
        return error(str(e), e.status)
    resp = jsonify(device=device)
    resp.status_code = 201
    set_device_cookie(resp, token)
    return resp


@bp.post("/confirm/options")
def confirm_options():
    """A signed-in device proving again that its user holds a passkey, before an administrative
    action (backend/access/confirm.py)."""
    if not g.get("device"):
        return error("Only a signed-in device confirms with a passkey.", 403)
    try:
        rp_id, origin = _site()
        return jsonify(svc().passkeys.login_options(rp_id, origin, purpose="confirm"))
    except PasskeyError as e:
        return error(str(e), e.status)


@bp.post("/confirm")
def confirm():
    device = g.get("device")
    if not device:
        return error("Only a signed-in device confirms with a passkey.", 403)
    try:
        rp_id, origin = _site()
        svc().passkeys.confirm(rp_id, origin, _credential(), client_addr(request.environ))
    except PasskeyError as e:
        return error(str(e), e.status)
    return jsonify(until=svc().confirmations.confirm(device["id"]))


@bp.delete("/<pk_id>")
def remove(pk_id):
    if (refused := admin.refusal("Passkeys can only be removed on the computer running Synapse.")) is not None:
        return refused
    if not svc().passkeys.remove(pk_id):
        return error("No such passkey.", 404)
    return jsonify(ok=True, passkeys=svc().passkeys.list())
