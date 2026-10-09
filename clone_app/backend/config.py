"""Filesystem layout of the app.

Everything the user owns (the library database, the full-text index, PDFs and
settings) lives under ``data/`` so the code folders stay clean and the data
folder can be backed up or moved as a whole.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    data_dir: Path

    @classmethod
    def from_env(cls, data_dir: str | os.PathLike | None = None) -> "Config":
        d = data_dir or os.environ.get("ZCLONE_DATA_DIR") or (APP_ROOT / "data")
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
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

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
        self._migrate_legacy_layout()

    def _migrate_legacy_layout(self) -> None:
        """Move data from the pre-restructure layout (papers.db, pdf_storage/)."""
        legacy_db = APP_ROOT / "papers.db"
        if legacy_db.exists() and legacy_db.stat().st_size > 0 and not self.db_path.exists():
            log.info("Migrating %s -> %s", legacy_db, self.db_path)
            shutil.move(str(legacy_db), str(self.db_path))
        legacy_pdfs = APP_ROOT / "pdf_storage"
        if legacy_pdfs.is_dir():
            for f in legacy_pdfs.iterdir():
                if f.is_file() and f.name != ".DS_Store" and not (self.pdf_dir / f.name).exists():
                    shutil.move(str(f), str(self.pdf_dir / f.name))
