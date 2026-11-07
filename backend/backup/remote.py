"""A Settings-managed rclone destination. Credentials are separate from archived preferences."""

from __future__ import annotations

import configparser
import io
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .errors import BackupError
from .rclone import Rclone

# An explicit list avoids accepting backend options that can execute commands or read server files.
PROVIDERS: list[dict] = [
    {
        "id": "drive",
        "name": "Google Drive",
        "oauth": True,
        "fields": [
            ("client_id", "Application client ID", False, True),
            ("client_secret", "Application client secret", True, True),
            ("root_folder_id", "Root folder ID (optional)", False, False),
        ],
    },
    {
        "id": "onedrive",
        "name": "Microsoft OneDrive",
        "oauth": True,
        "fields": [
            ("client_id", "Application client ID", False, True),
            ("client_secret", "Application client secret", True, True),
            ("drive_id", "Drive ID (optional; otherwise your default drive)", False, False),
            ("drive_type", "Drive type (personal, business or documentLibrary; optional)", False, False),
        ],
    },
    {
        "id": "dropbox",
        "name": "Dropbox",
        "oauth": True,
        "fields": [("client_id", "Application key", False, True), ("client_secret", "Application secret", True, True)],
    },
    {
        "id": "s3",
        "name": "S3 / S3-compatible storage",
        "oauth": False,
        "fields": [
            ("provider", "S3 provider (AWS, Other, …)", False, True),
            ("access_key_id", "Access key ID", True, True),
            ("secret_access_key", "Secret access key", True, True),
            ("region", "Region (optional)", False, False),
            ("endpoint", "Endpoint URL (optional for AWS)", False, False),
        ],
    },
    {
        "id": "webdav",
        "name": "WebDAV / Nextcloud",
        "oauth": False,
        "fields": [
            ("url", "WebDAV URL", False, True),
            ("vendor", "Vendor (other, nextcloud, owncloud, …)", False, False),
            ("user", "Username", False, True),
            ("pass", "Password / application password", True, True),
        ],
    },
    {
        "id": "sftp",
        "name": "SFTP",
        "oauth": False,
        "fields": [
            ("host", "Host", False, True),
            ("port", "Port (optional; default 22)", False, False),
            ("user", "Username", False, True),
            ("pass", "Password (or supply a private key)", True, False),
            ("key_pem", "Private key (PEM, optional)", True, False),
            ("known_hosts", "SSH known_hosts entry", False, True),
        ],
    },
]


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class RemoteStorage:
    def __init__(self, root: Path, oauth):
        self.root = root
        self.oauth = oauth
        self.rclone = Rclone(root / "rclone.conf")
        self.lock = threading.RLock()

    def metadata(self) -> dict:
        try:
            return json.loads((self.root / "destination.json").read_text("utf-8"))
        except FileNotFoundError:
            return {}
        except (ValueError, OSError) as e:
            raise BackupError(
                "The remote backup configuration cannot be read. Save it again in Settings → Backups."
            ) from e

    def _options(self) -> dict:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.rclone.config, encoding="utf-8")
        return dict(parser["remote"]) if parser.has_section("remote") else {}

    def _write_options(self, options: dict) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser["remote"] = options
        out = io.StringIO()
        parser.write(out)
        write_private(self.rclone.config, out.getvalue())

    def configuration(self) -> dict:
        meta, options = self.metadata(), self._options()
        provider = next((p for p in PROVIDERS if p["id"] == meta.get("provider")), None)
        fields = provider["fields"] if provider else []
        return {
            **meta,
            "available": self.rclone.available,
            "configured": bool(options),
            "authorized": bool(options.get("token")) if provider and provider["oauth"] else bool(options),
            "options": {
                key: options.get(key, "") for key, _, secret, _ in fields if not secret and key != "known_hosts"
            },
            "stored_secrets": [key for key, _, secret, _ in fields if secret and options.get(key)],
            "known_hosts": (self.root / "known_hosts").read_text("utf-8")
            if (self.root / "known_hosts").exists()
            else "",
        }

    def save(self, data: dict) -> dict:
        with self.lock:
            provider = next((p for p in PROVIDERS if p["id"] == data.get("provider")), None)
            if provider is None:
                raise BackupError("Choose a supported remote storage provider.")
            folder = str(data.get("folder") or "").strip()
            if not folder or len(folder) > 1000 or re.search(r"[:\x00-\x1f\\]", folder) or ".." in folder.split("/"):
                raise BackupError(
                    "Enter a destination folder (for S3, include the bucket), without '..' or a remote prefix."
                )
            values = data.get("options")
            if not isinstance(values, dict) or set(values) - {f[0] for f in provider["fields"]}:
                raise BackupError("The remote connection contains unsupported options.")
            previous = self.metadata()
            namespace = data.get("namespace") or previous.get("namespace") or "synapse-" + uuid.uuid4().hex
            if not isinstance(namespace, str) or not re.fullmatch(r"synapse-[a-f0-9]{32}", namespace):
                raise BackupError("Choose a Synapse backup set from the remote folder.")
            old = self._options() if previous.get("provider") == provider["id"] else {}
            options = {"type": provider["id"]}
            for key, label, secret, required in provider["fields"]:
                value = values.get(key, "")
                if not isinstance(value, str) or len(value) > 20000 or "\x00" in value:
                    raise BackupError(f"Invalid {label}.")
                value = value.strip()
                if key == "key_pem":
                    value = value.replace("\r\n", "\n").replace("\n", "\\n")
                if not value and secret:
                    value = old.get(key, "")
                if required and not value:
                    raise BackupError(f"Enter {label}.")
                if key not in ("key_pem", "known_hosts") and ("\n" in value or "\r" in value):
                    raise BackupError(f"Invalid {label}.")
                if key in ("url", "endpoint") and value:
                    parts = urlsplit(value)
                    if (
                        parts.scheme != "https"
                        or not parts.hostname
                        or parts.username
                        or parts.password
                        or parts.query
                        or parts.fragment
                    ):
                        raise BackupError(f"{label} must be an HTTPS address without embedded credentials.")
                if key == "port" and value and (not value.isdigit() or not 1 <= int(value) <= 65535):
                    raise BackupError("SFTP port must be between 1 and 65535.")
                if key == "host" and value and not re.fullmatch(r"[a-zA-Z0-9.:-]+", value):
                    raise BackupError("Enter a host name or IP address without a URL or path.")
                if key == "drive_type" and value and value not in ("personal", "business", "documentLibrary"):
                    raise BackupError("Choose personal, business or documentLibrary for the drive type.")
                if key == "known_hosts":
                    if value:
                        write_private(self.root / "known_hosts", value + "\n")
                        options["known_hosts_file"] = str(self.root / "known_hosts")
                    continue
                if key == "pass" and value and value != old.get(key):
                    value = self.rclone.run("obscure", "-", input=value).strip()
                if value:
                    options[key] = value
            if provider["id"] == "sftp" and not (options.get("pass") or options.get("key_pem")):
                raise BackupError("Enter an SFTP password or private key.")
            if provider["oauth"] and all(options.get(k) == old.get(k) for k in ("client_id", "client_secret")):
                for key in ("token", "drive_id", "drive_type"):
                    if key not in options and old.get(key):
                        options[key] = old[key]
            if provider["id"] == "drive":
                options["scope"] = "drive.file"
            if provider["id"] == "onedrive":
                options["access_scopes"] = "Files.ReadWrite offline_access User.Read"
                options["disable_site_permission"] = "true"
            if provider["id"] == "onedrive" and options.get("drive_id") and not options.get("drive_type"):
                raise BackupError("Enter the drive type together with its drive ID.")
            self._write_options(options)
            meta = {
                "provider": provider["id"],
                "folder": folder,
                "namespace": namespace,
                "revision": previous.get("revision")
                if old == options and previous.get("folder") == folder and previous.get("namespace") == namespace
                else uuid.uuid4().hex,
            }
            write_private(self.root / "destination.json", json.dumps(meta))
            self.oauth.cancel()
            return self.configuration()

    def authorize(self, owner: str, callback: str) -> dict:
        with self.lock:
            options = self._options()
            if options.get("type") not in ("drive", "onedrive", "dropbox"):
                raise BackupError("This provider uses the credentials entered above instead of browser sign-in.")
            return self.oauth.begin(owner, callback, options, self.metadata()["revision"])

    def finish_authorization(self, owner: str, args: dict) -> None:
        with self.lock:
            options = self._options()
            token, extra = self.oauth.finish(owner, args, self.metadata().get("revision"))
            options.update(extra)
            options["token"] = json.dumps(token)
            self._write_options(options)

    def destination(self) -> str:
        meta = self.metadata()
        if not meta:
            raise BackupError("Configure remote storage in Settings → Backups first.")
        return f"remote:{meta['folder'].rstrip('/')}/{meta['namespace']}"

    def test(self) -> None:
        with self.lock, tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / (".synapse-test-" + uuid.uuid4().hex)
            probe.write_bytes(b"Synapse backup connection test\n")
            target = self.destination() + "/" + probe.name
            self.rclone.run("copyto", str(probe), target)
            try:
                received = Path(tmp) / "received"
                self.rclone.run("copyto", target, str(received))
                if received.read_bytes() != probe.read_bytes():
                    raise BackupError("The remote storage connection returned different file contents.")
            finally:
                self.rclone.run("deletefile", target)

    def entries(self) -> list[dict]:
        with self.lock:
            return self.rclone.json("lsjson", self.destination(), "--files-only", "--max-depth", "1")

    def backup_sets(self) -> list[str]:
        with self.lock:
            meta = self.metadata()
            if not meta:
                raise BackupError("Save the remote connection first.")
            entries = self.rclone.json("lsjson", f"remote:{meta['folder']}", "--dirs-only", "--max-depth", "1")
            return sorted(e["Name"] for e in entries if re.fullmatch(r"synapse-[a-f0-9]{32}", e.get("Name", "")))

    def upload(self, path: Path) -> None:
        with self.lock:
            target = self.destination() + "/" + path.name
            self.rclone.run("copyto", str(path), target + ".part", timeout=900)
            self.rclone.run("moveto", target + ".part", target, "--immutable", timeout=900)

    def download(self, name: str, dest: Path) -> None:
        with self.lock:
            self.rclone.run("copyto", self.destination() + "/" + name, str(dest), timeout=900)

    def delete(self, name: str) -> None:
        with self.lock:
            self.rclone.run("deletefile", self.destination() + "/" + name)
