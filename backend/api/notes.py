"""Markdown notes. Notes are addressed by their path inside the vault, e.g. ``papers/smith2021.md``."""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, g, jsonify, request, send_file, send_from_directory

from ..access.note_password import PasswordError
from ..access.passkeys import PasskeyError, relying_party
from ..access.proxy import client_addr
from ..changes import CLIENT_HEADER
from ..notes import service as notes_service
from ..notes.history import GitError
from ..notes.locks import NoteLocked
from ..notes.vault import ATTACHMENTS, Conflict, NoteExists, NoteMoved, VaultError
from ..pdf.files import HEADER_WINDOW, file_is_pdf, looks_like_pdf
from ..pdf.text import PageTextCache
from . import admin, body, error, failure, message_of, svc

bp = Blueprint("notes", __name__, url_prefix="/api")

MULTIPART_MARGIN = 64 * 1024  # the form's own fields and boundaries, on top of the file
EXCERPT = 400
_PAGES = PageTextCache(8)  # the text of the PDFs hovered lately


@bp.after_request
def _record_change(resp):
    """Tell the other windows and devices that notes changed (see backend/changes.py)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and resp.status_code < 400:
        # recording the history changes no note (its commits announce themselves), except a milestone restored
        if request.path.startswith("/api/notes/history/") and not request.path.endswith("/restore"):
            return resp
        if request.path.startswith(("/api/notes/unlock", "/api/notes/relock", "/api/notes/locks")):
            return resp  # opening or closing a note for one page, or the password: no note changed
        svc().changes.record({"notes"}, request.headers.get(CLIENT_HEADER))
    return resp


def guarded(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Conflict as e:
            return jsonify(error=f"{e.path} was changed elsewhere", conflict=True, current_hash=e.current_hash), 409
        except NoteExists as e:
            what = "A PDF" if e.path.lower().endswith(".pdf") else "A note"
            return jsonify(error=f"{what} already exists at {e.path}", exists=True, path=e.path), 409
        except NoteLocked as e:
            return jsonify(error=f"“{e.path}” is locked. Open it and unlock it first.", locked=True, path=e.path), 423
        except PasswordError as e:
            return failure(e, e.status)
        except PasskeyError as e:
            return failure(e, e.status)
        except NoteMoved as e:
            return jsonify(error=f"{e.path} was moved to {e.to}", moved_to=e.to), 404
        except VaultError as e:
            return error(message_of(e) if e.args else type(e).__name__, e.status)
        except ValueError as e:
            return failure(e)

    return wrapper


def _path_arg() -> str:
    return request.args.get("path") or body().get("path") or ""


@bp.get("/notes")
@guarded
def overview():
    return jsonify(svc().note_service.overview())


@bp.get("/notes/note")
@guarded
def get_note():
    return jsonify(svc().note_service.get(_path_arg(), unless=request.args.get("unless") or None))


@bp.post("/notes/note")
@guarded
def create_note():
    d = body()
    res = svc().note_service.create(
        path=d.get("path") or None,
        folder=d.get("folder") or "",
        title=(d.get("title") or "").strip(),
        item_id=d.get("item_id") or None,
        text=d.get("text"),
    )
    return jsonify(res), 201


@bp.put("/notes/note")
@guarded
def save_note():
    d = body()
    if not isinstance(d.get("text"), str):
        return error("text is required")
    if d.get("create"):  # write exactly this text to a path that must not exist (e.g. deleted elsewhere)
        return jsonify(svc().note_service.save(d.get("path") or "", d["text"], None))
    if not d.get("hash"):
        return error("hash is required")
    return jsonify(svc().note_service.save(d.get("path") or "", d["text"], d["hash"]))


@bp.post("/notes/offline")
@guarded
def apply_offline_edits():
    """A device brings in the edits it made while Synapse could not be reached (docs/offline.md)."""
    d = body()
    try:
        return jsonify(
            svc().note_service.apply_offline(
                d.get("path") or "", d.get("base_hash") or None, d.get("base_text"), d.get("versions") or []
            )
        )
    except ValueError as e:
        return failure(e)


@bp.post("/notes/move")
@guarded
def move_note():
    d = body()
    return jsonify(svc().note_service.move(d.get("path") or "", d.get("to") or "", d.get("hash") or None))


@bp.post("/notes/folder")
@guarded
def make_folder():
    return jsonify(svc().note_service.make_folder(body().get("path") or "")), 201


@bp.post("/notes/folder/move")
@guarded
def move_folder():
    d = body()
    return jsonify(svc().note_service.move_folder(d.get("path") or "", d.get("to") or ""))


@bp.put("/notes/folder/color")
@guarded
def folder_color():
    """``{path, color}``: a notes folder's colour (``#rrggbb``; null or "" for none)."""
    d = body()
    return jsonify(svc().note_service.set_folder_color(d.get("path") or "", d.get("color")))


@bp.post("/notes/trash")
@guarded
def trash_note():
    d = body()
    if "paths" in d or "folders" in d:  # several notes and/or folders, restored together by one undo
        return jsonify(svc().note_service.trash_many(list(d.get("paths") or []), list(d.get("folders") or [])))
    return jsonify(svc().note_service.trash(_path_arg()))


@bp.get("/notes/trash")
@guarded
def trashed_notes():
    """Deleted notes, with when they will be deleted for good (Settings → notes trash)."""
    return jsonify(
        trash=svc().attachments.with_deletion_dates(svc().vault.trashed()),
        retention=svc().settings.get("notes_trash_retention"),
    )


@bp.post("/notes/restore")
@guarded
def restore_note():
    d = body()
    if not d.get("path"):  # everything deleted together
        return jsonify(svc().note_service.restore_batch(d.get("trash_id") or ""))
    return jsonify(svc().note_service.restore(d.get("trash_id") or "", d.get("path") or ""))


# ---------------------------------------------------------------- locked notes (notes/locks.py)
def _passkey_site() -> tuple[str, str] | None:
    try:
        return relying_party(request.host, request.scheme)
    except PasskeyError:
        return None


@bp.get("/notes/locks")
@guarded
def lock_status():
    """Whether a password is set and whether this page can unlock with a passkey."""
    site = _passkey_site()
    return jsonify(
        password_set=svc().note_password.is_set(),
        passkeys=site is not None and svc().passkeys.count(site[0]) > 0,
        minutes=svc().settings.get("notes_lock_minutes"),
        **admin.state(),
    )


@bp.put("/notes/locks/password")
@guarded
def set_lock_password():
    """``{password, current?}``: set the password, or change it with the current one. Setting it
    without the current one is an administrative action (this computer, or after a passkey)."""
    d = body()
    pw = svc().note_password
    device = (g.get("device") or {}).get("id") or "this-computer"
    if not (pw.is_set() and pw.check(str(d.get("current") or ""), device)):
        if (
            refused := admin.refusal("Enter the current password, or change it on the computer running Synapse.")
        ) is not None:
            return refused
    pw.set(str(d.get("password") or ""))
    return jsonify(password_set=True)


@bp.post("/notes/lock")
@guarded
def lock_note():
    """``{path, locked}``: lock a note, or remove its lock (once this page unlocked it)."""
    d = body()
    locked = bool(d.get("locked"))
    if locked and not svc().note_password.is_set():
        return error("Set a password for locked notes first (Settings → Notes).", 409)
    return jsonify(svc().note_service.set_locked(_path_arg(), locked))


@bp.post("/notes/unlock/options")
@guarded
def unlock_options():
    site = _passkey_site()
    if not site:
        return error("Passkeys do not work at this address.", 409)
    return jsonify(svc().passkeys.login_options(*site, purpose="unlock"))


@bp.post("/notes/unlock")
@guarded
def unlock_note():
    """``{path, password}`` or ``{path, credential}`` (a passkey): open a locked note for this page."""
    d = body()
    path = _path_arg()
    if isinstance(d.get("credential"), dict):
        site = _passkey_site()
        if not site:
            return error("Passkeys do not work at this address.", 409)
        svc().passkeys.unlock(*site, d["credential"], client_addr(request.environ))
    else:
        device = (g.get("device") or {}).get("id") or "this-computer"
        if not svc().note_password.check(str(d.get("password") or ""), device):
            return error("Wrong password.", 403)
    return jsonify(svc().note_service.unlock(path))


@bp.post("/notes/unlock/touch")
@guarded
def touch_unlocked():
    """``{paths}``: this page's user is using these unlocked notes (clicks, keys, scrolling): they
    stay open for the whole timeout from now. Reading a note does not do that."""
    paths = body().get("paths")
    if not isinstance(paths, list) or len(paths) > 50:
        return error("paths must be a list of at most 50 notes")
    return jsonify(svc().note_service.touch([str(p) for p in paths]))


@bp.post("/notes/relock")
@guarded
def relock_notes():
    """``{paths?}``: lock again notes this page unlocked (its tab closed); none given: all of them."""
    paths = body().get("paths")
    return jsonify(svc().note_service.relock([str(p) for p in paths] if isinstance(paths, list) else None))


@bp.get("/notes/history")
@guarded
def note_history():
    """A note's versions, newest first (without their text)."""
    try:
        limit = int(request.args.get("limit") or 200)
    except ValueError:
        return error("limit must be a number")
    return jsonify(svc().note_service.history_of(_path_arg(), limit))


@bp.get("/notes/history/version")
@guarded
def note_history_version():
    """One version: the note's text after the change and before it."""
    a = request.args
    try:
        return jsonify(svc().note_service.history_version(a.get("id") or "", a.get("path"), a.get("from")))
    except KeyError:
        return error("No such version", 404)


@bp.get("/notes/history/status")
@guarded
def history_status():
    """Whether the notes history is on (Git found), for Settings."""
    return jsonify(svc().note_service.history_status())


@bp.get("/notes/history/log")
@guarded
def history_log():
    """Every recorded change to the notes, newest first, and the milestones."""
    a = request.args
    try:
        limit, skip = int(a.get("limit") or 100), int(a.get("skip") or 0)
    except ValueError:
        return error("limit and skip must be numbers")
    return jsonify(svc().note_service.history_log(limit, skip))


@bp.get("/notes/history/diff")
@guarded
def history_diff():
    """The notes that differ between two states: ``from``/``to`` are commit ids, ``<id>^`` (the
    state before a commit), milestone ids or ``now``."""
    a = request.args
    try:
        return jsonify(svc().note_service.history_diff(a.get("from") or "", a.get("to") or "now"))
    except GitError:
        return error("No such version", 404)


@bp.post("/notes/history/checkpoint")
@guarded
def history_checkpoint():
    """``{path?, restored_from?}``: record what is waiting now (a note was closed, or an earlier
    version was put back)."""
    d = body()
    return jsonify(svc().note_service.checkpoint(d.get("path") or None, d.get("restored_from") or None))


@bp.post("/notes/history/milestones")
@guarded
def add_milestone():
    return jsonify(svc().note_service.add_milestone(body().get("label") or "")), 201


@bp.put("/notes/history/milestones/<mid>")
@guarded
def rename_milestone(mid: str):
    try:
        return jsonify(svc().note_service.rename_milestone(mid, body().get("label") or ""))
    except GitError:
        return error("No such milestone", 404)


@bp.delete("/notes/history/milestones/<mid>")
@guarded
def delete_milestone(mid: str):
    try:
        return jsonify(svc().note_service.delete_milestone(mid))
    except GitError:
        return error("No such milestone", 404)


@bp.post("/notes/history/milestones/<mid>/restore")
@guarded
def restore_milestone(mid: str):
    """Put every note back as it was at the milestone (one change in the history)."""
    return jsonify(svc().note_service.restore_milestone(mid))


@bp.get("/notes/search")
@guarded
def search_notes():
    return jsonify(results=svc().note_service.search(request.args.get("q") or ""))


@bp.get("/notes/links")
@guarded
def note_links():
    return jsonify(svc().note_service.links(_path_arg()))


@bp.get("/notes/rename_plan")
@guarded
def rename_plan():
    """What changing a citation key would change in the notes (nothing is written)."""
    return jsonify(svc().note_service.rename_plan(request.args.get("old") or "", request.args.get("new") or ""))


@bp.get("/notes/keys")
@guarded
def key_summary():
    """Which citation keys have a paper note / are cited in notes (for the item list)."""
    return jsonify(keys=svc().note_service.key_summary())


@bp.get("/notes/cite_index")
@guarded
def cite_index():
    return jsonify(items=svc().note_service.cite_index())


@bp.get("/items/<item_id>/notes")
@guarded
def item_notes(item_id):
    return jsonify(svc().note_service.item_notes(item_id))


@bp.post("/notes/append")
@guarded
def append_to_paper_note():
    d = body()
    return jsonify(svc().note_service.append_to_paper_note(d.get("item_id") or "", d.get("markdown") or ""))


@bp.post("/notes/attachments")
@guarded
def upload_image():
    f = request.files.get("file")
    if not f:
        return error("No file uploaded")
    return jsonify(svc().note_service.add_image(f.filename or "", f.read())), 201


@bp.post("/notes/pdf")
@guarded
def upload_pdf():
    """A PDF (multipart ``file``, optional ``folder``) added to a notes folder."""
    # refused from the header, before the body is read into temporary files
    size = request.content_length
    cap = notes_service.MAX_PDF_BYTES
    if size is None and request.mimetype == "multipart/form-data":  # a body of unknown size: not read
        return error("The upload must say how large it is.", 411)
    if size is not None and size > cap + MULTIPART_MARGIN:
        return error(f"The PDF is larger than {cap // (1024 * 1024)} MB.", 413)
    f = request.files.get("file")
    if not f:
        return error("No file uploaded")
    head = f.stream.read(HEADER_WINDOW)
    f.stream.seek(0)
    if not looks_like_pdf(head):
        what = f"“{f.filename}”" if f.filename else "The file"
        return error(f"{what} is not a PDF. Only PDF files can be added to the notes.", 415)
    return jsonify(svc().note_service.add_pdf(f.filename or "", f.stream, request.form.get("folder") or "")), 201


@bp.get("/note-pdfs/<int:pdf_id>")
@guarded
def note_pdf(pdf_id):
    found = svc().note_pdfs.file_of(pdf_id)
    if not found:
        return error("This PDF is not in the notes any more.", 404)
    return jsonify(found[0])


@bp.get("/note-pdfs/<int:pdf_id>/page/<int:page>")
@guarded
def note_pdf_page(pdf_id, page):
    """The start of one page's text (for the card a link to the page shows)."""
    found = svc().note_pdfs.file_of(pdf_id)
    if not found or not file_is_pdf(found[1]):
        return error("This PDF is not in the notes any more.", 404)
    pages = _PAGES.pages(pdf_id, found[1])  # an unreadable PDF has none: the card shows no text
    text = " ".join(pages[page - 1].split()) if 1 <= page <= len(pages) else ""
    return jsonify(page=page, pages=len(pages), excerpt=text[:EXCERPT] + ("…" if len(text) > EXCERPT else ""))


@bp.get("/note-pdfs/<int:pdf_id>/file")
@guarded
def note_pdf_file(pdf_id):
    found = svc().note_pdfs.file_of(pdf_id)
    if not found:
        return error("This PDF is not in the notes any more.", 404)
    p = found[1]
    ok = file_is_pdf(p)  # served from the app's origin: what is not a PDF must never run in a browser
    resp = send_file(
        p,
        mimetype="application/pdf" if ok else "application/octet-stream",
        as_attachment=not ok,
        download_name=p.name,
        conditional=True,
        max_age=0,
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-cache"  # the file can be replaced under the same number
    return resp


@bp.get("/notes/attachments")
@guarded
def list_attachments():
    """Every attachment with the notes that show it, and the attachment trash."""
    return jsonify(svc().attachments.overview())


@bp.post("/notes/attachments/restore")
@guarded
def restore_attachment():
    d = body()
    try:
        return jsonify(path=svc().attachments.restore(d.get("trash_id") or "", d.get("name") or ""))
    except FileNotFoundError:
        return error("That attachment is no longer in the trash", 404)


@bp.post("/notes/attachments/delete")
@guarded
def delete_attachment():
    """Delete a trashed attachment for good."""
    d = body()
    try:
        svc().attachments.delete(d.get("trash_id") or "", d.get("name") or "")
    except FileNotFoundError:
        return error("That attachment is no longer in the trash", 404)
    return jsonify(ok=True)


@bp.post("/notes/attachments/clean")
@guarded
def clean_attachments():
    """Move unused attachments to their trash and empty old trash now (it also runs hourly)."""
    a = svc().attachments
    return jsonify(**a.sweep(), purged=a.purge())


@bp.get("/notes/attachments/trash/<trash_id>/<path:name>")
@guarded
def get_trashed_attachment(trash_id, name):
    try:
        p = svc().attachments.trashed_path(trash_id, name)
    except FileNotFoundError:
        return error("Not found", 404)
    resp = send_from_directory(p.parent, p.name)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.get("/notes/attachments/<path:name>")
@guarded
def get_attachment(name):
    v = svc().vault
    rel = v.clean_rel(f"{ATTACHMENTS}/{name}", suffix=None)
    resp = send_from_directory(v.root, rel)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"  # names are never reused
    return resp


@bp.post("/notes/rescan")
@guarded
def rescan():
    return jsonify(svc().note_service.rescan())
