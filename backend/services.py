"""Wires the backend components together (one instance per Flask app)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .config import Config
from .desktop import Presence
from .library.db import Database
from .library.organize import FolderRepository, SmartFolderRepository, TagRepository
from .library.repository import PaperRepository
from .notes import NotesIndex, Vault
from .notes.attachments import Attachments
from .notes.service import NotesService
from .pdf.fulltext import FulltextIndex, Indexer
from .references.service import Citations
from .references.store import CitationStore
from .settings import SettingsStore


class Services:
    def __init__(self, config: Config):
        config.ensure_dirs()
        self.config = config
        self.db = Database(config.db_path)
        self.db.migrate()
        self.settings = SettingsStore(config.settings_path)
        from .changes import ChangeFeed

        self.changes = ChangeFeed(
            config.db_path, config.notes_index_path
        )  # tells other windows and devices what changed
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
        from .references.connections import Connections

        self.connections = Connections(self.citations, self.papers)  # how outside papers connect to the library
        self.vault = Vault(config.notes_dir)
        self.vault.ensure()
        self.notes = NotesIndex(config.notes_index_path, self.vault)
        # key renames back up the notes into the backup folder from Settings, like Backups does
        self.note_service = NotesService(self.vault, self.notes, self.papers, self._backup_dir)
        # Attachments no note uses go to their own trash; both trashes are emptied after a while.
        self.attachments = Attachments(self.vault, self.notes, self.settings)
        self.note_service.on_change = self.attachments.poke
        # Tasks: records in library.db that notes show as {{task:id}} lines.
        from .tasks import TaskService, TaskStore

        self.task_store = TaskStore(self.db)
        self.tasks = TaskService(self.task_store, self.note_service)
        self.attachments.extra_texts = lambda: [(f"task:{i}", b) for i, b in self.task_store.bodies()]
        self.attachments.on_maintain = self.purge_task_trash
        # Backups of library.db, settings and notes; the scheduler is started by create_app only.
        from .access.devices import Devices
        from .access.passkeys import Passkeys
        from .access.tokens import AccessTokens

        self.devices = Devices(config.auth_db_path)  # paired iPads/phones (--lan)
        self.passkeys = Passkeys(config.auth_db_path, self.devices)  # sign a device in without pairing
        self.tokens = AccessTokens(config.auth_db_path)  # AI apps on other computers (/mcp)
        self._mcp_http = None
        from .backup import Backups

        self.backups = Backups(self)
        self.presence = Presence()  # the open windows, when the server stops with the last one
        # Plugins (backend/plugins/), started by create_app (or mcp_server.py), not here. They hear
        # about changes to items and notes through the listeners below.
        from .plugins import LibraryReader, PluginHost

        self.plugins = PluginHost(
            config,
            library=LibraryReader(self),
            before_backup=self.backups.before,
            changed=lambda: self.changes.record({"plugins"}),
            settings=self.settings,
            fields_changed=lambda: self.changes.record({"fields"}),
            sources_changed=lambda: self.changes.record({"sources"}),
            services=self,
        )
        self.papers.on_items(lambda event, ids, **details: self.plugins.emit(event, ids=ids, **details))
        self.papers.on_citekey_change(lambda i, old, new: self.plugins.emit("citekey.changed", id=i, old=old, new=new))
        self.note_service.saved_listeners.append(
            lambda path, h, by: self.plugins.emit("note.saved", path=path, hash=h, by=by)
        )
        # A changed citation key is rewritten in every note (with a backup first).
        self.papers.on_citekey_change(lambda _id, old, new: self.note_service.rename_key(old, new) if new else None)
        self.papers.on_citekey_change(lambda _id, old, new: self.tasks.rename_key(old, new) if new and old else None)
        try:
            self.notes.sync()
        except Exception:  # notes must never stop the library from starting
            logging.getLogger(__name__).exception("Notes index sync failed")

    def mcp_http(self):
        """The MCP server over HTTP (``/mcp``), built on first use. It shares these services with
        the web app, so its writes reach the change feed like the app's own."""
        with self._mcp_lock:
            if self._mcp_http is None:
                from .mcp import build_server
                from .mcp.http import HttpTransport

                # Notes and tasks written by a tool show up in the open windows. The request itself
                # counts only as a library write (kind_of), which would hide a task change.
                self._mcp_http = HttpTransport(
                    lambda: build_server(self, remote=True), lambda: self.changes.record({"notes", "tasks"})
                )
            return self._mcp_http

    _mcp_lock = threading.Lock()

    def purge_task_trash(self) -> int:
        """Empty the tasks trash after the same retention as the notes trash."""
        from datetime import datetime, timedelta, timezone

        from .notes.attachments import RETENTION

        secs = RETENTION.get(self.settings.get("notes_trash_retention"))
        if not secs:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self.task_store.purge(cutoff)

    def _backup_dir(self) -> Path:
        return self.backups.dir

    def find_pdf(self, item_id: str) -> dict:
        """Look for an open-access PDF of an item and attach it. Returns the finder's result."""
        from .library.identifiers import identifiers
        from .online import pdf_finder

        item = self.papers.get(item_id)
        _, arxiv_id = identifiers(item)  # from archive_id, an arXiv DOI, the URL, Extra …
        if not item.get("title") and not item.get("doi") and not arxiv_id:
            return {"status": "error", "message": "The item needs a title, DOI or arXiv ID to search for a PDF."}
        paper = {"title": item.get("title"), "doi": item.get("doi"), "arxiv": arxiv_id}
        sources = self.plugins.has_pdf_sources() if self.plugins else False
        result = pdf_finder.find_and_download(
            item.get("title"),
            item.get("doi"),
            self.config.pdf_dir,
            self.settings.get("contact_email"),
            arxiv_id=arxiv_id,
            # where to look: the plugins' PDF sources (Unpaywall and arXiv: plugins/open-access-pdfs)
            candidates=(lambda by_title: self.plugins.pdf_candidates(paper, by_title=by_title)) if sources else None,
        )
        if result["status"] == "success":
            result["attachment"] = self.papers.add_attachment(item_id, "Full Text PDF", result["filename"], "pdf")
            self.attachments_changed()
        return result

    _pdf_lock = threading.Lock()  # one background search at a time (gentle on Unpaywall/arXiv)

    def find_pdf_later(self, item_id: str) -> None:
        """Search for a PDF in the background (items added from the browser button)."""

        def run():
            with self._pdf_lock:
                try:
                    self.find_pdf(item_id)
                except Exception:
                    logging.getLogger(__name__).exception("Background PDF search failed for %s", item_id)

        threading.Thread(target=run, name="find-pdf", daemon=True).start()

    def add_paper(
        self, paper: dict, *, folder_id: int | None = None, tags=(), lookup: bool = True
    ) -> tuple[dict, bool]:
        """Add a paper from outside the library (an outside record: title, authors, doi, arxiv …),
        as Add by Identifier does: its metadata looked up online by DOI or arXiv id (the record's
        own fields when that fails, offline say), the duplicate check, a citation key. Returns
        (item, False), or (the library item it already is, True)."""
        from .library.identifiers import norm_arxiv, norm_doi
        from .online.http import LookupFailed
        from .online.metadata import known_authors
        from .online.metadata import lookup as lookup_meta

        arx = norm_arxiv(paper.get("arxiv")) or norm_arxiv(paper.get("archive_id"))
        doi = norm_doi(paper.get("doi"))
        meta = {
            k: paper[k]
            for k in ("title", "authors", "year", "abstract", "journal", "item_type", "url", "volume", "pages", "date")
            if paper.get(k)
        }
        if doi:
            meta["doi"] = paper.get("doi")
        if arx:
            meta.setdefault("archive_id", f"arXiv:{arx}")
            meta.setdefault("url", f"https://arxiv.org/abs/{arx}")
            meta.setdefault("item_type", "preprint")
        dup = self.papers.find_duplicate(meta)
        if dup:
            return self.papers.get(dup), True
        if lookup and (arx or doi):
            try:
                fetched = lookup_meta(
                    sources=self.plugins.lookup_metadata,
                    identifier=f"arXiv:{arx}" if arx else doi,
                    known=known_authors(self.papers.list()),
                    email=self.settings.get("contact_email"),
                )
                meta.update({k: v for k, v in fetched.items() if v})
            except (LookupFailed, OSError) as e:
                logging.getLogger(__name__).info("Adding %s without looking it up: %s", arx or doi, e)
            except Exception:  # requests errors and the like: the record is enough
                logging.getLogger(__name__).exception("Looking up %s failed", arx or doi)
            dup = self.papers.find_duplicate(meta)
            if dup:
                return self.papers.get(dup), True
        item = self.papers.create({**meta, "folder_id": folder_id, "tags": list(tags or [])})
        return item, False

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
            self.changes.record({"library"})  # background work: everyone refreshes
