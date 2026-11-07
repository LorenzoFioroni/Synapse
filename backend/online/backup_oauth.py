"""Browser authorization for Settings-managed rclone accounts on a remote server.

Only authorization and OneDrive's default drive discovery happen here; rclone owns storage and
refreshing tokens. Web application credentials use Synapse's own HTTPS callback, not localhost.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import requests

from .http import public_request

CALLBACK = "/api/backups/remote/callback"
OAUTH = {
    "drive": (
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://www.googleapis.com/auth/drive.file",
    ),
    "onedrive": (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "Files.ReadWrite offline_access User.Read",
    ),
    "dropbox": ("https://www.dropbox.com/oauth2/authorize", "https://api.dropboxapi.com/oauth2/token", ""),
}


class AuthorizationError(ValueError):
    pass


class BackupOAuth:
    def __init__(self):
        self._pending: dict = {}
        self._lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            self._pending = {}

    def begin(self, owner: str, callback: str, options: dict, revision: str) -> dict:
        parts = urlsplit(callback)
        if parts.path != CALLBACK or parts.query or parts.fragment or parts.username:
            raise AuthorizationError("Invalid backup authorization callback address.")
        if parts.scheme != "https" and not (
            parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")
        ):
            raise AuthorizationError("Connecting a cloud account needs Synapse's public HTTPS address.")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        auth, _, scope = OAUTH[options["type"]]
        params = {
            "client_id": options["client_id"],
            "redirect_uri": callback,
            "response_type": "code",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if scope:
            params["scope"] = scope
        if options["type"] == "drive":
            params.update(access_type="offline", prompt="consent")
        if options["type"] == "dropbox":
            params["token_access_type"] = "offline"
        with self._lock:
            self._pending = {
                "owner": owner,
                "state": state,
                "expires": time.time() + 600,
                "callback": callback,
                "verifier": verifier,
                "options": dict(options),
                "revision": revision,
            }
        return {"url": auth + "?" + urlencode(params)}

    def _json(self, method: str, url: str, **kwargs) -> dict:
        try:
            with public_request(method, url, timeout=30, stream=True, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    raise AuthorizationError(
                        "The provider refused authorization. Check the application credentials and try connecting again."
                    )
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 1024 * 1024:
                        raise AuthorizationError("The authorization response was too large.")
                import json

                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except (requests.RequestException, ValueError) as e:
            if isinstance(e, AuthorizationError):
                raise
            raise AuthorizationError(
                "Could not complete cloud authorization. Check the connection and try again."
            ) from e

    def finish(self, owner: str, args: dict, revision: str | None) -> tuple[dict, dict]:
        with self._lock:
            pending = self._pending
            if (
                not pending
                or pending["owner"] != owner
                or pending["revision"] != revision
                or pending["expires"] < time.time()
                or not secrets.compare_digest(str(args.get("state", "")), pending["state"])
            ):
                raise AuthorizationError(
                    "This account connection expired or belongs to another session. Connect again from Settings → Backups."
                )
            self._pending = {}
        if args.get("error") or not args.get("code"):
            raise AuthorizationError(
                "Account authorization was cancelled or refused. You can connect again from Settings → Backups."
            )
        options = pending["options"]
        token = self._json(
            "POST",
            OAUTH[options["type"]][1],
            data={
                "client_id": options["client_id"],
                "client_secret": options["client_secret"],
                "grant_type": "authorization_code",
                "code": args["code"],
                "redirect_uri": pending["callback"],
                "code_verifier": pending["verifier"],
            },
        )
        if not isinstance(token.get("access_token"), str) or not isinstance(token.get("refresh_token"), str):
            raise AuthorizationError(
                "The provider did not grant ongoing backup access. Reconnect and allow offline access."
            )
        try:
            expiry = datetime.now(timezone.utc) + timedelta(
                seconds=max(1, min(int(token.get("expires_in", 3600)), 31536000))
            )
        except (ValueError, TypeError) as e:
            raise AuthorizationError("The provider returned an invalid token expiry.") from e
        saved = {key: token[key] for key in ("access_token", "refresh_token", "token_type") if key in token}
        saved["expiry"] = expiry.isoformat()
        extra = {}
        if options["type"] == "onedrive" and not options.get("drive_id"):
            drive = self._json(
                "GET",
                "https://graph.microsoft.com/v1.0/me/drive",
                headers={"Authorization": "Bearer " + token["access_token"]},
            )
            if not drive.get("id") or drive.get("driveType") not in ("personal", "business", "documentLibrary"):
                raise AuthorizationError(
                    "The default OneDrive could not be found. Enter its drive ID and type in Settings."
                )
            extra = {"drive_id": drive["id"], "drive_type": drive["driveType"]}
        return saved, extra
