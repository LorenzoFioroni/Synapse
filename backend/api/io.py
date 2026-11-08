"""Import and export of references."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, Response, jsonify, request

from .. import formats
from ..formats.common import split_keywords
from ..formats.exporting import options, prepare
from ..library.fields import EDITABLE_KEYS
from ..library.repository import DuplicateFinder
from ..plugins import PluginError
from . import body, error, failure, svc

bp = Blueprint("io", __name__, url_prefix="/api")


@bp.get("/formats")
def list_formats():
    return jsonify(formats=formats.listing(), exports=svc().plugins.export_formats())


@bp.post("/import/preview")
def import_preview():
    """Parse uploaded/pasted text; flag duplicates. Nothing is written yet."""
    s = svc()
    if "file" in request.files:
        f = request.files["file"]
        raw = f.read()
        filename = f.filename
        fmt = request.form.get("format") or None
    else:
        data = body()
        raw = (data.get("text") or "").encode("utf-8")
        filename = data.get("filename")
        fmt = data.get("format") or None
    text = _decode(raw)
    if not text.strip():
        return error("The file is empty.")
    try:
        fmt, entries = formats.parse(text, fmt, filename)
    except formats.FormatError as e:
        return failure(e)
    finder = DuplicateFinder(s.papers.list())
    for entry in entries:
        entry["duplicate_of"] = finder.find(entry)
    return jsonify(format=fmt, label=formats.FORMATS[fmt].label, entries=entries)


@bp.post("/import/commit")
def import_commit():
    s = svc()
    data = body()
    entries = data.get("entries") or []
    if entries:
        s.backups.before("an import")
    skip_dupes = data.get("skip_duplicates", s.settings.get("import_skip_duplicates"))
    finder = DuplicateFinder(s.papers.list() if skip_dupes else [])
    folder_id = data.get("folder_id")
    # Settings → Import: keywords become tags never / always / when the user ticked the box
    as_tags = s.settings.wants("import_tags", data.get("keywords_as_tags"))
    keep, skipped = [], 0
    for e in entries:
        if skip_dupes and finder.find(e):
            skipped += 1
            continue
        clean = {k: v for k, v in e.items() if k in EDITABLE_KEYS}
        tags = list(e.get("tags") or [])
        if as_tags and clean.get("keywords"):
            tags += split_keywords(clean.pop("keywords"))
        clean["tags"] = tags
        clean["folder_id"] = folder_id
        keep.append(clean)
        finder.add(clean, "pending")  # also catch duplicates within the same file
    # Imported keys are kept when usable; missing ones follow the auto-generate setting.
    created, renamed = s.papers.import_many(keep)
    return jsonify(created=created, skipped=skipped, renamed_keys=renamed)


@bp.route("/export", methods=["GET", "POST"])
def export():
    s = svc()
    args = request.values if request.method == "GET" else {**request.values, **body()}
    fmt = args.get("format") or s.settings.get("export_format")
    ids = args.get("ids")
    if isinstance(ids, str):
        ids = [i for i in ids.split(",") if i]
    items = prepare(s.papers.list(), ids or None)
    try:
        f, text = s.plugins.export_items(fmt, items, options(s.settings))
    except PluginError as e:
        return failure(e)
    if args.get("download", "1") in ("0", "false", False):
        return jsonify(text=text, format=fmt, count=len(items))
    name = (
        f"library-{date.today().isoformat()}.{f.extension}"
        if not ids or len(ids) > 1
        else f"{(items[0].get('citation_key') or 'item') if items else 'empty'}.{f.extension}"
    )
    return Response(
        text, mimetype=f.mimetype + "; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


@bp.post("/clipboard/bibtex")
def clipboard_bibtex():
    s = svc()
    ids = body().get("ids")
    if not isinstance(ids, list):
        return error("ids must be a list")
    items = prepare(s.papers.list(), [str(item_id) for item_id in ids])
    return jsonify(text=formats.dump(items, "bibtex", **options(s.settings)), count=len(items), format="bibtex")
