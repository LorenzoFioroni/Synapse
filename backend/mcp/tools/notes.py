"""Tools for notes: get_note, write_note, edit_note, undo_note_edit."""

from __future__ import annotations

import json
import re
import time

from ...notes import frontmatter, parser
from ...notes.service import now_iso, ulid
from ...notes.vault import Conflict, NoteExists, slugify
from ..notecheck import NoteChecker
from ..protocol import Parts, ToolError
from .common import AI_FOLDER, MAX_NOTE_CHARS, drop_empty
from .images import ImageToolsMixin


class NoteToolsMixin(ImageToolsMixin):  # uses _note_images
    """get_note, write_note, edit_note, undo_note_edit."""

    def get_note(self, note: str):
        path = self._note_path(note)
        got = self.s.note_service.get(path)
        info = got["info"] or {}
        links = self.s.note_service.links(path)
        items = self._items()
        keys = self._by_key(items)
        cited = []
        for c in links["cited"]:
            p = keys.get(c["key"])
            cites = [
                drop_empty(
                    {"line": l["line"], "locator": l.get("locator"), "page": l.get("page"), "context": l.get("context")}
                )
                for l in links["outgoing"]
                if l["kind"] == "cite" and l["target"] == c["key"]
            ]
            cited.append(
                drop_empty(
                    {
                        "key": c["key"],
                        "paper": self._brief(p) if p else None,
                        "not_in_library": None if p else True,
                        "citations": cites,
                    }
                )
            )
        notes_out = [
            drop_empty(
                {
                    "target": l["target"],
                    "resolves_to": l.get("resolved"),
                    "broken": None if l.get("resolved") else True,
                    "line": l["line"],
                    "embed": l["kind"] == "embed" or None,
                }
            )
            for l in links["outgoing"]
            if l["kind"] in ("note", "embed")
        ]
        backlinks = [
            drop_empty(
                {
                    "path": b["path"],
                    "title": b.get("source_title"),
                    "link": self._note_link(b["path"], b.get("source_title")),
                    "line": b["line"],
                    "context": b.get("context"),
                }
            )
            for b in links["backlinks"]
        ]
        paper_key = info.get("paper")
        paper = keys.get(paper_key) if paper_key else None
        mtime = self.s.vault.abs(path).stat().st_mtime
        summary = drop_empty(
            {
                "path": path,
                "link": self._note_link(path, info.get("title")),
                "title": info.get("title"),
                "type": info.get("type"),
                "author": info.get("author"),
                "tags": info.get("tags"),
                "created": info.get("created"),
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
                "about_paper": self._brief(paper) if paper else info.get("paper"),
                "outline": [("#" * h["level"]) + " " + h["text"] for h in info.get("headings") or []],
                "cited_papers": cited,
                "links_to_notes": notes_out,
                "linked_from": backlinks,
                "images": self._note_images(got["text"]) or None,
                "tasks": self._note_tasks(links["outgoing"]) or None,
                "math_macros": self._math_macros() or None,
            }
        )
        if summary.get("images"):
            summary["images_hint"] = "view_image(note=…) shows these images (paths are inside the notes folder)."
        text = got["text"]
        if len(text) > MAX_NOTE_CHARS:
            text = text[:MAX_NOTE_CHARS] + f"\n\n[… note cut at {MAX_NOTE_CHARS} characters of {len(got['text'])}]"
        return Parts([summary, f"Text of {path}:\n\n{text}"])

    def _note_tasks(self, outgoing: list[dict]) -> list[dict]:
        """The tasks the note shows ({{task:ID}} lines), with what the line displays."""
        refs = [l for l in outgoing if l["kind"] == "task"]
        known = self.s.task_store.get_many({l["target"] for l in refs})
        out = []
        for l in refs:
            t = known.get(l["target"])
            out.append(
                drop_empty(
                    {
                        "line": l["line"],
                        "id": l["target"],
                        "title": t["title"] if t else None,
                        "status": t["status"] if t else None,
                        "due": t.get("due") if t else None,
                        "missing": None if t else True,
                        "in_trash": True if t and t.get("deleted_at") else None,
                    }
                )
            )
        return out

    def _math_macros(self) -> str:
        """The user's own math commands (Settings → Math macros), which every note may use."""
        return str(self.s.settings.get("notes_math_macros") or "").strip()

    def write_note(self, title: str, text: str, folder: str = "", tags: list | None = None):
        title = " ".join(str(title).split())
        sub = "/".join(
            slugify(x) for x in re.split(r"[/\\]+", folder or "") if x.strip() and x.strip() not in (".", "..")
        )
        base = f"{AI_FOLDER}/{sub}" if sub else AI_FOLDER
        name = slugify(title, fallback="AI note")
        path = f"{base}/{name}.md"
        n = 2
        while self.s.vault.exists(path):
            path = f"{base}/{name} {n}.md"
            n += 1
        body = text if text.endswith("\n") else text + "\n"
        _, fm_body, _ = frontmatter.split(body)
        if not re.search(r"^\s*#\s", fm_body):
            body = f"# {title}\n\n" + body
        meta: dict[str, object] = {"id": ulid(), "title": title, "type": "note", "author": "ai", "created": now_iso()}
        if tags:
            meta["tags"] = [str(t) for t in tags]
        content = frontmatter.set_fields(body, meta)
        checker = NoteChecker(self)
        content, errors, warnings = checker.check(content, path)
        if errors:
            raise ToolError(
                "Nothing was saved: the note has problems that would show as broken in the app. "
                "Fix them and call write_note again.\n- "
                + "\n- ".join(errors)
                + ("\nAlso: " + " ".join(warnings) if warnings else "")
            )
        try:
            self.s.note_service.save(path, content, None)
        except NoteExists as e:
            raise ToolError(f"A note already exists at {path}; try another title.") from e
        parsed = parser.parse(content, fallback_title=title)
        return drop_empty(
            {
                "saved": path,
                "link": self._note_link(path, title),
                "citations": len([l for l in parsed.links if l.kind == "cite"]),
                "links_fixed": checker.fixes or None,
                "warnings": warnings or None,
            }
        )

    # ------------------------------------------------------------------ editing notes
    def _check_change(self, old: str, new: str, path: str) -> tuple[str, list[str], list[str], list[str]]:
        """Check an edited note. Only problems the edit adds count: an existing note may have
        broken links or quotes the user left there. Returns (text, errors, warnings, fixes)."""
        strip = lambda e: re.sub(r"^lines? [\d, …]+:\s*", "", e)
        before_checker = NoteChecker(self)
        _, before, _ = before_checker.check(old, path)
        checker = NoteChecker(self)
        text, errors, warnings = checker.check(new, path)
        left: dict[str, int] = {}
        for e in before:
            left[strip(e)] = left.get(strip(e), 0) + 1
        added = []
        for e in errors:
            k = strip(e)
            if left.get(k):
                left[k] -= 1
            else:
                added.append(e)
        return text, added, warnings, checker.fixes

    @staticmethod
    def _diff(old: str, new: str, limit: int = 4000) -> str:
        import difflib

        lines = list(difflib.unified_diff(old.split("\n"), new.split("\n"), "before", "after", n=1, lineterm=""))[2:]
        out = "\n".join(lines)
        return out if len(out) <= limit else out[:limit] + "\n[… diff cut]"

    def edit_note(
        self,
        note: str,
        edits: list | None = None,
        append: str | None = None,
        text: str | None = None,
        summary: str | None = None,
    ):
        path = self._note_path(note)
        if text is not None and (edits or append):
            raise ToolError("Give either text (the whole new note) or edits and/or append, not both.")
        if text is None and not edits and not append:
            raise ToolError("Nothing to change: give edits=[{old, new}], append or text.")
        flat = []
        for e in edits or []:  # some clients send the list (or its items) as JSON text
            if isinstance(e, str):
                try:
                    e = json.loads(e)
                except ValueError as err:
                    raise ToolError('edits must be a list of {"old": …, "new": …} objects.') from err
            flat.extend(e if isinstance(e, list) else [e])
        edits = flat
        for _ in range(3):  # the user may be saving this note right now: re-read and apply again
            old, h = self.s.vault.read(path)
            new = old
            if text is not None:
                body = text if text.endswith("\n") else text + "\n"
                header, old_body, _ = frontmatter.split(old)
                new_header, _, _ = frontmatter.split(body)
                if header is not None and new_header is None:  # keep the note's front matter
                    body = old[: len(old) - len(old_body)] + body
                new = body
            for k, e in enumerate(edits or [], 1):
                if not isinstance(e, dict) or not isinstance(e.get("old"), str) or not isinstance(e.get("new"), str):
                    raise ToolError(f'Edit {k}: give {{"old": exact text now in the note, "new": its replacement}}.')
                if not e["old"]:
                    raise ToolError(
                        f"Edit {k}: old is empty. To add at the end use append; to add elsewhere, "
                        "include a neighbouring line in old and new."
                    )
                count = new.count(e["old"])
                if count != 1:
                    raise ToolError(
                        f"Edit {k}: the old text occurs {count} times in {path}"
                        + (
                            " (it must be copied exactly from get_note, with its line breaks)."
                            if not count
                            else "; include more of the surrounding text so it occurs once."
                        )
                        + " Nothing was saved."
                    )
                new = new.replace(e["old"], e["new"], 1)
            if append:
                nl = "\r\n" if "\r\n" in new else "\n"
                new = new.rstrip("\r\n") + nl + nl + append.strip("\n").replace("\n", nl) + nl
            if new == old:
                raise ToolError("The edit changes nothing; the note was left as it is.")
            if len(new) > 4 * MAX_NOTE_CHARS:
                raise ToolError("The note would become too long.")
            new, errors, warnings, fixes = self._check_change(old, new, path)
            if errors:
                raise ToolError(
                    "Nothing was saved: the change adds problems that would show as broken in the app. "
                    "Fix them and call edit_note again.\n- "
                    + "\n- ".join(errors)
                    + ("\nAlso: " + " ".join(warnings) if warnings else "")
                )
            summary_text = " ".join(str(summary or "").split()) or "Edited by Claude"
            try:
                res = self.s.note_service.edit(path, new, h, by="ai", summary=summary_text)
                break
            except Conflict:
                continue
        else:
            raise ToolError(
                f"{path} kept changing while saving (the user is probably typing in it). Try again shortly."
            )
        return drop_empty(
            {
                "saved": path,
                "link": self._note_link(path),
                "version": res["version"],
                "changes": self._diff(old, new),
                "links_fixed": fixes or None,
                "warnings": warnings or None,
                "undo": "The user sees the change in the app and can undo it there (Cmd+Z in the open note, or the "
                "note's Claude edits list); undo_note_edit reverts it if the user asks you to.",
            }
        )

    def undo_note_edit(self, note: str):
        path = self._note_path(note)
        e = next((x for x in self.s.note_service.history.list(path) if x.get("by") == "ai"), None)
        if not e:
            raise ToolError(f"Claude has not edited {path} (or its earlier versions were cleaned up).")
        full = self.s.note_service.history.get(e["id"])
        cur, h = self.s.vault.read(path)
        if h != e["after_hash"]:
            raise ToolError(
                f"{path} was changed after Claude's last edit ({e['summary']}), so undoing it would also "
                "remove those changes. Nothing was undone. The user can restore any earlier version in "
                "the app (the note's Claude edits list), or read the note with get_note and edit_note it."
            )
        try:
            res = self.s.note_service.edit(path, full["before"], h, by="ai", summary=f"Undid: {e['summary']}")
        except Conflict as err:
            raise ToolError(f"{path} changed just now; nothing was undone.") from err
        return {
            "restored": path,
            "undid": e["summary"],
            "version": res["version"],
            "changes": self._diff(cur, full["before"]),
        }
