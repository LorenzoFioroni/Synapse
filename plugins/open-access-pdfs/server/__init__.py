"""Open-Access PDFs: the PDF sources of Find Available PDF (comes with Synapse, on by default).

Two sources: one by identifier (arXiv for a preprint, Unpaywall's best open-access copy for a
DOI, then arXiv for a published paper that has an arXiv id) and one by title (Unpaywall's search
and arXiv's), which Synapse asks only when the first found nothing for a paper with an arXiv id.
Synapse itself downloads the candidates and checks each is the paper. A contributed plugin: it
uses only the public plugin API (backend.plugins).
"""

from __future__ import annotations

from backend.plugins import Plugin

from .finder import by_identifier, by_title


def activate(plugin: Plugin) -> None:
    @plugin.pdf_source("identifiers", title="Unpaywall and arXiv, by DOI and arXiv id", order=10)
    def identifiers(paper: dict) -> list[str]:
        return by_identifier(plugin.http, paper.get("doi"), paper.get("arxiv"), plugin.services.contact_email())

    @plugin.pdf_source("titles", title="Unpaywall and arXiv, by title", by_title=True, order=10)
    def titles(paper: dict) -> list[str]:
        return by_title(plugin.http, paper.get("title"), plugin.services.contact_email())
