"""The text around the cursor → an Ollama request, and the model's answer → the suggestion shown.

Pure functions, so they are tested without a model.
"""

from __future__ import annotations

import re

from .boundaries import trim
from .context import prefix_with_context, tail

PREFIX_CHARS = 4000  # text before the cursor sent to the model (whole lines, from the end)
SUFFIX_CHARS = 1500  # text after it, for models that fill in the middle
MAX_TOKENS = {"line": 64, "paragraph": 220}
MAX_LINES = 12  # of a paragraph suggestion

# Special tokens some models print when their template does not swallow them
_SPECIAL = re.compile(r"<\|?(?:fim_|file_sep|endoftext|im_end|im_start|eot|EOT|end_of_turn|repo_name)[^>]*>|</s>")


def _head(text: str, n: int) -> str:
    """The first ``n`` characters, ending at a line end when there is one."""
    if len(text) <= n:
        return text
    cut = text.rfind("\n", 0, n)
    return text[: cut + 1] if cut > 0 else text[:n]


def _tail(text: str, n: int) -> str:
    """The last ``n`` characters, starting at a line start when there is one."""
    return tail(text, n)


def current_line(prefix: str) -> str:
    """The cursor's line up to the cursor."""
    return prefix[prefix.rfind("\n") + 1 :]


def build(
    model: str, prefix: str, suffix: str, *, fim: bool, thinking: bool, length: str, context: object = None
) -> dict:
    """The /api/generate payload (without stream and keep_alive)."""
    before, after = prefix_with_context(prefix, context, PREFIX_CHARS), _head(suffix, SUFFIX_CHARS)
    stop = []
    if current_line(before).strip():
        # a suggestion goes to the end of this line, or of this paragraph
        stop = ["\n"] if length == "line" else ["\n\n"]
    options = {"temperature": 0.2, "top_p": 0.9, "num_predict": MAX_TOKENS.get(length, 64), "stop": stop}
    if fim:
        # An empty suffix would make Ollama fall back to the model's chat template, which answers
        # like a chat instead of continuing the note; a line end keeps it filling in.
        payload: dict = {"model": model, "prompt": before, "suffix": after or "\n", "options": options}
        if thinking:
            payload["think"] = False
        return payload
    # A model that cannot fill in the middle continues the text, without its chat template.
    return {"model": model, "prompt": before, "raw": True, "options": options}


def clean(text: str, prefix: str, suffix: str, length: str, fim: bool = True) -> str:
    """What to show from the model's answer: one line or one paragraph, without what already
    follows the cursor. Empty when there is nothing worth showing."""
    text = text.replace("\r", "")
    m = _SPECIAL.search(text)
    if m:
        text = text[: m.start()]
    line = current_line(prefix)
    if not line.strip():
        text = text.lstrip("\n")  # on an empty line: start on it, not below it
    elif prefix.endswith((" ", "\t")):
        text = re.sub(r"^[ \t]+", "", text)  # no double space

    rest = suffix.split("\n", 1)[0]
    if length == "line" or rest.strip():
        # one line (and only one when there is text after the cursor on this line)
        text = text.split("\n", 1)[0]
    else:
        lines = re.split(r"\n[ \t]*\n", text, maxsplit=1)[0].split("\n")
        text = "\n".join(lines[:MAX_LINES])

    # FIM models also write past a supplied closer, even though they saw it in their context.
    text = trim(text, prefix, suffix, fim=fim)
    # ... or start writing the next lines again
    following = suffix.lstrip()[:20]
    if len(following) >= 8:
        at = text.find(following)
        if at >= 0:
            text = text[:at]

    text = text.rstrip()
    return text if text.strip() else ""
