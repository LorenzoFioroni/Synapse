"""Who may do the administrative actions (pairing, access tokens, passkeys, removing other devices):
the computer running Synapse, or a device that just confirmed with a passkey. See
backend/access/confirm.py."""

from __future__ import annotations

from flask import g, jsonify, request

from ..access.confirm import may_administer
from ..access.passkeys import PasskeyError, relying_party
from . import svc


def can_confirm() -> bool:
    """Whether the device asking could confirm with a passkey here (signed in, over HTTPS by name,
    and Synapse knows at least one passkey for this address)."""
    if g.get("this_computer") or not g.get("device"):
        return False
    try:
        rp_id, _ = relying_party(request.host, request.scheme)
    except PasskeyError:
        return False
    return svc().passkeys.count(rp_id) > 0


def allowed() -> bool:
    return may_administer(bool(g.get("this_computer")), g.get("device"), svc().confirmations)


def state() -> dict:
    """For the Settings pages: whether to offer the administrative buttons."""
    return {
        "this_computer": bool(g.get("this_computer")),
        "can_confirm": can_confirm(),
        "confirmed": allowed() and not g.get("this_computer"),
    }


def refusal(where: str):
    """None when this request may do an administrative action; else a 403. When the device can
    confirm with a passkey, the answer says so (``confirm: true``) and the app asks for it and tries
    again."""
    if allowed():
        return None
    if can_confirm():
        resp = jsonify(error="Confirm with your passkey to do this from another device.", confirm=True)
    else:
        resp = jsonify(
            error=f"{where} Or, on a device signed in with a passkey (over HTTPS), confirm with the passkey first."
        )
    resp.status_code = 403
    return resp
