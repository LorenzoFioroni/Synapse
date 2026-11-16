"""Tools for notes: get_note, write_note, edit_note, undo_note_edit."""

from __future__ import annotations

import json
import re
import time

from ...library.agent import current_agent
from ...notes import frontmatter, parser
from ...notes.service import now_iso, ulid
from ...notes.vault import Conflict, NoteExists, slugify
from ..notecheck import NoteChecker
from ..protocol import Parts, ToolError
from .common import AI_FOLDER, MAX_NOTE_CHARS, NOTE_READ_CHARS, drop_empty
from .images import ImageToolsMixin


class NoteToolsMixin(ImageToolsMixin):  # uses _note_images
    """get_note, write_note, edit_note, undo_note_edit."""

    def get_note(self, note: str, section: str = "", full: bool = False, lines: str = ""):
        path = self._note_path(note)
        got = self.s.note_service.get(path)
        info = got["info"] or {}
        ranged = bool(section or lines)
        too_long = not ranged and not full and len(got["text"]) > NOTE_READ_CHARS
        links = self.s.note_service.links(path)
        if section:
            first, last = self._section_lines(info.get("headings") or [], section)
        elif lines:
            first, last = self._line_range(lines)
        else:
            first, last = 1, None

        def inside(line: int) -> bool:
            return line >= first and (last is None or line <= last)

        links = {**links, "outgoing": [l for l in links["outgoing"] if inside(l["line"])]}
        links["cited"] = (
            [
                c
                for c in links["cited"]
                if any(l["kind"] == "cite" and l["target"] == c["key"] for l in links["outgoing"])
            ]
            if ranged
            else links["cited"]
        )
        items = self._items()
        keys = self._by_key(items)
        cited = []
        for c in links["cited"]:
            p = keys.get(c["key"])
            # Where, not what: the text around a citation is the text this tool returns.
            mine = [l for l in links["outgoing"] if l["kind"] == "cite" and l["target"] == c["key"]]
            at = sorted({l["line"] for l in mine})
            pages = sorted({l["page"] for l in mine if l.get("page")})
            other = sorted({str(l["locator"]) for l in mine if l.get("locator") and not l.get("page")})
            cited.append(
                drop_empty(
                    {
                        "key": c["key"],
                        # The rest (authors, DOI, venue) is in get_paper; a long note cites hundreds of papers.
                        "paper": drop_empty(
                            {"title": p.get("title"), "year": p.get("year"), "has_pdf": bool(self._pdfs(p))}
                        )
                        if p
                        else None,
                        "not_in_library": None if p else True,
                        "lines": at,
                        "pages": pages,
                        "locators": other,
                    }
                )
            )
        grouped: dict[tuple, dict] = {}  # one entry per target, with every line that links to it
        for l in links["outgoing"]:
            if l["kind"] not in ("note", "embed"):
                continue
            key = (l["target"], l.get("resolved"), l["kind"] == "embed")
            if key not in grouped:
                grouped[key] = drop_empty(
                    {
                        "target": l["target"],
                        "resolves_to": l.get("resolved"),
                        "broken": None if l.get("resolved") else True,
                        "embed": l["kind"] == "embed" or None,
                    }
                )
                grouped[key]["lines"] = []
            grouped[key]["lines"].append(l["line"])
        notes_out = list(grouped.values())
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
                "outline": self._outline(info.get("headings") or [], got["text"] if too_long else None),
                "section": section or (f"lines {first}-{last or 'end'}" if lines else None),
                "links_to_notes": notes_out,
                "linked_from": backlinks,
                "images": [i for i in self._note_images(got["text"], caption_once=True) if inside(i["line"])] or None,
                "tasks": self._note_tasks(links["outgoing"]) or None,
                "math_macros": self._math_macros() or None,
            }
        )
        summary["cited_papers"] = cited  # kept when empty: a missing key reads as "not computed"
        summary["cited_papers_scope"] = f"{'only that part' if ranged else 'the whole note'}: " + (
            f"{len(cited)} library papers or keys" if cited else "it cites no papers"
        )
        if summary.get("images"):
            summary["images_hint"] = "view_image(note=…) shows these images (paths are inside the notes folder)."
        text = got["text"]
        if too_long:
            summary["too_long"] = (
                f"The note has {len(text)} characters, too many to read at once: its text is not included. "
                "Read the part you need with section='<heading>' (the outline shows each section's size) or "
                "lines='120-300', or give full=true for all of it. cited_papers and images below cover the "
                "whole note."
            )
            return summary
        if ranged:
            text = "\n".join(text.replace("\r\n", "\n").split("\n")[first - 1 : last])
        if len(text) > MAX_NOTE_CHARS:
            text = text[:MAX_NOTE_CHARS] + f"\n\n[… note cut at {MAX_NOTE_CHARS} characters of {len(got['text'])}]"
        what = (
            f"Section '{section}' of {path} (lines {first}-{last or 'end'})"
            if section
            else f"Lines {first}-{last or 'end'} of {path}"
            if lines
            else f"Text of {path}"
        )
        return Parts([summary, f"{what}:\n\n{text}"])

    @staticmethod
    def _shift_lines(message: str, k: int) -> str:
        """ "line 14: …" counted in the saved note → counted in the text the caller sent (k lines fewer)."""
        m = re.match(r"(lines? )([\d, …]+)(:.*)", message, re.S)
        if not m or not k:
            return message
        nums = re.sub(r"\d+", lambda n: str(max(int(n.group()) - k, 1)), m.group(2))
        return m.group(1) + nums + m.group(3)

    @staticmethod
    def _outline(headings: list[dict], text: str | None) -> list[str]:
        """The headings as "## Title"; with ``text``, each with its lines and size."""
        if text is None:
            return [("#" * h["level"]) + " " + h["text"] for h in headings]
        lines = text.replace("\r\n", "\n").split("\n")
        out = []
        for i, h in enumerate(headings):
            end = next((x["line"] - 1 for x in headings[i + 1 :] if x["level"] <= h["level"]), len(lines))
            size = sum(len(l) + 1 for l in lines[h["line"] - 1 : end])
            out.append(f"{'#' * h['level']} {h['text']} (lines {h['line']}-{end}, {size} characters)")
        return out

    @staticmethod
    def _line_range(spec: str) -> tuple[int, int | None]:
        """ "120-300" → (120, 300); "120-" → (120, None); "120" → (120, 120)."""
        m = re.fullmatch(r"\s*(\d+)\s*(?:(-|–)\s*(\d*))?\s*", str(spec))
        if not m or int(m.group(1)) < 1:
            raise ToolError(f"lines='{spec}' is not a line range: write e.g. '120-300' (line numbers from 1).")
        first = int(m.group(1))
        last = (int(m.group(3)) if m.group(3) else None) if m.group(2) else first
        if last is not None and last < first:
            raise ToolError(f"lines='{spec}': the end is before the start.")
        return first, last

    @staticmethod
    def _section_lines(headings: list[dict], section: str) -> tuple[int, int | None]:
        """First and last line (1-based) of the section under a heading, up to the next heading of the
        same or a higher level. ``section`` is the heading text, with or without its ``#``s."""
        want = re.sub(r"^#+\s*", "", section).strip().casefold()
        found = [h for h in headings if h["text"].strip().casefold() == want] or [
            h for h in headings if want and want in h["text"].casefold()
        ]
        if len(found) != 1:
            outline = "; ".join(("#" * h["level"]) + " " + h["text"] for h in headings) or "the note has no headings"
            why = "is ambiguous" if found else "matches no heading"
            raise ToolError(f"'{section}' {why}. Headings: {outline}")
        h = found[0]
        later = [x["line"] for x in headings if x["line"] > h["line"] and x["level"] <= h["level"]]
        return h["line"], (min(later) - 1 if later else None)

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
        meta: dict[str, object] = {"id": ulid(), "title": title, "type": "note", "author": "ai"}
        if current_agent():
            meta["agent"] = current_agent()  # which AI app wrote it
        meta["created"] = now_iso()
        if tags:
            meta["tags"] = [str(t) for t in tags]
        content = frontmatter.set_fields(body, meta)
        checker = NoteChecker(self)
        content, errors, warnings = checker.check(content, path)
        first = next((l for l in text.split("\n") if l.strip()), "")
        added = content[: max(content.find(first), 0)].count("\n")  # front matter and title the tool put before it
        errors, warnings = ([self._shift_lines(m, added) for m in ms] for ms in (errors, warnings))
        if errors:
            raise ToolError(
                "Nothing was saved: the note has problems that would show as broken in the app. "
                "Fix them and call write_note again.\n- "
                + "\n- ".join(errors)
                + ("\nAlso: " + " ".join(warnings) if warnings else "")
            )
        try:
            self.s.note_service.save(path, content, None, by="ai", summary=f"Wrote “{title}”")
        except NoteExists as e:
            raise ToolError(f"A note already exists at {path}; try another title.") from e
        parsed = parser.parse(content, fallback_title=title)
        same = [n["path"] for n in self.s.notes.list() if n["path"] != path and (n.get("title") or "") == title]
        if same:
            warnings = [
                *warnings,
                f"{'Another note has' if len(same) == 1 else f'{len(same)} other notes have'} this title "
                f"({', '.join(same[:5])}). Refer to this one by its path, {path}, not by title.",
            ]
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
            summary_text = " ".join(str(summary or "").split()) or f"Edited by {current_agent() or 'an AI app'}"
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
                "note's list of AI edits); undo_note_edit reverts it if the user asks you to.",
            }
        )

    def undo_note_edit(self, note: str):
        path = self._note_path(note)
        ns = self.s.note_service
        cur, h = self.s.vault.read(path)
        if not ns.history.enabled:
            return self._undo_legacy(path, cur, h)
        e = next((x for x in ns.history_of(path, limit=300)["entries"] if x.get("by") == "ai"), None)
        if not e:
            raise ToolError(f"No AI app has edited {path}.")
        try:
            new = ns.history.reverted(e["id"], path, cur)
        except ValueError as err:
            raise ToolError(
                f"{path} was changed after the last AI edit ({e['summary']}) in the same places, so undoing it "
                "would also remove those changes. Nothing was undone. The user can restore any earlier version "
                "in the app (the note's history), or read the note with get_note and edit_note it."
            ) from err
        if new == cur:
            raise ToolError(
                f"The last AI edit of {path} ({e['summary']}) is no longer in the note; nothing was undone."
            )
        try:
            res = ns.edit(path, new, h, by="ai", summary=f"Undid: {e['summary']}")
        except Conflict as err:
            raise ToolError(f"{path} changed just now; nothing was undone.") from err
        return drop_empty(
            {
                "restored": path,
                "undid": e["summary"],
                "kept_later_edits": True if e.get("after_hash") != h else None,
                "version": res["version"],
                "changes": self._diff(cur, new),
            }
        )

    def _undo_legacy(self, path: str, cur: str, h: str):
        """Without Git: the AI changes kept as JSON files (only the latest, and only while the
        note is exactly what it left)."""
        hist = self.s.note_service.history.legacy
        e = next((x for x in hist.list(path) if x.get("by") == "ai"), None)
        if not e:
            raise ToolError(f"No AI app has edited {path} (or its earlier versions were cleaned up).")
        full = hist.get(e["id"])
        if not full or h != e["after_hash"]:
            raise ToolError(
                f"{path} was changed after the last AI edit ({e['summary']}), so undoing it would also "
                "remove those changes. Nothing was undone. The user can restore any earlier version in "
                "the app (the note's list of AI edits), or read the note with get_note and edit_note it."
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
