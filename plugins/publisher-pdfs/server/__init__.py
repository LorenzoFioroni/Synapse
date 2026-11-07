"""Publisher PDFs: Find Available PDF tries the publisher's copy first (comes with Synapse, on by
default).

Publishers give PDFs to readers whose network has a subscription (a university's network or VPN)
without a login, so the version of record is there to take; elsewhere the attempt ends at a login
page and Synapse moves on to the open-access sources (order 10). A contributed plugin: it uses
only the public plugin API (backend.plugins).
"""

from __future__ import annotations

from backend.plugins import Plugin

from .finder import candidates


def activate(plugin: Plugin) -> None:
    @plugin.pdf_source("publisher", title="The publisher's PDF, by DOI", order=5)
    def publisher(paper: dict) -> list[str]:
        return candidates(plugin.http, paper.get("doi"), plugin.services.contact_email())
