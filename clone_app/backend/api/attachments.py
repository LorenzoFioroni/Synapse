"""Attachments, PDF files, annotations and the PDF proxy."""
from __future__ import annotations

import os
import uuid

import requests
from flask import Blueprint, Response, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from .. import pdf_finder
from . import body, error, svc

bp = Blueprint("attachments", __name__)


def _save_upload(file) -> tuple[str, str]:
    safe = secure_filename(file.filename or "file.pdf") or "file.pdf"
    filename = f"{uuid.uuid4().hex}_{safe}"
    file.save(os.path.join(svc().config.pdf_dir, filename))
    return file.filename or safe, filename


@bp.post("/api/items/<item_id>/attachments")
def upload(item_id):
    s = svc()
    files = request.files.getlist("file")
    if not files:
        return error("No file uploaded")
    s.papers.get(item_id)  # 404 early
    created = []
    for f in files:
        name, filename = _save_upload(f)
        created.append(s.papers.add_attachment(item_id, name, filename, "pdf"))
    s.attachments_changed()
    return jsonify(attachments=created), 201


@bp.patch("/api/attachments/<int:att_id>")
def rename(att_id):
    name = (body().get("name") or "").strip()
    if not name:
        return error("Name cannot be empty")
    return jsonify(svc().papers.rename_attachment(att_id, name))


@bp.delete("/api/attachments/<int:att_id>")
def delete(att_id):
    s = svc()
    s.papers.delete_attachment(att_id)
    s.fulltext.remove([att_id])
    return jsonify(ok=True)


@bp.post("/api/items/<item_id>/find_pdf")
def find_pdf(item_id):
    s = svc()
    item = s.papers.get(item_id)
    if not item.get("title") and not item.get("doi"):
        return error("The item needs a title or DOI to search for a PDF.")
    result = pdf_finder.find_and_download(item.get("title"), item.get("doi"), s.config.pdf_dir,
                                          s.settings.get("contact_email"))
    if result["status"] == "success":
        att = s.papers.add_attachment(item_id, "Full Text PDF", result["filename"], "pdf")
        s.attachments_changed()
        return jsonify(status="success", attachment=att, source=result.get("source"))
    return jsonify(result)


# ------------------------------------------------------------------ files
@bp.get("/pdfs/<path:filename>")
def serve_pdf(filename):
    return send_from_directory(svc().config.pdf_dir, filename)


@bp.get("/api/proxy")
def proxy():
    url = request.args.get("url", "")
    if not url.startswith(("http://", "https://")):
        return error("Only http(s) URLs can be proxied")
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": pdf_finder.UA})
        return Response(r.content, status=r.status_code, mimetype="application/pdf")
    except requests.RequestException as e:
        return error(str(e), 502)


# ------------------------------------------------------------ annotations
@bp.get("/api/annotations")
def get_annotations():
    pid = request.args.get("paper_id")
    if not pid:
        return error("Missing paper_id")
    return jsonify(annotations=svc().papers.get_annotations(pid))


@bp.post("/api/annotations")
def save_annotations():
    data = body()
    if not data.get("paper_id"):
        return error("Missing paper_id")
    svc().papers.save_annotations(data["paper_id"], data.get("annotations"))
    return jsonify(status="success")
