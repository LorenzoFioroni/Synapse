"""Endpoints of the companion (``/api/companion/``, for the program on the user's computer, bearer
token only) and of "Ask AI" in the app (``/api/ai/``). The rendezvous is in backend/companion/."""

from __future__ import annotations

from flask import Blueprint, Response, g, jsonify, request

from ..companion import bundle, prompt
from ..companion.hub import (
    CompanionLimit,
    InvalidCompanion,
    InvalidEvents,
    NoCompanion,
    TooBusy,
    UnknownJob,
    WrongCompanion,
)
from . import body, error, failure, svc

bp = Blueprint("companion", __name__)

VERSION = "1.0"


MAX_BODY = 512 * 1024  # the app-wide limit is far larger; none of these requests needs more


@bp.before_request
def _small_bodies():
    request.max_content_length = MAX_BODY  # checked when the body is read: a larger one answers 413


def _token_id() -> str:
    return (g.get("access_token") or {}).get("id", "")


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/api/companion/hello")
def hello():
    return _no_store(
        jsonify(
            app="synapse",
            version=VERSION,
            extension={"build": bundle.extension()[0]},
            script={"build": bundle.script()[0]},
        )
    )


@bp.post("/api/companion/poll")
def poll():
    data = body()
    try:
        reply = svc().companions.poll(
            data.get("client"), data.get("name"), data.get("providers", []), data.get("running", []), token=_token_id()
        )
    except WrongCompanion as e:
        return failure(e, 409)
    except (InvalidCompanion, CompanionLimit) as e:
        return failure(e)
    return _no_store(jsonify(**reply))


@bp.post("/api/companion/jobs/<job_id>/events")
def job_events(job_id):
    data = body()
    try:
        cancelled = svc().companions.post_events(data.get("client"), job_id, data.get("events", []), token=_token_id())
    except UnknownJob:
        return error("No such job.", 404)
    except WrongCompanion:
        return error("This job belongs to another companion.", 409)
    except InvalidEvents as e:
        return failure(e)
    return _no_store(jsonify(cancelled=cancelled))


@bp.get("/api/companion/extension")
def extension():
    build, data = bundle.extension()
    if not data:
        return error("This server has no browser extension to download.", 404)
    return _no_store(Response(data, mimetype="application/zip", headers={"X-Build": build}))


@bp.get("/api/companion/script")
def script():
    build, data = bundle.script()
    if not data:
        return error("This server has no companion script to download.", 404)
    return _no_store(Response(data, mimetype="application/zip", headers={"X-Build": build}))


# ---------------------------------------------------------------------- the app


@bp.get("/api/ai/companion-script")
def download_script():
    """The companion program (``synapse_companion.pyz``), for Settings' Download button (device auth)."""
    _, data = bundle.script()
    if not data:
        return error("This server has no companion script to download.", 404)
    return _no_store(
        Response(
            data,
            mimetype="application/zip",
            headers={"Content-Disposition": 'attachment; filename="synapse_companion.pyz"'},
        )
    )


@bp.get("/api/ai/status")
def ai_status():
    s = svc()
    return _no_store(
        jsonify(configured=any(t["scope"] == "companion" for t in s.tokens.list()), companions=s.companions.status())
    )


def _text_arg(value) -> str | None:
    return value if isinstance(value, str) and value else None


@bp.post("/api/ai/ask")
def ask():
    s = svc()
    data = body()
    conversation = _text_arg(data.get("conversation"))
    try:
        text, system = prompt.build(data.get("prompt"), data.get("context"), notes=s.note_service, papers=s.papers)
        pin = None
        if conversation:
            row = s.ai_conversations.get(conversation)
            if row is None:
                raise NoCompanion("This conversation is gone. Start a new conversation.")
            pin = (row["companion"]["id"], row["provider"], row["companion"]["name"])
        question = data["prompt"].strip()

        def record(reply):
            s.ai_conversations.record_ask(
                reply,
                question,
                context=data.get("context"),
                model=_text_arg(data.get("model")),
                effort=_text_arg(data.get("effort")),
                job=reply["job"],
            )

        reply = s.companions.ask(
            text,
            system,
            provider=_text_arg(data.get("provider")),
            conversation=conversation,
            companion=_text_arg(data.get("companion")),
            model=_text_arg(data.get("model")),
            effort=_text_arg(data.get("effort")),
            pin=pin,
            record=record,
        )
    except prompt.UnknownContext as e:
        return failure(e, 404)
    except (NoCompanion, TooBusy) as e:
        return failure(e, 409)
    except ValueError as e:
        return failure(e)
    return jsonify(**reply), 201


def _with_state(row: dict) -> dict:
    """A stored conversation with whether it can be continued now and which job is still answering."""
    hub = svc().companions
    live = {c["id"]: c for c in hub.status()}
    comp = live.get(row["companion"]["id"])
    row["available"] = bool(comp and any(p["id"] == row["provider"] for p in comp["providers"]))
    row["running"] = hub.running_job(row["id"])
    return row


@bp.get("/api/ai/conversations")
def conversations():
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        return error("Invalid limit.")
    rows = svc().ai_conversations.list(limit, request.args.get("q", ""))
    return _no_store(jsonify(conversations=[_with_state(r) for r in rows]))


@bp.get("/api/ai/conversations/<conv_id>")
def conversation(conv_id):
    row = svc().ai_conversations.get(conv_id)
    if row is None:
        return error("No such conversation.", 404)
    return _no_store(jsonify(**_with_state(row)))


@bp.patch("/api/ai/conversations/<conv_id>")
def rename_conversation(conv_id):
    title = body().get("title")
    try:
        done = isinstance(title, str) and svc().ai_conversations.rename(conv_id, title)
    except ValueError as e:
        return failure(e)
    if not done:
        return (
            error("No such conversation.", 404) if isinstance(title, str) else error("Give the conversation a title.")
        )
    return jsonify(**_with_state(svc().ai_conversations.get(conv_id) or {}))


@bp.delete("/api/ai/conversations/<conv_id>")
def delete_conversation(conv_id):
    if not svc().ai_conversations.delete(conv_id):
        return error("No such conversation.", 404)
    return jsonify(ok=True)


@bp.get("/api/ai/jobs/<job_id>")
def watch(job_id):
    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        return error("Invalid position.")
    try:
        return _no_store(jsonify(**svc().companions.watch(job_id, after)))
    except UnknownJob:
        return error("No such job.", 404)


@bp.post("/api/ai/jobs/<job_id>/cancel")
def cancel(job_id):
    try:
        return jsonify(state=svc().companions.cancel(job_id))
    except UnknownJob:
        return error("No such job.", 404)
