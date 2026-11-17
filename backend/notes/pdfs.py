"""PDFs kept beside the notes (``data/notes/<folder>/x.pdf``) and their identity.

The file is the PDF; the registry only gives it a number that outlives its path, so that what is
attached to it (highlights, links) survives a rename or a move. It lives in ``library.db`` — not in
the disposable notes index — because the highlights hang on it.

* Moves, renames and the notes trash made by the app go through ``moved``: the row follows the file.
* A row has the lifetime of its file: when the notes trash deletes the file for good, or a PDF has
  been ``missing`` for longer than the notes trash retention (``never`` keeps it), ``forget_under``
  / ``sync`` delete the row and its highlights. Until then a file that comes back gets its row back.
* Changes made outside the app are found by ``sync``: a file at a known path whose size or time
  changed keeps its row (a replaced PDF); a new file that is byte-for-byte a file that disappeared
  takes over that row (an external rename keeps the highlights); a row whose file is gone becomes
  ``missing``. A file that is gone is never a reason to delete a row right away.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

from .vault import PDF_SUFFIX, TRASH, Vault

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS note_pdfs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    sha256 TEXT,
    size INTEGER,
    mtime REAL,
    state TEXT NOT NULL DEFAULT 'present',   -- present | trashed | missing
    since TEXT
);
-- Only a file that is there owns its path: the row of a PDF that vanished may share it with a new one.
CREATE UNIQUE INDEX IF NOT EXISTS idx_note_pdfs_path ON note_pdfs(path) WHERE state = 'present';
"""


def apply_schema(c) -> None:
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            c.execute(stmt)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _info(r) -> dict:
    d = dict(r)
    d["name"] = PurePosixPath(d["path"]).name
    return d


class NotePdfs:
    def __init__(self, db, vault: Vault):
        self.db = db
        self.vault = vault
        self._lock = threading.RLock()
        # seconds a vanished PDF's row (and highlights) is kept, None for ever (the trash retention)
        self.retention: Callable[[], float | None] = lambda: None
        db.add_schema(apply_schema)
        with db.session() as c:
            apply_schema(c)

    # ------------------------------------------------------------------ reads
    def get(self, pdf_id: int) -> dict | None:
        with self.db.session() as c:
            r = c.execute("SELECT * FROM note_pdfs WHERE id = ?", (pdf_id,)).fetchone()
        return _info(r) if r else None

    def by_path(self, rel: str) -> dict | None:
        with self.db.session() as c:
            r = c.execute("SELECT * FROM note_pdfs WHERE path = ? AND state = 'present'", (rel,)).fetchone()
        return _info(r) if r else None

    def listing(self) -> list[dict]:
        """The PDFs that are in the notes folders, for the notes tree."""
        with self.db.session() as c:
            rows = c.execute("SELECT * FROM note_pdfs WHERE state = 'present' ORDER BY path").fetchall()
        return [_info(r) for r in rows]

    def file_of(self, pdf_id: int):
        """The row and the file on disk of a PDF that is in the notes folders; None when it is not
        (unknown, trashed, missing, a link or no longer a regular file)."""
        row = self.get(pdf_id)
        if not row or row["state"] != "present":
            return None
        try:
            p = self.vault.abs(row["path"], suffix=PDF_SUFFIX)
        except Exception:
            return None
        if p.is_symlink() or not p.is_file():
            return None
        return row, p

    # ------------------------------------------------------------------ writes
    def register(self, rel: str, sha256: str | None = None) -> dict:
        """Give a PDF that just appeared in the notes folders its number (the row it has already
        when something was registered at this path)."""
        p = self.vault.root / rel
        st = p.stat()
        with self._lock, self.db.session() as c:
            row = c.execute("SELECT id FROM note_pdfs WHERE path = ? AND state = 'present'", (rel,)).fetchone()
            sha = sha256 or _sha256(p)
            if row:
                c.execute(
                    "UPDATE note_pdfs SET sha256 = ?, size = ?, mtime = ? WHERE id = ?",
                    (sha, st.st_size, st.st_mtime, row["id"]),
                )
                pdf_id = row["id"]
            else:
                lost = [dict(r) for r in c.execute("SELECT * FROM note_pdfs WHERE state = 'missing'")]
                pdf_id = self._take_over(c, rel, sha, st, lost)  # a vanished PDF that came back keeps its row
                if pdf_id is None:
                    pdf_id = c.execute(
                        "INSERT INTO note_pdfs (path, sha256, size, mtime, state, since) "
                        "VALUES (?,?,?,?, 'present', ?)",
                        (rel, sha, st.st_size, st.st_mtime, _now()),
                    ).lastrowid
        return self.get(pdf_id) or {}

    @staticmethod
    def _take_over(c, rel: str, sha: str, st, lost: list[dict]) -> int | None:
        """The row of a vanished PDF that the file now at ``rel`` is: the one at this path (a replacement,
        whatever its content), else one with the same content (found again elsewhere, name first).
        Removes it from ``lost``; None when the file is a new PDF."""
        here = [r for r in lost if r["path"] == rel and r["state"] == "missing"]
        same = sorted(
            (r for r in lost if r["sha256"] == sha),
            key=lambda r: PurePosixPath(r["path"]).name != PurePosixPath(rel).name,
        )
        old = (here or same or [None])[0]
        if old is None:
            return None
        lost.remove(old)
        c.execute(
            "UPDATE note_pdfs SET path = ?, sha256 = ?, state = 'present', size = ?, mtime = ? WHERE id = ?",
            (rel, sha, st.st_size, st.st_mtime, old["id"]),
        )
        return old["id"]

    def moved(self, src: str, dst: str) -> None:
        """A PDF (or a folder with PDFs: ``src`` and ``dst`` are then the folders) was moved by the
        app; ``dst`` inside ``.trash/`` means deleted, a ``src`` there means restored."""
        state = "trashed" if dst.startswith(TRASH + "/") else "present"
        with self._lock, self.db.session() as c:
            rows = c.execute(
                "SELECT id, path FROM note_pdfs WHERE path = ? OR substr(path, 1, ?) = ?",
                (src, len(src) + 1, src + "/"),
            ).fetchall()
            moving = {r["id"] for r in rows}
            for r in rows:
                new = dst + r["path"][len(src) :]
                if state == "present":  # a row of a file that was already gone must not hold the new place
                    for o in c.execute("SELECT id FROM note_pdfs WHERE path = ? AND state = 'present'", (new,)):
                        if o["id"] not in moving:
                            c.execute(
                                "UPDATE note_pdfs SET state = 'missing', since = ? WHERE id = ?", (_now(), o["id"])
                            )
                c.execute("UPDATE note_pdfs SET path = ?, state = ? WHERE id = ?", (new, state, r["id"]))

    def forget_under(self, prefix: str) -> int:
        """The trash deleted these files for good: their rows (and highlights) go too."""
        with self._lock, self.db.session() as c:
            return c.execute("DELETE FROM note_pdfs WHERE substr(path, 1, ?) = ?", (len(prefix), prefix)).rowcount

    def sync(self) -> dict:
        """Bring the registry in line with the files (see the module's text)."""
        counts = {"added": 0, "changed": 0, "adopted": 0, "missing": 0, "forgotten": 0}
        with self._lock, self.db.session() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM note_pdfs WHERE state IN ('present', 'missing')")]
            present = {r["path"]: r for r in rows if r["state"] == "present"}
            on_disk = {rel: st for rel, st in self.vault.iter_files(PDF_SUFFIX)}
            lost = [r for r in rows if r["state"] == "present" and r["path"] not in on_disk]
            lost += [r for r in rows if r["state"] == "missing"]
            for rel, st in on_disk.items():
                row = present.get(rel)
                if row and row["size"] == st.st_size and row["mtime"] == st.st_mtime:
                    continue
                try:
                    sha = _sha256(self.vault.root / rel)
                except OSError:
                    continue
                if row:  # replaced in place: the same PDF for the notes, its highlights stay
                    c.execute(
                        "UPDATE note_pdfs SET sha256 = ?, size = ?, mtime = ? WHERE id = ?",
                        (sha, st.st_size, st.st_mtime, row["id"]),
                    )
                    counts["changed"] += 1
                    continue
                if self._take_over(c, rel, sha, st, lost) is not None:  # a vanished PDF, back (here or elsewhere)
                    counts["adopted"] += 1
                else:
                    c.execute(
                        "INSERT INTO note_pdfs (path, sha256, size, mtime, state, since) "
                        "VALUES (?,?,?,?, 'present', ?)",
                        (rel, sha, st.st_size, st.st_mtime, _now()),
                    )
                    counts["added"] += 1
            for r in lost:
                if r["state"] == "present":
                    c.execute("UPDATE note_pdfs SET state = 'missing', since = ? WHERE id = ?", (_now(), r["id"]))
                    counts["missing"] += 1
            counts["forgotten"] += self._forget_gone(c)
        return counts

    def _forget_gone(self, c) -> int:
        """Delete the rows (and highlights) of files the trash no longer has, and of PDFs missing for
        longer than the retention."""
        gone = [
            r["id"]
            for r in c.execute("SELECT id, path FROM note_pdfs WHERE state = 'trashed'")
            if not (self.vault.root / r["path"]).exists()  # emptied or deleted outside the app
        ]
        secs = self.retention()
        if secs:
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%SZ")
            gone += [
                r["id"] for r in c.execute("SELECT id FROM note_pdfs WHERE state = 'missing' AND since < ?", (cutoff,))
            ]
        for i in gone:
            c.execute("DELETE FROM note_pdfs WHERE id = ?", (i,))
        return len(gone)
