"""Graph: the graph of notes and the papers they cite (a contributed plugin). Its data (``build.py``) is
derived from the reference lists and the notes; nothing is stored. The view is the frontend
part (plugins/graph/frontend/)."""

from __future__ import annotations

from backend.plugins import Plugin

from .build import build_graph


def activate(plugin: Plugin) -> None:
    @plugin.route("/data")
    def data():
        """Papers, the works they cite and notes, with their links."""
        return build_graph(plugin)

    @plugin.route("/add", methods=["POST"])
    def add():
        from flask import request

        return plugin.actions.add(request.get_json(silent=True) or {})
