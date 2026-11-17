"""Wires the backend components together (one instance per Flask app)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from .mcp.http import HttpTransport


class Services:
    def __init__(self, config: Config):
        config.ensure_dirs()
        self.config = config
        self.db = Database(config.db_path)
        self.db.migrate()
        self.settings = SettingsStore(config.settings_path)
        from .online.browser_pdfs import BrowserPdfs

        self.browser_pdfs = BrowserPdfs()
        from .companion import Companions, Conversations

        self.companions = Companions()  # questions for the user's own computer (docs/companion.md)
        self.ai_conversations = Conversations(config.ai_db_path)  # what Ask AI keeps (ai.db)
        self.companions.on_finish = self.ai_conversations.record_answer
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
        from .notes.pdfs import NotePdfs

        self.note_pdfs = NotePdfs(self.db, self.vault)  # the PDFs beside the notes
        self.note_service = NotesService(
            self.vault,
            self.notes,
            self.papers,
            self._backup_dir,
            history_dir=config.notes_history_dir,
            pdfs=self.note_pdfs,
        )
        self.note_service.history.on_tick = self.sync_external_notes
        self.note_service.history.on_commit = lambda: self.changes.record({"history"})
        # Attachments no note uses go to their own trash; both trashes are emptied after a while.
        self.attachments = Attachments(self.vault, self.notes, self.settings)
        self.note_service.on_change = self.attachments.poke
        self.attachments.on_purged = self.note_pdfs.forget_under
        # PDFs in the notes folders: their text is searched with the notes (own tables in fulltext.db)
        self.indexer.note_pdfs = lambda: [(p["id"], self.vault.root / p["path"]) for p in self.note_pdfs.listing()]
        self.note_service.pdf_search = self._search_note_pdfs
        self.note_service.pdf_pages = self.fulltext.note_pages
        self.note_service.on_pdfs_changed = self.note_pdfs_changed
        from .notes.attachments import RETENTION

        self.note_pdfs.retention = lambda: RETENTION.get(self.settings.get("notes_trash_retention"))
        # Tasks: records in library.db that notes show as {{task:id}} lines.
        from .tasks import TaskService, TaskStore

        self.task_store = TaskStore(self.db)
        self.tasks = TaskService(self.task_store, self.note_service)
        self.attachments.extra_texts = lambda: [(f"task:{i}", b) for i, b in self.task_store.bodies()]
        self.attachments.on_maintain = self.purge_task_trash
        # Backups of library.db, settings and notes; the scheduler is started by create_app only.
        from .access.confirm import Confirmations
        from .access.devices import Devices
        from .access.passkeys import Passkeys
        from .access.tokens import AccessTokens

        self.devices = Devices(config.auth_db_path)  # paired iPads/phones (--lan)
        self.passkeys = Passkeys(config.auth_db_path, self.devices)  # sign a device in without pairing
        self.tokens = AccessTokens(config.auth_db_path)  # AI apps on other computers (/mcp)
        self.confirmations = Confirmations()  # devices that just confirmed with a passkey (admin actions)
        self._mcp_http: HttpTransport | None = None
        from .backup import Backups
        from .backup.remote import RemoteStorage
        from .online.backup_oauth import BackupOAuth

        self.backups = Backups(self, RemoteStorage(config.backup_remote_dir, BackupOAuth()))
        self.presence = Presence()  # the open windows, when the server stops with the last one
        from .config import APP_ROOT
        from .online.updates import Updates

        # Newer code on GitHub, installed by the deployment's updater (Settings → Updates).
        self.updates = Updates(
            config.update_source,
            config.commit,
            config.updater_url,
            config.updater_token,
            before_install=lambda: self.backups.create("before updating Synapse"),
            app_root=APP_ROOT,
            container=config.container,
        )
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
        self.citations.reference_sources = self.plugins.fetch_references
        self.citations.reference_source_title = self.plugins.reference_source_title
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
        try:
            self.note_pdfs.sync()
        except Exception:
            logging.getLogger(__name__).exception("Notes PDF sync failed")

    def sync_external_notes(self) -> None:
        """Refresh disposable indexes and every window after files change outside the app."""
        result = self.notes.sync()
        try:
            pdf_result = self.note_pdfs.sync()
            result = {**result, **{f"pdf_{k}": v for k, v in pdf_result.items()}}
            if pdf_result["added"] or pdf_result["changed"] or pdf_result["adopted"]:
                self.note_pdfs_changed()
        except Exception:
            logging.getLogger(__name__).exception("Notes PDF sync failed")
        if any(result.values()):
            self.changes.record({"notes"})
            self.attachments.poke()

    def mcp_http(self) -> HttpTransport:
        """The MCP server over HTTP (``/mcp``), built on first use. It shares these services with
        the web app, so its writes reach the change feed like the app's own."""
        with self._mcp_lock:
            if self._mcp_http is None:
                from .mcp import build_server
                from .mcp.http import HttpTransport

                # Notes and tasks written by a tool show up in the open windows. The request itself
                # counts only as a library write (kind_of), which would hide a task change.
                def wrote() -> None:
                    self.changes.record({"notes", "tasks"})

                self._mcp_http = HttpTransport(lambda: build_server(self, remote=True), wrote)
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

    def add_by_identifier(
        self,
        identifier: str,
        *,
        folder_id: int | None = None,
        tags=(),
        allow_duplicate: bool = False,
        fetch_references=None,
    ) -> dict:
        """Add by Identifier: look a DOI, arXiv id or ISBN up online and add the item. Returns
        ``{"status": "added", "item"}``, ``{"status": "duplicate", "item_id", "metadata"}`` (nothing
        added) or ``{"status": "failed", "message"}`` (the lookup's own sentence)."""
        from .online.http import LookupFailed, message_of
        from .online.metadata import known_authors
        from .online.metadata import lookup as lookup_meta

        try:
            meta = lookup_meta(
                sources=self.plugins.lookup_metadata,
                identifier=identifier,
                known=known_authors(self.papers.list()),
                email=self.settings.get("contact_email"),
            )
        except LookupFailed as e:
            return {"status": "failed", "message": message_of(e)}
        dup = self.papers.find_duplicate(meta)
        if dup and not allow_duplicate:
            return {"status": "duplicate", "item_id": dup, "metadata": meta}
        item = self.papers.create({**meta, "folder_id": folder_id, "tags": list(tags or [])})
        self.citations.maybe_enqueue(item, fetch_references)
        return {"status": "added", "item": item}

    def fetch_references_now(self, item_id: str) -> dict:
        """Fetch an item's reference list now. ``{"status": "ok", "view"}`` or ``{"status": "failed",
        "message"}``: a lookup's own sentence, or only the kind of error (its text can hold request URLs
        with API keys)."""
        from .online.http import LookupFailed, message_of

        try:
            return {"status": "ok", "view": self.citations.fetch_now(item_id)}
        except LookupFailed as e:
            return {"status": "failed", "message": message_of(e)}
        except Exception as e:
            logging.getLogger(__name__).exception("Fetching the references of %s failed", item_id)
            return {"status": "failed", "message": f"Could not fetch references ({type(e).__name__})."}

    def note_pdfs_changed(self) -> None:
        """A PDF was added to or came back to the notes folders: its text is indexed."""
        self.attachments_changed()

    def _search_note_pdfs(self, query: str) -> list[dict]:
        hits = self.fulltext.match_notes(query)
        rows = {p["id"]: p for p in self.note_pdfs.listing()}
        return [
            {"path": rows[i]["path"], "title": rows[i]["name"], "snippet": snip, "kind": "pdf", "id": i}
            for i, snip in hits.items()
            if i in rows
        ]

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
