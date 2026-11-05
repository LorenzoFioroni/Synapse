"""The MCP endpoint for AI apps on other computers (``/mcp``) and its access tokens
(Settings → AI Access). See backend/mcp/http.py and backend/access/tokens.py."""

from __future__ import annotations

from flask import Blueprint, Response, g, jsonify, request

from ..access.tokens import TokenError
from . import admin, body, error, svc
from .devices import addresses

bp = Blueprint("mcp", __name__)


@bp.post("/mcp")
def mcp():
    # The guard has checked the access token already.
    token = g.get("access_token") or {}
    status, reply = (
        svc().mcp_http().handle(request.get_data(), request.headers.get("MCP-Protocol-Version"), caller=token.get("id"))
    )
    if reply is None:
        return Response(status=status)
    return jsonify(reply), status


@bp.route("/mcp", methods=["GET", "DELETE"])
def mcp_other():
    # No event stream and no sessions: every answer comes back with its POST.
    return Response(status=405, headers={"Allow": "POST"})


def _endpoints() -> list[dict]:
    """The URLs an AI app can use: on the network when it is reachable there, else this computer's."""
    found = addresses() if svc().config.other_devices else [{"label": request.host, "url": request.host_url}]
    return [{"label": a["label"], "url": a["url"] + "mcp"} for a in found]


@bp.get("/api/mcp/tokens")
def tokens():
    return jsonify(
        **admin.state(),
        lan=svc().config.other_devices,
        endpoints=_endpoints(),
        tokens=svc().tokens.list(),
    )


@bp.post("/api/mcp/tokens")
def new_token():
    """A new access token, shown this once. Only on the computer running Synapse."""
    if (refused := admin.refusal("Access tokens can only be made on the computer running Synapse.")) is not None:
        return refused
    try:
        data = body()
        entry, token = svc().tokens.create(str(data.get("name") or ""), scope=str(data.get("scope", "mcp")))
    except TokenError as e:
        return error(str(e), e.status)
    return jsonify(token=token, entry=entry, tokens=svc().tokens.list()), 201


@bp.delete("/api/mcp/tokens/<tok_id>")
def revoke_token(tok_id):
    if (refused := admin.refusal("Access tokens can only be removed on the computer running Synapse.")) is not None:
        return refused
    if not svc().tokens.revoke(tok_id):
        return error("No such token.", 404)
    return jsonify(ok=True, tokens=svc().tokens.list())
