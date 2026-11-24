"""Changes a device queued while Synapse could not be reached, sent again once it answers
(plugin API 1.16, docs/plugins.md#offline-use). A plugin whose manifest says ``"offline":
{"queue": true}`` may have its frontend queue writes; Synapse sends them in order with these
headers, and the plugin's route decides how they merge with what changed meanwhile:

* ``replayed_at()`` — when the change was made on the device (seconds since the epoch), or None
  for an ordinary request;
* ``forced()`` — the user chose to keep their offline change after a conflict;
* ``conflict(message, retry=…)`` — the answer for a change that cannot be merged: the app asks the
  user, and **Keep mine** sends ``retry`` (the body to apply anyway) again with ``forced()`` true.
"""

from __future__ import annotations

import time

from flask import jsonify, request

OFFLINE_HEADER = "X-Synapse-Offline"  # when the change was made, ms since the epoch
FORCE_HEADER = "X-Synapse-Offline-Force"


def replayed_at() -> float | None:
    raw = request.headers.get(OFFLINE_HEADER, "")
    try:
        at = float(raw) / 1000
    except ValueError:
        return None
    return min(max(at, 0.0), time.time())  # a device clock ahead is not believed


def forced() -> bool:
    return request.headers.get(FORCE_HEADER) == "1"


def conflict(message: str, *, retry: dict | None = None, **extra):
    """A 409 the app shows to the user (``message``, one sentence); ``retry``: what Keep mine sends."""
    return jsonify(error=message, conflict={"message": message, "retry": retry, **extra}), 409
