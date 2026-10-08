"""Wires the backend components together (one instance per Flask app)."""
from __future__ import annotations

from .config import Config
from .db import Database
from .repository import PaperRepository
from .settings import SettingsStore


class Services:
    def __init__(self, config: Config):
        config.ensure_dirs()
        self.config = config
        self.db = Database(config.db_path)
        self.db.migrate()
        self.settings = SettingsStore(config.settings_path)
        self.papers = PaperRepository(self.db, self.settings, config.pdf_dir)

    def attachments_changed(self) -> None:
        pass
