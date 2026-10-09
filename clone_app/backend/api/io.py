"""Import and export of references."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, Response, jsonify, request

from .. import formats
from ..fields import EDITABLE_KEYS
from ..formats.common import split_keywords
from ..repository import DuplicateFinder
from . import body, error, svc

bp = Blueprint("io", __name__, url_prefix="/api")


@bp.get("/formats")
def list_formats():
    return jsonify(formats=formats.listing())


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
        return error(str(e))
    finder = DuplicateFinder(s.papers.list())
    for e in entries:
        e["duplicate_of"] = finder.find(e)
    return jsonify(format=fmt, label=formats.FORMATS[fmt].label, entries=entries)


@bp.post("/import/commit")
def import_commit():
    s = svc()
    data = body()
    entries = data.get("entries") or []
    skip_dupes = data.get("skip_duplicates", s.settings.get("import_skip_duplicates"))
    finder = DuplicateFinder(s.papers.list() if skip_dupes else [])
    folder_id = data.get("folder_id")
    as_tags = s.settings.get("import_keywords_as_tags")
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
    # Imported keys are kept; missing ones follow the auto-generate setting.
    created = s.papers.create_many(keep)
    return jsonify(created=created, skipped=skipped)


@bp.route("/export", methods=["GET", "POST"])
def export():
    s = svc()
    args = request.values if request.method == "GET" else {**request.values, **body()}
    fmt = args.get("format") or s.settings.get("export_format")
    if fmt not in formats.FORMATS:
        return error(f"Unknown format: {fmt}")
    ids = args.get("ids")
    if isinstance(ids, str):
        ids = [i for i in ids.split(",") if i]
    items = s.papers.list()
    if ids:
        order = {i: n for n, i in enumerate(ids)}
        items = sorted((p for p in items if p["id"] in order), key=lambda p: order[p["id"]])
    else:
        items.sort(key=lambda p: ((p.get("authors") or "").lower(), str(p.get("year") or "")))
    for p in items:  # tags travel as keywords in every format
        kws = split_keywords(p.get("keywords")) + list(p.get("tags") or [])
        p["keywords"] = "; ".join(dict.fromkeys(kws))
    text = formats.dump(items, fmt, unicode=s.settings.get("bibtex_unicode"),
                        abstract=s.settings.get("export_abstract"), pattern=s.settings.get("citekey_pattern"))
    f = formats.FORMATS[fmt]
    if args.get("download", "1") in ("0", "false", False):
        return jsonify(text=text, format=fmt, count=len(items))
    name = f"library-{date.today().isoformat()}.{f.extension}" if not ids or len(ids) > 1 else \
        f"{(items[0].get('citation_key') or 'item') if items else 'empty'}.{f.extension}"
    return Response(text, mimetype=f.mimetype + "; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")
