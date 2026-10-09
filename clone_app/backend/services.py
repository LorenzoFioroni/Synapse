"""Wires the backend components together (one instance per Flask app)."""
from __future__ import annotations

import logging

from .config import Config
from .db import Database
from .fulltext import FulltextIndex, Indexer
from .notes import NotesIndex, Vault
from .organize import FolderRepository, SmartFolderRepository, TagRepository
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
        self.folders = FolderRepository(self.db)
        self.tags = TagRepository(self.db)
        self.smart = SmartFolderRepository(self.db)
        self.fulltext = FulltextIndex(config.fulltext_db_path)
        self.indexer = Indexer(self.papers, self.fulltext)
        self.vault = Vault(config.notes_dir)
        self.vault.ensure()
        self.notes = NotesIndex(config.notes_index_path, self.vault)
        try:
            self.notes.sync()
        except Exception:  # notes must never stop the library from starting
            logging.getLogger(__name__).exception("Notes index sync failed")

    def attachments_changed(self) -> None:
        """Call after adding/removing files so the full-text index catches up."""
        if self.settings.get("fulltext_auto_index"):
            self.indexer.sync()
