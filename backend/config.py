"""Filesystem layout of the app.

Everything the user owns (the library database, the full-text index, PDFs and
settings) lives under ``data/`` so the code folders stay clean and the data
folder can be backed up or moved as a whole.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

APP_ROOT = Path(__file__).resolve().parent.parent

SERVICE_NAME = re.compile(r"[a-z][a-z0-9-]{0,39}")


class LocalServiceError(ValueError):
    pass


def parse_local_service(text: str) -> tuple[str, str]:
    """``ollama=http://ollama:11434`` → ``("ollama", "http://ollama:11434")``: a name and an http(s)
    origin (no path, query or credentials)."""
    name, sep, url = (text or "").strip().partition("=")
    name = name.strip().lower()
    if not sep or not SERVICE_NAME.fullmatch(name):
        raise LocalServiceError(f"--local-service {text!r}: write NAME=URL, like ollama=http://ollama:11434.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as e:
        raise LocalServiceError(f"--local-service {name}: {url!r} is not an address.") from e
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise LocalServiceError(
            f"--local-service {name}: the address starts with http:// or https:// and names a host."
        )
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.password:
        raise LocalServiceError(f"--local-service {name}: the address alone, like http://ollama:11434 (no path).")
    host = parts.hostname.lower()
    netloc = f"[{host}]" if ":" in host else host
    return name, f"{parts.scheme.lower()}://{netloc}" + (f":{port}" if port is not None else "")


def parse_local_services(text: str | None) -> dict[str, str]:
    """``SYNAPSE_LOCAL_SERVICES``: ``NAME=URL`` entries separated by commas or spaces."""
    return dict(parse_local_service(item) for item in re.split(r"[\s,]+", text or "") if item)


@dataclass
class Config:
    data_dir: Path
    # Extra host names (besides 127.0.0.1 / localhost) the server answers to, e.g. a LAN address
    # when it is started with --host 0.0.0.0. See backend/access/guard.py.
    allowed_hosts: set[str] = field(default_factory=set)
    # True when the server listens on the network (``--lan`` or ``--host 0.0.0.0``): other devices can
    # pair (Settings → Devices) and requests naming this computer by a LAN address are accepted.
    lan: bool = False
    # ``--public-url https://name``: the HTTPS address a tunnel or reverse proxy (such as Cloudflare
    # Tunnel) serves Synapse at. Requests for that name are never trusted as "this computer", however
    # they connect. See backend/access/proxy.py.
    public_url: str | None = None
    # ``--safe-mode``: no plugin is started, so a plugin that breaks the app can be turned off.
    safe_mode: bool = False
    # ``--dev-plugins DIR``: plugins being written, loaded from DIR and reloaded when a file changes.
    dev_plugins_dir: Path | None = None
    # ``--container``: running in a container behind a tunnel (docs/docker.md). It listens on the
    # container's network but, as with loopback listening, answers only to loopback and the public
    # name; the tunnel's container, on a private network, may report the visitor's address.
    container: bool = False
    # ``--local-service NAME=URL`` or SYNAPSE_LOCAL_SERVICES: services the deployment runs next to
    # Synapse, such as Ollama in its own container. Plugins with the "loopback" permission may reach
    # exactly these origins as if they were on this computer (backend/plugins/http.py). They come only
    # from whoever starts the server, never from Settings: a signed-in device must not be able to point
    # the server at addresses inside its network.
    local_services: dict[str, str] = field(default_factory=dict)
    # Updates of Synapse itself (backend/online/updates.py, docs/updates.md), from the environment
    # only: where the code comes from (SYNAPSE_SOURCE, https://github.com/owner/repo.git#branch), the
    # commit this copy was built from (SYNAPSE_COMMIT, set by the Docker image) and the updater that
    # installs updates (SYNAPSE_UPDATER, SYNAPSE_UPDATER_TOKEN).
    update_source: str | None = None
    commit: str | None = None
    updater_url: str | None = None
    updater_token: str | None = None

    @classmethod
    def from_env(cls, data_dir: str | os.PathLike | None = None) -> Config:
        # SYNAPSE_DATA_DIR; ZCLONE_DATA_DIR is the old name, still accepted
        d = data_dir or os.environ.get("SYNAPSE_DATA_DIR") or os.environ.get("ZCLONE_DATA_DIR") or (APP_ROOT / "data")
        env = os.environ
        return cls(
            Path(d).expanduser().resolve(),
            local_services=parse_local_services(env.get("SYNAPSE_LOCAL_SERVICES")),
            update_source=env.get("SYNAPSE_SOURCE") or None,
            commit=env.get("SYNAPSE_COMMIT") or None,
            updater_url=env.get("SYNAPSE_UPDATER") or None,
            updater_token=env.get("SYNAPSE_UPDATER_TOKEN") or None,
        )

    @property
    def other_devices(self) -> bool:
        """Whether other devices can reach this server (on the network or through a tunnel)."""
        return self.lan or bool(self.public_url)

    # -- paths -------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.db"

    @property
    def fulltext_db_path(self) -> Path:
        # Kept separate from the library so extracted text never bloats it.
        return self.data_dir / "fulltext.db"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def notes_dir(self) -> Path:
        # Markdown notes: the files are the source of truth.
        return self.data_dir / "notes"

    @property
    def notes_history_dir(self) -> Path:
        # Git repository of every version of the notes' text (its work tree is notes_dir).
        return self.data_dir / "notes-history.git"

    @property
    def notes_index_path(self) -> Path:
        # Derived from notes_dir; safe to delete (rebuilt on startup).
        return self.data_dir / "notes_index.db"

    @property
    def citations_db_path(self) -> Path:
        # Reference lists fetched online (the citation graph). Can be fetched again if lost.
        return self.data_dir / "citations.db"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def backup_remote_dir(self) -> Path:
        # Account credentials and delivery receipts stay outside backup archives.
        return self.data_dir / "backup-remote"

    @property
    def auth_db_path(self) -> Path:
        # Paired devices (hashed tokens). Not part of backups.
        return self.data_dir / "auth.db"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def plugins_dir(self) -> Path:
        # Plugins the user installed: one folder each, with a manifest.json (docs/plugins.md).
        return self.data_dir / "plugins"

    @property
    def bundled_plugins_dir(self) -> Path:
        # Plugins that come with Synapse; off until the user turns them on.
        return APP_ROOT / "plugins"

    @property
    def plugin_data_dir(self) -> Path:
        # What plugins store, one folder per plugin; apart from their code, and in backups.
        return self.data_dir / "plugin-data"

    @property
    def fields_db_path(self) -> Path:
        # Values plugins attach to papers (backend/plugins/fields.py); in backups.
        return self.data_dir / "fields.db"

    @property
    def plugins_state_path(self) -> Path:
        # Which plugins are on, and their settings.
        return self.data_dir / "plugins.json"

    @property
    def frontend_dir(self) -> Path:
        return APP_ROOT / "frontend"

    # -- setup -------------------------------------------------------------
    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
