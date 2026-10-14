"""Wires the backend components together (one instance per Flask app)."""
from __future__ import annotations

import logging
import threading

from .config import Config
from .library.db import Database
from .fulltext import FulltextIndex, Indexer
from .notes import NotesIndex, Vault
from .notes.attachments import Attachments
from .notes.service import NotesService
from .references import Citations, CitationStore
from .library.organize import FolderRepository, SmartFolderRepository, TagRepository
from .library.repository import PaperRepository
from .settings import SettingsStore


class Services:
    def __init__(self, config: Config):
        config.ensure_dirs()
        self.config = config
        self.db = Database(config.db_path)
        self.db.migrate()
        self.settings = SettingsStore(config.settings_path)
        from .changes import ChangeFeed
        self.changes = ChangeFeed(config.db_path, config.notes_index_path)   # for the other windows (#34)
        self.db.on_write = self._library_written
        self.papers = PaperRepository(self.db, self.settings, config.pdf_dir)
        from .library.annotations import AnnotationStore
        self.annotations = AnnotationStore(self.db)
        self.folders = FolderRepository(self.db)
        self.tags = TagRepository(self.db)
        self.smart = SmartFolderRepository(self.db)
        self.fulltext = FulltextIndex(config.fulltext_db_path)
        self.indexer = Indexer(self.papers, self.fulltext)
        self.citations = Citations(CitationStore(config.citations_db_path), self.papers, self.settings)
        self.vault = Vault(config.notes_dir)
        self.vault.ensure()
        self.notes = NotesIndex(config.notes_index_path, self.vault)
        self.note_service = NotesService(self.vault, self.notes, self.papers, config.backups_dir)
        # Attachments no note uses go to their own trash; both trashes are emptied after a while.
        self.attachments = Attachments(self.vault, self.notes, self.settings)
        self.note_service.on_change = self.attachments.poke
        # Backups of library.db, settings and notes; the scheduler is started by create_app only.
        from .devices import Devices
        self.devices = Devices(config.auth_db_path)   # paired iPads/phones (#34)
        from .backup import Backups
        self.backups = Backups(self)
        # A changed citation key is rewritten in every note (with a backup first).
        self.papers.on_citekey_change(lambda _id, old, new: self.note_service.rename_key(old, new) if new else None)
        try:
            self.notes.sync()
        except Exception:  # notes must never stop the library from starting
            logging.getLogger(__name__).exception("Notes index sync failed")

    def find_pdf(self, item_id: str) -> dict:
        """Look for an open-access PDF of an item and attach it. Returns the finder's result."""
        from . import pdf_finder
        from .references import identifiers
        item = self.papers.get(item_id)
        _, arxiv_id = identifiers(item)   # from archive_id, an arXiv DOI, the URL, Extra …
        if not item.get("title") and not item.get("doi") and not arxiv_id:
            return {"status": "error", "message": "The item needs a title, DOI or arXiv ID to search for a PDF."}
        result = pdf_finder.find_and_download(item.get("title"), item.get("doi"), self.config.pdf_dir,
                                              self.settings.get("contact_email"), arxiv_id=arxiv_id)
        if result["status"] == "success":
            result["attachment"] = self.papers.add_attachment(item_id, "Full Text PDF", result["filename"], "pdf")
            self.attachments_changed()
        return result

    _pdf_lock = threading.Lock()   # one background search at a time (gentle on Unpaywall/arXiv)

    def find_pdf_later(self, item_id: str) -> None:
        """Search for a PDF in the background (items added from the browser button)."""
        def run():
            with self._pdf_lock:
                try:
                    self.find_pdf(item_id)
                except Exception:
                    logging.getLogger(__name__).exception("Background PDF search failed for %s", item_id)
        threading.Thread(target=run, name="find-pdf", daemon=True).start()

    def attachments_changed(self) -> None:
        """Call after adding/removing files so the full-text index catches up."""
        if self.settings.get("fulltext_auto_index"):
            self.indexer.sync()

    def _library_written(self) -> None:
        from flask import has_request_context, request
        from .changes import CLIENT_HEADER, kind_of
        if has_request_context():
            self.changes.record({kind_of(request.path)}, request.headers.get(CLIENT_HEADER))
        else:
            self.changes.record({"library"})   # background work: everyone refreshes
