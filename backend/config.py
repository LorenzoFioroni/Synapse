"""Filesystem layout of the app.

Everything the user owns (the library database, the full-text index, PDFs and
settings) lives under ``data/`` so the code folders stay clean and the data
folder can be backed up or moved as a whole.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    data_dir: Path
    # Extra host names (besides 127.0.0.1 / localhost) the server answers to, e.g. a LAN address
    # when it is started with --host 0.0.0.0. See backend/guard.py.
    allowed_hosts: set[str] = field(default_factory=set)
    # True when the server listens on the network (``--lan`` or ``--host 0.0.0.0``): other devices can
    # pair (Settings → Devices) and requests naming this computer by a LAN address are accepted.
    lan: bool = False

    @classmethod
    def from_env(cls, data_dir: str | os.PathLike | None = None) -> Config:
        # SYNAPSE_DATA_DIR; ZCLONE_DATA_DIR is the old name, still accepted
        d = data_dir or os.environ.get("SYNAPSE_DATA_DIR") or os.environ.get("ZCLONE_DATA_DIR") or (APP_ROOT / "data")
        return cls(Path(d).expanduser().resolve())

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
    def auth_db_path(self) -> Path:
        # Paired devices (hashed tokens). Not part of backups.
        return self.data_dir / "auth.db"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def frontend_dir(self) -> Path:
        return APP_ROOT / "frontend"

    # -- setup -------------------------------------------------------------
    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
