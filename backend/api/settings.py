"""User settings."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..library import citekeys
from ..settings import DEFAULTS, SEARCH_MODES, SettingsError
from . import body, error, svc

bp = Blueprint("settings", __name__, url_prefix="/api/settings")

SAMPLE = {
    "authors": "Cui, Jian; Cirac, J. Ignacio; Bañuls, Mari Carmen",
    "title": "Variational Matrix Product Operators for the Steady State of Dissipative Quantum Systems",
    "year": 2015,
    "journal": "Physical Review Letters",
    "doi": "10.1103/PhysRevLett.114.220601",
}


@bp.get("")
def get_settings():
    return jsonify(
        settings=svc().settings.all(), defaults=DEFAULTS, searchModes=SEARCH_MODES, citekeyPresets=citekeys.PRESETS
    )


@bp.put("")
def put_settings():
    try:
        return jsonify(settings=svc().settings.update(body()))
    except SettingsError as e:
        return error(str(e))


@bp.post("/reset")
def reset_settings():
    return jsonify(settings=svc().settings.reset())


@bp.get("/citekey_preview")
def citekey_preview():
    pattern = request.args.get("pattern") or svc().settings.get("citekey_pattern")
    examples = [citekeys.render(pattern, SAMPLE)]
    # also show the first few real items so the user sees their own data
    for p in svc().papers.list()[:3]:
        examples.append(citekeys.render(pattern, p))
    return jsonify(pattern=pattern, examples=examples)
