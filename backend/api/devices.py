"""Paired devices (Settings → Devices) and the pairing itself. See backend/access/devices.py."""

from __future__ import annotations

import io

from flask import Blueprint, g, jsonify, request

from ..access import lan
from ..access.devices import COOKIE, PairingError
from ..access.guard import set_device_cookie
from ..access.proxy import client_addr, is_proxied, public_host
from . import admin, body, error, svc

bp = Blueprint("devices", __name__, url_prefix="/api/devices")


def _port() -> str:
    host = request.host
    if host.startswith("["):
        rest = host[host.find("]") + 1 :]
        return rest[1:] if rest.startswith(":") else ""
    return host.rsplit(":", 1)[1] if host.count(":") == 1 else ""


def addresses() -> list[dict]:
    """Where other devices can reach this server: the public address (``--public-url``) first, then
    — when it listens on the network — the .local name and IP addresses."""
    out = []
    public = svc().config.public_url
    if public:
        out.append({"label": public_host(public) or public, "url": public + "/"})
    if not svc().config.lan or is_proxied(request.environ):
        return out  # through the tunnel the network addresses would be the server's, not reachable here
    port = _port()
    suffix = f":{port}" if port and port not in ("80", "443") else ""
    scheme = request.scheme
    name = lan.local_hostname()
    if name:
        out.append({"label": name, "url": f"{scheme}://{name}{suffix}/"})
    for ip in lan.own_addresses():
        host = f"[{ip}]" if ":" in ip else ip
        out.append({"label": ip, "url": f"{scheme}://{host}{suffix}/"})
    return out


def _qr_svg(text: str) -> str:
    import segno

    buf = io.BytesIO()
    segno.make(text, error="m").save(
        buf,
        kind="svg",
        scale=5,
        border=2,
        dark="#000000",
        light="#ffffff",
        xmldecl=False,
        svgns=True,
        nl=False,
        omitsize=True,
    )  # viewBox: scales to its box
    return buf.getvalue().decode("utf-8")


@bp.get("")
def overview():
    current = (g.get("device") or {}).get("id")
    return jsonify(
        **admin.state(),
        lan=svc().config.other_devices,
        public_url=svc().config.public_url,
        current=current,
        devices=svc().devices.list(),
        addresses=addresses() if svc().config.other_devices else [],
    )


@bp.post("/pairing")
def new_pairing():
    """A one-time pairing code, as links with QR codes. On the computer running Synapse, or on a
    device that just confirmed with a passkey (``python server.py --pairing-link`` asks for one too)."""
    if (refused := admin.refusal("Devices can only be added on the computer running Synapse.")) is not None:
        return refused
    if not svc().config.other_devices:
        return error(
            "Synapse is only reachable from this computer. Start it with  python server.py --lan  "
            "(or --public-url behind a tunnel) to use it from other devices.",
            409,
        )
    code, expires = svc().devices.new_pairing_code()
    links = []
    for a in addresses():
        url = f"{a['url']}pair#code={code}"  # the code is in the fragment: never sent in a request line or logged
        try:
            qr = _qr_svg(url)
        except ImportError:
            qr = None
        links.append({"label": a["label"], "url": url, "qr": qr})
    return jsonify(expires=expires, links=links), 201  # qr is None without segno: the page says so


@bp.delete("/pairing")
def cancel_pairing():
    """Void the pairing code being shown (Cancel, or leaving Settings)."""
    if (refused := admin.refusal("Devices can only be added on the computer running Synapse.")) is not None:
        return refused
    svc().devices.cancel_pairing()
    return jsonify(ok=True)


@bp.post("/pair")
def pair():
    data = body()
    try:
        device, token = svc().devices.redeem(
            str(data.get("code") or ""),
            str(data.get("name") or ""),
            request.headers.get("User-Agent", ""),
            client_addr(request.environ),
        )
    except PairingError as e:
        return error(str(e), e.status)
    resp = jsonify(device=device)
    resp.status_code = 201
    set_device_cookie(resp, token)
    return resp


def _manage_refusal(dev_id: str):
    """The computer running Synapse manages every device; another device itself, and the others
    after confirming with a passkey."""
    if (g.get("device") or {}).get("id") == dev_id:
        return None
    return admin.refusal(MANAGE_REFUSED)


MANAGE_REFUSED = "Other devices can only be renamed or removed on the computer running Synapse."


@bp.patch("/<dev_id>")
def rename(dev_id):
    if (refused := _manage_refusal(dev_id)) is not None:
        return refused
    try:
        device = svc().devices.rename(dev_id, str(body().get("name") or ""))
    except PairingError as e:
        return error(str(e), e.status)
    if device is None:
        return error("No such device.", 404)
    return jsonify(device=device)


@bp.delete("/<dev_id>")
def revoke(dev_id):
    if (refused := _manage_refusal(dev_id)) is not None:
        return refused
    if not svc().devices.revoke(dev_id):
        return error("No such device.", 404)
    svc().confirmations.forget(dev_id)
    resp = jsonify(ok=True, devices=svc().devices.list())
    if (g.get("device") or {}).get("id") == dev_id:  # signed this device out
        resp.delete_cookie(COOKIE, path="/")
        resp.headers["X-Synapse-Signed-Out"] = "1"
    return resp
