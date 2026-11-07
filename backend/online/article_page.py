"""Where an article's web page says its PDF is.

Publishers name the PDF of an article page in a ``citation_pdf_url`` meta tag (the tags Google
Scholar indexes, so nearly every publisher has them). Some DOIs first lead to a page that moves on
with a meta refresh instead of an HTTP redirect (Elsevier's linkinghub.elsevier.com does).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

_REFRESH_URL = re.compile(r"url\s*=\s*['\"]?([^'\"]+)", re.IGNORECASE)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pdf: str | None = None
        self.refresh: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "").strip() for k, v in attrs}
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name == "citation_pdf_url" and a.get("content") and not self.pdf:
                self.pdf = a["content"]
            elif (a.get("http-equiv") or "").lower() == "refresh" and not self.refresh:
                m = _REFRESH_URL.search(a.get("content") or "")
                if m:
                    self.refresh = m.group(1).strip()
        elif tag == "link" and not self.pdf and (a.get("type") or "").lower() == "application/pdf":
            if "alternate" in (a.get("rel") or "").lower().split() and a.get("href"):
                self.pdf = a["href"]


def links(page: bytes, base: str) -> tuple[str | None, str | None]:
    """(the PDF the page names, the page a meta refresh moves on to), as absolute http(s)
    addresses; None for what the page does not have."""
    parser = _Links()
    try:
        parser.feed(page.decode("utf-8", errors="replace"))
        parser.close()
    except Exception:  # a page too broken to read names nothing
        pass

    def absolute(u: str | None) -> str | None:
        if not u:
            return None
        u = urljoin(base, u)
        return u if u.startswith(("https://", "http://")) else None

    refresh = parser.refresh
    if refresh and "%3a%2f%2f" in refresh.lower() and "://" not in refresh:
        refresh = unquote(refresh)  # a whole address quoted into the attribute
    return absolute(parser.pdf), absolute(refresh)
