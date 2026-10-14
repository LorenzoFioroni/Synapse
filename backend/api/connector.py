"""Endpoints for the browser extension ("Save to Library" button). What saving does is in
``backend/connector.py``; this module reads the requests and shapes the answers, which the
extension relies on (see docs/browser-extension.md)."""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, request
from werkzeug.datastructures import FileStorage

from .. import connector
from . import error, svc

bp = Blueprint("connector", __name__, url_prefix="/api/connector")

VERSION = "1.0"


@bp.after_request
def allow_extension_origins(resp):
    """The extension normally reaches 127.0.0.1/localhost through its host permissions;
    CORS for extension origins also lets it talk to a library running elsewhere."""
    origin = request.headers.get("Origin", "")
    if origin.startswith(("chrome-extension://", "moz-extension://")):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@bp.get("/ping")
def ping():
    """Lets the extension check the library is running and fill its folder/tag pickers."""
    s = svc()
    return jsonify(
        app="synapse",
        version=VERSION,
        folders=s.folders.tree(),
        tags=[t["name"] for t in s.tags.list()],
        counts=s.papers.counts(),
        # Settings → "When an item is added": the popup shows a checkbox for each "ask"
        on_add={
            "fetch_metadata": s.settings.get("add_fetch_metadata"),
            "find_pdf": s.settings.get("add_find_pdf"),
            "fetch_references": s.settings.get("add_fetch_references"),
        },
    )


def _payload() -> tuple[dict, FileStorage | None]:
    """Metadata from JSON or from a multipart 'meta' field (+ optional 'file')."""
    if request.files or request.form:
        try:
            meta = json.loads(request.form.get("meta") or "{}")
        except ValueError:
            meta = {}
        return meta, request.files.get("file")
    return request.get_json(silent=True) or {}, None


@bp.post("/file")
def file_existing():
    d = request.get_json(silent=True) or {}
    item = connector.file_existing(svc(), str(d.get("id") or ""), d.get("folder_id"), d.get("tags"))
    return jsonify(item=item)


@bp.post("/save")
def save():
    page, upload = _payload()
    pdf_bytes = upload.read() if upload is not None else None
    try:
        result = connector.save(svc(), page, pdf_bytes)
    except connector.NothingToSave as e:
        return error(str(e))
    except connector.Duplicate as e:
        existing = e.item
        return jsonify(
            duplicate_of=existing["id"], title=existing.get("title"), folder_ids=existing.get("folder_ids") or []
        ), 409
    return jsonify(**result), 201
