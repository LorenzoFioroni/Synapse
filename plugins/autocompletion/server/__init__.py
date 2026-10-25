"""Autocompletion in notes, from a model served by Ollama on this computer (a core plugin).

`ollama.py` is the HTTP client (and checks that Ollama's address is on this computer),
`prompt.py` turns the text around the cursor into a request and cleans what comes back, and
`completer.py` checks that Ollama and the model are there before anything is asked of them, and
keeps requests from piling up. The frontend (frontend/src/plugins/core/autocompletion/) shows the
suggestions in the notes editor and its section in Settings.
"""

from __future__ import annotations

from backend.plugins import Plugin

from .completer import Completer
from .ollama import Unavailable

__all__ = ["Completer", "Unavailable", "activate"]

MAX_TEXT = 200_000  # characters of either side sent per request (only the end near the cursor is used)


def activate(plugin: Plugin) -> None:
    completer = Completer(lambda: plugin.settings.all())

    @plugin.settings.on_change
    def changed(values: dict) -> None:
        completer.forget()

    @plugin.route("/status")
    def status():
        """Whether suggestions can be made (Ollama running, model installed) and why not.
        ``fresh=1`` asks Ollama again; ``warm=1`` also loads the model into memory."""
        from flask import request

        return completer.status(fresh=request.args.get("fresh") == "1", warm=request.args.get("warm") == "1")

    @plugin.route("/complete", methods=["POST"])
    def complete():
        """A suggestion for the cursor between ``prefix`` and ``suffix``. Always answers: when
        there is no suggestion, ``reason`` says why, so the editor never shows an error per
        keystroke."""
        from flask import request

        data = request.get_json(silent=True) or {}
        prefix = str(data.get("prefix") or "")[-MAX_TEXT:]
        suffix = str(data.get("suffix") or "")[:MAX_TEXT]
        return completer.complete(prefix, suffix)
