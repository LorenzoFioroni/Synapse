"""PDF annotations, one record each (see backend/annotations.py)."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..library.annotations import AnnotationError
from . import body, error, failure, svc

bp = Blueprint("annotations", __name__, url_prefix="/api")


@bp.get("/attachments/<int:att_id>/annotations")
def list_for_attachment(att_id):
    svc().papers.get_attachment(att_id)  # 404 for an unknown attachment
    return jsonify(annotations=svc().annotations.for_attachment(att_id))


@bp.post("/attachments/<int:att_id>/annotations")
def create(att_id):
    svc().papers.get_attachment(att_id)
    try:
        ann = svc().annotations.create(att_id, body())
    except AnnotationError as e:
        return failure(e)
    return jsonify(annotation=ann), 201


@bp.get("/note-pdfs/<int:pdf_id>/annotations")
def list_for_note_pdf(pdf_id):
    if not svc().note_pdfs.get(pdf_id):
        return error("This PDF is not in the notes any more.", 404)
    return jsonify(annotations=svc().annotations.for_note_pdf(pdf_id))


@bp.post("/note-pdfs/<int:pdf_id>/annotations")
def create_on_note_pdf(pdf_id):
    if not svc().note_pdfs.file_of(pdf_id):  # only a PDF that is there can be marked
        return error("This PDF is not in the notes any more.", 404)
    try:
        ann = svc().annotations.create_on_note_pdf(pdf_id, body())
    except AnnotationError as e:
        return failure(e)
    return jsonify(annotation=ann), 201


@bp.patch("/annotations/<ann_id>")
def update(ann_id):
    try:
        ann = svc().annotations.update(ann_id, body())
    except AnnotationError as e:
        return failure(e)
    except KeyError:
        return error("This annotation no longer exists.", 404)
    return jsonify(annotation=ann)


@bp.delete("/annotations/<ann_id>")
def delete(ann_id):
    if not svc().annotations.delete(ann_id):
        return error("This annotation no longer exists.", 404)
    return jsonify(ok=True)


@bp.get("/items/<item_id>/annotations")
def list_for_item(item_id):
    svc().papers.get(item_id)
    return jsonify(annotations=svc().annotations.for_item(item_id))


@bp.get("/annotations/migration")
def migration_report():
    """What was converted from the old format, and what could not be."""
    return jsonify(items=svc().annotations.migration_report())
