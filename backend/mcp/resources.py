"""Library content as MCP resources: notes, papers and the folder overview, addressed by URI.

    synapse://collections                    folders, tags, smart folders (what list_collections returns)
    synapse://note/<path>[?section=Heading]  a note's Markdown, or one section of it
    synapse://paper/<key>                    a paper's metadata, abstract and outline

The notes are listed (up to ``MAX_LISTED_NOTES``); papers are too many to list, so a client reaches them
through the template. Everything here is also reachable through the tools: resources let an AI app attach
it by reference (an ``@`` mention) instead of the model calling a tool.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .protocol import ResourceNotFound, ToolError
from .tools import LibraryTools
from .tools.common import MAX_NOTE_CHARS

SCHEME = "synapse"
MAX_LISTED_NOTES = 200

TEMPLATES = [
    {
        "uriTemplate": f"{SCHEME}://paper/{{key}}",
        "name": "paper",
        "title": "A paper",
        "description": "Metadata, abstract and outline of a library item, by citation key.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": f"{SCHEME}://note/{{path}}{{?section}}",
        "name": "note",
        "title": "A note",
        "description": "A Markdown note by path; with section, only the text under that heading.",
        "mimeType": "text/markdown",
    },
]


def _json(value) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False)


class Resources:
    def __init__(self, tools: LibraryTools):
        self.t = tools

    def listed(self) -> list[dict]:
        out = [
            {
                "uri": f"{SCHEME}://collections",
                "name": "collections",
                "title": "Folders, tags and notes folders",
                "description": "The library's folders with item counts, tags, smart folders and overall counts.",
                "mimeType": "application/json",
            }
        ]
        self.t.refresh()
        notes = sorted(self.t.s.notes.list(), key=lambda n: n["path"].casefold())
        for n in notes[:MAX_LISTED_NOTES]:
            out.append(
                {
                    "uri": f"{SCHEME}://note/{quote(n['path'], safe='/')}",
                    "name": n["path"],
                    "title": n.get("title") or n["path"],
                    "mimeType": "text/markdown",
                }
            )
        return out

    def read(self, uri: str) -> list[dict]:
        parts = urlsplit(uri)
        if parts.scheme != SCHEME:
            raise ResourceNotFound(f"Unknown resource: {uri}")
        kind, rest = parts.netloc, unquote(parts.path.lstrip("/"))
        self.t.refresh()
        try:
            if kind == "collections" and not rest:
                return [self._content(uri, "application/json", _json(self.t.list_collections()))]
            if kind == "note" and rest:
                section = (parse_qs(parts.query).get("section") or [""])[0]
                return [self._content(uri, "text/markdown", self._note(rest, section))]
            if kind == "paper" and rest:
                return [self._content(uri, "application/json", self._paper(rest))]
        except ToolError as e:
            raise ResourceNotFound(str(e)) from None
        raise ResourceNotFound(f"Unknown resource: {uri}")

    @staticmethod
    def _content(uri: str, mime: str, text: str) -> dict:
        return {"uri": uri, "mimeType": mime, "text": text}

    def _note(self, ref: str, section: str) -> str:
        t = self.t
        path = t._note_path(ref)
        got = t.s.note_service.get(path)
        text = got["text"]
        if section:
            first, last = t._section_lines((got["info"] or {}).get("headings") or [], section)
            text = "\n".join(text.replace("\r\n", "\n").split("\n")[first - 1 : last])
        if len(text) > MAX_NOTE_CHARS:
            text = text[:MAX_NOTE_CHARS] + f"\n\n[… cut at {MAX_NOTE_CHARS} characters; ask for a section]"
        return text

    def _paper(self, key: str) -> str:
        t = self.t
        got = t.get_paper(key)
        paper = got[0] if isinstance(got, list) else got  # with a paper note, the metadata is the first part
        out = {"paper": paper}
        try:
            out["outline"] = t.get_paper_outline(key)
        except ToolError:
            pass  # no PDF: the metadata still stands
        return _json(out)
