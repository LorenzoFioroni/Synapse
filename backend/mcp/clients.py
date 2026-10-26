"""The name of the AI app on the other end of an MCP connection, as the user knows it.

Every MCP client names itself in ``initialize`` (``clientInfo``: a machine name, sometimes a
title). Those names are not meant for people ("claude-ai", "codex-mcp-client"), so the apps people
use are recognised here; any other client is shown by its title, else by its name tidied up.
A client that gives no name stays unnamed (None): the app then says "an AI app".
"""

from __future__ import annotations

import re
from typing import Any

# (pattern in the client's name or title, the name to show), first match wins
KNOWN = (
    ("claude", "Claude"),
    ("codex", "Codex"),
    ("cursor", "Cursor"),
    ("gemini", "Gemini"),
    ("copilot", "GitHub Copilot"),
    ("visual studio code", "VS Code"),
    ("vscode", "VS Code"),
    ("windsurf", "Windsurf"),
    ("zed", "Zed"),
    ("lm studio", "LM Studio"),
    ("chatgpt", "ChatGPT"),
    ("openai", "ChatGPT"),
)


def client_label(info: Any) -> str | None:
    """``{"name": "codex-mcp-client", "title": "Codex", …}`` → "Codex"."""
    if not isinstance(info, dict):
        return None
    name = str(info.get("name") or "").strip()
    title = str(info.get("title") or "").strip()
    both = f"{name} {title}".lower()
    for pattern, label in KNOWN:
        if re.search(rf"(?<![a-z]){re.escape(pattern)}", both):
            return label
    if title:
        return title[:60]
    tidy = re.sub(r"[-_ ]*(mcp)?[-_ ]*(client)?$", "", name, flags=re.I)
    tidy = " ".join(w[:1].upper() + w[1:] for w in re.split(r"[-_\s]+", tidy) if w)
    return tidy[:60] or None
