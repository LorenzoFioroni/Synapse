"""Plugins: Settings → Plugins (``/api/plugin-host``) and the routes plugins serve
(``/api/plugins/<id>/…``)."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import HTTPException

from ..plugins import PluginError
from . import body, error, svc

bp = Blueprint("plugins", __name__, url_prefix="/api")


def _host():
    return svc().plugins


@bp.get("/plugin-host")
def overview():
    return jsonify(_host().overview())


@bp.post("/plugin-host/<pid>/enable")
def enable(pid: str):
    try:
        return jsonify(plugin=_host().enable(pid))
    except PluginError as e:
        return error(str(e))


@bp.post("/plugin-host/<pid>/disable")
def disable(pid: str):
    try:
        return jsonify(plugin=_host().disable(pid))
    except PluginError as e:
        return error(str(e))


@bp.post("/plugin-host/<pid>/restart")
def restart(pid: str):
    try:
        return jsonify(plugin=_host().restart(pid))
    except PluginError as e:
        return error(str(e))


@bp.put("/plugin-host/<pid>/settings")
def settings(pid: str):
    try:
        return jsonify(values=_host().set_settings(pid, body()))
    except PluginError as e:
        return error(str(e))


@bp.put("/plugin-host/<pid>/remote-mcp")
def remote_mcp(pid: str):
    try:
        return jsonify(plugin=_host().set_remote_mcp(pid, bool(body().get("on"))))
    except PluginError as e:
        return error(str(e))


@bp.get("/plugin-host/<pid>/log")
def plugin_log(pid: str):
    try:
        lines = max(1, min(int(request.args.get("lines", 500)), 2000))
        return jsonify(lines=_host().log_lines(pid, lines))
    except (PluginError, ValueError) as e:
        return error(str(e))


@bp.route("/plugins/<pid>/", defaults={"rest": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@bp.route("/plugins/<pid>/<path:rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def plugin_route(pid: str, rest: str):
    host = _host()
    try:
        fn, args = host.route(pid, "/" + rest, request.method)
        result = host.run(pid, f"{request.method} /{rest}", fn, **args)
    except PluginError as e:
        return error(str(e))
    except HTTPException as e:
        return error(e.description or e.name, e.code or 500)
    except Exception:
        return error("The plugin failed; see its log in Settings → Plugins.", 500)
    if isinstance(result, Response | tuple):
        return result
    if result is None:
        return jsonify(ok=True)
    if isinstance(result, str):
        return Response(result, mimetype="text/plain")
    return jsonify(result)
