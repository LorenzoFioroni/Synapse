"""Client for the Zotero *local* API (http://127.0.0.1:23119/api).

Reading works with Zotero 7+ (enable "Allow other applications on this
computer to communicate with Zotero" in Settings → Advanced).  Writing requires
Zotero 10+, where the local API accepts the Web API write requests after the
user approved this application in a Zotero dialog.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

log = logging.getLogger(__name__)
UA = "ZoteroLibraryCleaner/2.0 (local research utility)"


class ZoteroError(RuntimeError):
    def __init__(self, msg, status=None, body=""):
        super().__init__(msg)
        self.status = status
        self.body = body


class AuthError(ZoteroError):
    pass


class ConflictError(ZoteroError):
    pass


class Zotero:
    def __init__(self, settings, secrets):
        self.settings = settings
        self.secrets = secrets
        self.s = requests.Session()
        self.s.trust_env = False  # never send localhost traffic through a proxy
        self.s.headers.update({"Zotero-API-Version": "3", "User-Agent": UA})

    # ------------------------------------------------------------------ basics
    @property
    def base(self) -> str:
        return self.settings.get("zotero_api").rstrip("/")

    @property
    def lib(self) -> str:
        return self.settings.get("library").strip("/")

    @property
    def server_id(self):
        return self.secrets.get("server_id")

    def _key_entry(self) -> dict | None:
        sid = self.server_id
        e = (self.secrets.get("keys") or {}).get(sid) if sid else None
        if isinstance(e, str):  # stored by an older version: reusability unknown
            return {"key": e, "remember": False}
        return e

    @property
    def write_key(self):
        e = self._key_entry()
        return e["key"] if e else None

    @property
    def key_reusable(self) -> bool:
        """True if the user chose 'Always Allow' (the key can be used for many writes)."""
        e = self._key_entry()
        return bool(e and e.get("remember"))

    def ensure_reusable_key(self):
        """A commit needs many writes. Zotero's plain 'Allow' gives a key that is
        consumed by the first write, so insist on 'Always Allow'."""
        if self.key_reusable:
            return
        res = self.authorize()
        if not res.get("remember"):
            self.forget_key()
            raise AuthError("You clicked 'Allow' in Zotero, which authorises a single write only. Applying changes "
                            "needs many writes: click 'Authorise writes' again and choose 'Always Allow' in the Zotero "
                            "dialog. (You can revoke it later in Zotero's settings.)", 401)

    def _url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def _check_server(self, r: requests.Response):
        sid = r.headers.get("Zotero-Server-ID")
        if sid and self.server_id and sid != self.server_id:
            raise ZoteroError(
                "Connected to a different Zotero database than the one this workspace was built from "
                f"(expected server ID {self.server_id}, got {sid}). Use 'Reset connection' in Settings "
                "if this is intended, then rescan.", 412)

    def request(self, method: str, path: str, *, write: bool = False, params=None, json=None,
                data=None, headers=None, timeout=60, ok=(200, 201, 204, 302)) -> requests.Response:
        for attempt in (1, 2):
            h = dict(headers or {})
            if self.server_id:
                h["Zotero-Server-ID"] = self.server_id
            if write:
                if not self.server_id:
                    self.ping()
                    h["Zotero-Server-ID"] = self.server_id
                if not self.write_key:
                    raise AuthError("Write access has not been authorised yet. Click 'Authorise writes' and choose "
                                    "'Always Allow' in the Zotero dialog.", 401)
                h["Zotero-API-Key"] = self.write_key
            try:
                r = self.s.request(method, self._url(path), params=params, json=json, data=data, headers=h,
                                   timeout=timeout, allow_redirects=False)
            except requests.ConnectionError as e:
                raise ZoteroError("Cannot reach Zotero at %s. Is Zotero running, and is 'Allow other applications "
                                  "on this computer to communicate with Zotero' enabled (Settings → Advanced)?" % self.base) from e
            self._check_server(r)
            if write and r.status_code == 401 and attempt == 1:
                # expired/consumed key: Zotero says clients should simply authorise again
                self.forget_key()
                self.authorize()
                continue
            break
        if write and r.status_code in ok and not self.key_reusable:
            self.forget_key()  # single-use key ('Allow') has been consumed by this write
        if r.status_code in ok:
            return r
        body = r.text[:1000]
        if r.status_code in (401, 403) and write:
            raise AuthError(f"Zotero refused the write ({r.status_code}): {body}. Click 'Authorise writes' and choose "
                            "'Always Allow' in the Zotero dialog.", r.status_code, body)
        if r.status_code == 403 and "local API" in body:
            raise ZoteroError("The Zotero local API is disabled. Enable it in Zotero → Settings → Advanced → "
                              "'Allow other applications on this computer to communicate with Zotero'.", 403, body)
        if r.status_code == 412:
            raise ConflictError(f"Version conflict: the item changed in Zotero. {body}", 412, body)
        if r.status_code == 428:
            raise ZoteroError("Zotero requires the server ID header (428). Reconnect.", 428, body)
        raise ZoteroError(f"Zotero API {method} {path} → {r.status_code}: {body}", r.status_code, body)

    # ------------------------------------------------------------------ connection
    def ping(self) -> dict:
        r = self.request("GET", "/")
        sid = r.headers.get("Zotero-Server-ID")
        if sid and not self.server_id:
            self.secrets.set("server_id", sid)
        return {"server_id": sid or self.server_id, "api_version": r.headers.get("Zotero-API-Version"),
                "schema_version": r.headers.get("Zotero-Schema-Version"),
                "write_authorised": self.key_reusable}

    def reset_connection(self):
        self.secrets.set("server_id", None)

    def authorize(self, app_name="Zotero Library Cleaner") -> dict:
        if not self.server_id:
            self.ping()
        h = {"Zotero-Server-ID": self.server_id, "Content-Type": "application/json"}
        try:
            r = self.s.post(self._url("local/authorize"), json={"appName": app_name}, headers=h, timeout=300)
        except requests.ConnectionError as e:
            raise ZoteroError("Cannot reach Zotero.") from e
        self._check_server(r)
        if r.status_code == 404:
            raise AuthError("This Zotero version does not support writes through the local API. "
                            "Zotero 10 or later is required to apply changes.", 404)
        if r.status_code == 403:
            raise AuthError("Authorisation was denied in Zotero.", 403)
        if r.status_code == 429:
            raise AuthError("Too many authorisation requests; wait a minute and try again.", 429)
        if r.status_code >= 400:
            raise AuthError(f"Authorisation failed ({r.status_code}): {r.text[:500]}", r.status_code)
        j = r.json()
        keys = dict(self.secrets.get("keys") or {})
        keys[self.server_id] = {"key": j["key"], "remember": bool(j.get("remember", False))}
        self.secrets.set("keys", keys)
        return {"authorised": True, "remember": bool(j.get("remember", False))}

    def forget_key(self):
        keys = dict(self.secrets.get("keys") or {})
        keys.pop(self.server_id, None)
        self.secrets.set("keys", keys)

    # ------------------------------------------------------------------ reading
    def fetch_items(self, since: int | None = None, page: int = 500,
                    progress: Callable[[int, int | None], None] | None = None,
                    cancelled: Callable[[], bool] | None = None) -> tuple[list[dict], int | None]:
        """All items (incl. trashed, notes, attachments) changed since *since*."""
        out: list[dict] = []
        start, total, lib_version = 0, None, None
        while True:
            params = {"format": "json", "limit": page, "start": start, "includeTrashed": 1}
            if since is not None:
                params["since"] = since
            r = self.request("GET", f"{self.lib}/items", params=params, timeout=300)
            lib_version = lib_version or r.headers.get("Last-Modified-Version")
            if total is None and r.headers.get("Total-Results"):
                total = int(r.headers["Total-Results"])
            chunk = r.json()
            out.extend(chunk)
            if progress:
                progress(len(out), total)
            if cancelled and cancelled():
                raise ZoteroError("cancelled")
            if len(chunk) < page:
                break
            start += page
        return out, int(lib_version) if lib_version else None

    def deleted_since(self, since: int) -> list[str]:
        r = self.request("GET", f"{self.lib}/deleted", params={"since": since})
        return r.json().get("items", [])

    def get_item(self, key: str) -> dict:
        return self.request("GET", f"{self.lib}/items/{key}", params={"format": "json"}).json()

    def get_items(self, keys: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(keys), 50):
            chunk = keys[i:i + 50]
            r = self.request("GET", f"{self.lib}/items", params={"format": "json", "itemKey": ",".join(chunk), "includeTrashed": 1})
            out.extend(r.json())
        return out

    def children(self, key: str) -> list[dict]:
        return self.request("GET", f"{self.lib}/items/{key}/children", params={"format": "json"}).json()

    def file_path(self, key: str) -> Path | None:
        """Local path of an attachment file (None if Zotero doesn't know it)."""
        try:
            r = self.request("GET", f"{self.lib}/items/{key}/file/view/url", ok=(200,))
        except ZoteroError as e:
            if e.status == 404:
                return None
            raise
        u = r.text.strip()
        if u.startswith("file://"):
            return Path(unquote(urlparse(u).path))
        return None

    # ------------------------------------------------------------------ writing
    def patch_item(self, key: str, version: int, patch: dict) -> int:
        r = self.request("PATCH", f"{self.lib}/items/{key}", write=True, json=patch,
                         headers={"If-Unmodified-Since-Version": str(version), "Content-Type": "application/json"})
        return int(r.headers.get("Last-Modified-Version") or 0)

    def put_item(self, key: str, data: dict) -> int:
        r = self.request("PUT", f"{self.lib}/items/{key}", write=True, json=data,
                         headers={"If-Unmodified-Since-Version": str(data["version"]), "Content-Type": "application/json"})
        return int(r.headers.get("Last-Modified-Version") or 0)

    def post_items(self, objs: list[dict]) -> dict:
        """Batch create/update (≤50). Existing objects use PATCH semantics."""
        r = self.request("POST", f"{self.lib}/items", write=True, json=objs,
                         headers={"Content-Type": "application/json"}, timeout=300)
        return r.json() if r.content else {}

    def upload_file(self, key: str, path: Path, filename: str, old_md5: str | None) -> None:
        """Upload *path* as the stored file of attachment *key* named *filename*."""
        data = path.read_bytes()
        md5 = hashlib.md5(data).hexdigest()
        mtime = int(os.path.getmtime(path) * 1000)
        h = {"Content-Type": "application/x-www-form-urlencoded"}
        h["If-Match" if old_md5 else "If-None-Match"] = old_md5 or "*"
        r = self.request("POST", f"{self.lib}/items/{key}/file", write=True, headers=h,
                         data={"md5": md5, "filename": filename, "filesize": len(data), "mtime": mtime})
        j = r.json() if r.content else {}
        if j.get("exists"):
            return
        upload_url = j.get("url")
        if not upload_url:
            raise ZoteroError(f"Unexpected upload authorisation response: {j}")
        body = (j.get("prefix") or "").encode("latin-1") + data + (j.get("suffix") or "").encode("latin-1")
        target = upload_url if upload_url.startswith("http") else self._url(upload_url.replace("/api/", "", 1))
        hh = {"Content-Type": j.get("contentType") or "application/octet-stream",
              "Zotero-API-Key": self.write_key or "", "Zotero-Server-ID": self.server_id or ""}
        r2 = self.s.post(target, data=body, headers=hh, timeout=600)
        if r2.status_code not in (200, 201, 204):
            raise ZoteroError(f"File upload failed ({r2.status_code}): {r2.text[:300]}", r2.status_code)
        h2 = {"Content-Type": "application/x-www-form-urlencoded"}
        h2["If-Match" if old_md5 else "If-None-Match"] = old_md5 or "*"
        self.request("POST", f"{self.lib}/items/{key}/file", write=True, headers=h2,
                     data={"upload": j.get("uploadKey")})

    def create_saved_search(self, name: str, tags: list[str]) -> requests.Response:
        conditions = []
        for tag in tags:
            conditions.append({
                "condition": "tag",
                "operator": "is",
                "value": tag
            })
        payload = [{
            "name": name,
            "conditions": conditions
        }]
        return self.request("POST", f"{self.lib}/searches", write=True, json=payload)

    def wait(self, secs: float):
        time.sleep(secs)
