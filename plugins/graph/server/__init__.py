"""Graph: the graph of notes and the papers they cite (a core plugin). Its data (``build.py``) is
derived from the reference lists and the notes; nothing is stored. The view is the frontend
part (frontend/src/plugins/core/graph/)."""

from __future__ import annotations

from backend.plugins import Plugin

from .build import build_graph


def activate(plugin: Plugin) -> None:
    @plugin.route("/data")
    def data():
        """Papers, the works they cite and notes, with their links."""
        return build_graph(plugin.synapse)
