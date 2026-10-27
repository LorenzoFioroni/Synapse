"""Compatibility URLs for clients using /api/tasks before the optional Tasks plugin."""

from flask import Blueprint

from .plugins import plugin_route

bp = Blueprint("tasks", __name__, url_prefix="/api")


@bp.route("/tasks", defaults={"rest": ""}, methods=["GET", "POST"])
@bp.route("/tasks/<path:rest>", methods=["GET", "POST", "PATCH"])
def tasks_route(rest: str):
    return plugin_route("tasks", rest)
