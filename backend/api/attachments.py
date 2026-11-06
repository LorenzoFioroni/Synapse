"""Attachments, PDF files and PDFs of attachments stored as web links. Annotations: api/pdf_annotations.py."""

from __future__ import annotations

import os
import uuid

from flask import Blueprint, Response, abort, jsonify, request, send_from_directory
from werkzeug.security import safe_join
from werkzeug.utils import secure_filename

from ..online import remote_pdf
from ..online.http import BROWSER_UA
from ..pdf.files import HEADER_WINDOW, file_is_pdf, looks_like_pdf
from ..pdf.text import clean_text, extract_text, find_identifier
from . import body, error, svc

bp = Blueprint("attachments", __name__)


def _not_pdfs(files) -> str | None:
    """Why these uploads are refused, or None. Checked before any is saved, so a refused batch adds
    nothing."""
    for f in files:
        head = f.stream.read(HEADER_WINDOW)
        f.stream.seek(0)
        if not looks_like_pdf(head):
            return f"“{f.filename or 'The file'}” is not a PDF. Only PDF files can be attached."
    return None


def _save_upload(file) -> tuple[str, str]:
    safe = secure_filename(file.filename or "file.pdf") or "file.pdf"
    stem = os.path.splitext(safe)[0] or "file"
    filename = f"{uuid.uuid4().hex}_{stem}.pdf"  # the stored name never carries another type
    file.save(os.path.join(svc().config.pdf_dir, filename))
    return file.filename or safe, filename


@bp.post("/api/items/<item_id>/attachments")
def upload(item_id):
    s = svc()
    files = request.files.getlist("file")
    if not files:
        return error("No file uploaded")
    s.papers.get(item_id)  # 404 early
    if refused := _not_pdfs(files):
        return error(refused, 415)
    created = []
    for f in files:
        name, filename = _save_upload(f)
        created.append(s.papers.add_attachment(item_id, name, filename, "pdf"))
    s.attachments_changed()
    return jsonify(attachments=created), 201


@bp.post("/api/items/from_pdf")
def items_from_pdfs():
    """Create one item per dropped PDF, pre-filled from a DOI/arXiv id found in the text."""
    s = svc()
    files = request.files.getlist("file")
    if not files:
        return error("No file uploaded")
    if refused := _not_pdfs(files):
        return error(refused, 415)
    from ..online.http import LookupFailed
    from ..online.metadata import known_authors, lookup

    known = known_authors(s.papers.list())
    created = []
    for f in files:
        name, filename = _save_upload(f)
        path = s.config.pdf_dir / filename
        text, _ = extract_text(path, max_pages=2)
        ident = find_identifier(clean_text(text))
        meta, note = {}, None
        try:
            if ident.get("doi"):
                meta = lookup(
                    sources=s.plugins.lookup_metadata,
                    doi=ident["doi"],
                    known=known,
                    email=s.settings.get("contact_email"),
                )
            elif ident.get("arxiv"):
                meta = lookup(sources=s.plugins.lookup_metadata, identifier="arXiv:" + ident["arxiv"], known=known)
        except LookupFailed as e:
            note = str(e)
        if not meta:
            meta = {"title": os.path.splitext(name)[0].replace("_", " ")}
            if ident.get("doi"):
                meta["doi"] = ident["doi"]
        item = s.papers.create({**meta, "folder_id": request.form.get("folder_id") or None})
        if note is None and len(meta) > 2:  # metadata came from a lookup
            s.citations.maybe_enqueue(item, request.form.get("fetch_references") in ("1", "true"))
        s.papers.add_attachment(item["id"], name, filename, "pdf")
        created.append({"item": s.papers.get(item["id"]), "identifier": ident, "note": note})
    s.attachments_changed()
    return jsonify(created=created), 201


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
    result = svc().find_pdf(item_id)  # a title, DOI or arXiv id is enough
    if result["status"] == "error":
        return error(result["message"])
    if result["status"] == "success":
        return jsonify(status="success", attachment=result["attachment"], source=result.get("source"))
    return jsonify(result)


# ------------------------------------------------------------------ files
@bp.get("/pdfs/<path:filename>")
def serve_pdf(filename):
    # Only PDFs are shown in the browser. Anything else in the folder (a file kept from before
    # uploads were checked) is only offered as a download, so it can never run as a page or script.
    folder = svc().config.pdf_dir
    path = safe_join(str(folder), filename)
    if path is None or not os.path.isfile(path):
        abort(404)
    if file_is_pdf(path):
        resp = send_from_directory(folder, filename, mimetype="application/pdf")
    else:
        resp = send_from_directory(folder, filename, mimetype="application/octet-stream", as_attachment=True)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.get("/api/attachments/<int:att_id>/remote")
def remote_attachment(att_id):
    """The PDF of an attachment stored as a web link. Only the attachment's own URL is fetched,
    never one sent by the browser, and never from the local network."""
    att = svc().papers.get_attachment(att_id)
    if not att or not str(att.get("url") or "").startswith(("http://", "https://")):
        return error("This attachment is not a web link.", 404)
    try:
        data = remote_pdf.fetch_pdf(att["url"], BROWSER_UA)
    except remote_pdf.RemoteError as e:
        return error(str(e), e.status)
    return Response(data, mimetype="application/pdf")
