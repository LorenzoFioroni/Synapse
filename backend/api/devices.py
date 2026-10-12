"""Paired devices (Settings → Devices) and the pairing itself (issue #34). See backend/devices.py."""
from __future__ import annotations

import io

from flask import Blueprint, g, jsonify, request

from .. import lan
from ..devices import COOKIE, PairingError
from ..guard import set_device_cookie
from . import body, error, svc

bp = Blueprint("devices", __name__, url_prefix="/api/devices")


def _port() -> str:
    host = request.host
    if host.startswith("["):
        rest = host[host.find("]") + 1:]
        return rest[1:] if rest.startswith(":") else ""
    return host.rsplit(":", 1)[1] if host.count(":") == 1 else ""


def addresses() -> list[dict]:
    """Where other devices can reach this server: the .local name first, then IP addresses."""
    port = _port()
    suffix = f":{port}" if port and port not in ("80", "443") else ""
    scheme = request.scheme
    out = []
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
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#000000", light="#ffffff",
                                    xmldecl=False, svgns=True, nl=False)
    return buf.getvalue().decode("utf-8")


@bp.get("")
def overview():
    current = (g.get("device") or {}).get("id")
    return jsonify(lan=svc().config.lan, this_computer=bool(g.get("this_computer")), current=current,
                   devices=svc().devices.list(), addresses=addresses() if svc().config.lan else [])


@bp.post("/pairing")
def new_pairing():
    """A one-time pairing code, as links with QR codes. Only on the computer running Synapse."""
    if not g.get("this_computer"):
        return error("Devices can only be added on the computer running Synapse.", 403)
    if not svc().config.lan:
        return error("Synapse is only reachable from this computer. Start it with  python server.py --lan  "
                     "to use it from other devices.", 409)
    code, expires = svc().devices.new_pairing_code()
    links = []
    for a in addresses():
        url = f"{a['url']}pair#code={code}"   # the code is in the fragment: never sent in a request line or logged
        try:
            qr = _qr_svg(url)
        except ImportError:
            qr = None
        links.append({"label": a["label"], "url": url, "qr": qr})
    return jsonify(expires=expires, links=links), 201


@bp.post("/pair")
def pair():
    data = body()
    try:
        device, token = svc().devices.redeem(str(data.get("code") or ""), str(data.get("name") or ""),
                                             request.headers.get("User-Agent", ""), request.remote_addr or "")
    except PairingError as e:
        return error(str(e), e.status)
    resp = jsonify(device=device)
    resp.status_code = 201
    set_device_cookie(resp, token)
    return resp


@bp.patch("/<dev_id>")
def rename(dev_id):
    try:
        device = svc().devices.rename(dev_id, str(body().get("name") or ""))
    except PairingError as e:
        return error(str(e), e.status)
    if device is None:
        return error("No such device.", 404)
    return jsonify(device=device)


@bp.delete("/<dev_id>")
def revoke(dev_id):
    if not svc().devices.revoke(dev_id):
        return error("No such device.", 404)
    resp = jsonify(ok=True, devices=svc().devices.list())
    if (g.get("device") or {}).get("id") == dev_id:   # signed this device out
        resp.delete_cookie(COOKIE, path="/")
        resp.headers["X-Synapse-Signed-Out"] = "1"
    return resp
