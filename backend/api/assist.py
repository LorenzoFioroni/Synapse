"""Autocompletion in the notes editor, from a model served by Ollama on this computer."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import body, svc

bp = Blueprint("assist", __name__, url_prefix="/api/assist")

MAX_TEXT = 200_000  # characters of either side sent per request (only the end near the cursor is used)


@bp.get("/status")
def status():
    """Whether suggestions can be made (Ollama running, model installed) and why not.
    ``fresh=1`` asks Ollama again; ``warm=1`` also loads the model into memory."""
    return jsonify(svc().assist.status(fresh=request.args.get("fresh") == "1", warm=request.args.get("warm") == "1"))


@bp.post("/complete")
def complete():
    """A suggestion for the cursor between ``prefix`` and ``suffix``. Always answers 200: when
    there is no suggestion, ``reason`` says why, so the editor never shows an error per keystroke."""
    data = body()
    prefix = str(data.get("prefix") or "")[-MAX_TEXT:]
    suffix = str(data.get("suffix") or "")[:MAX_TEXT]
    return jsonify(svc().assist.complete(prefix, suffix))
