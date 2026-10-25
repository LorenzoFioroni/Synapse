"""Citation Counts: how often each paper is cited, as the per-item field ``citations``.

Synapse hands the refresher the library's papers in batches of 50: those without a count, then
those whose count is more than a week old. Papers with a DOI are asked of OpenAlex first (one
request for the batch), preprints with only an arXiv id of Semantic Scholar first (it counts a
preprint and its journal version as one paper; OpenAlex often keeps them apart and gives the
preprint few citations). Each service is the other's fallback. ``plugin.http`` adds the API keys
and contact address from Settings → Online Services, as Synapse's own lookups use them.
"""

from __future__ import annotations

from backend.plugins import HttpError, Plugin

OPENALEX = "https://api.openalex.org/works"
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
OPENALEX_OR = 50  # values OpenAlex accepts in one "a|b|c" filter


def activate(plugin: Plugin) -> None:
    def openalex(papers: list[dict], by_arxiv: bool = False) -> dict:
        """{key: (count, OpenAlex page)} for the papers OpenAlex knows, asked by DOI (or by the
        arXiv DOI, 10.48550/arxiv.<id>)."""
        want: dict[str, list[dict]] = {}
        for p in papers:
            doi = f"10.48550/arxiv.{p['arxiv'].lower()}" if by_arxiv else _doi(p)
            if doi and "|" not in doi and "," not in doi:  # would break the filter's syntax
                want.setdefault(doi, []).append(p)
        out = {}
        dois = list(want)
        for i in range(0, len(dois), OPENALEX_OR):
            chunk = dois[i : i + OPENALEX_OR]
            r = plugin.http.get(
                OPENALEX,
                params={"filter": "doi:" + "|".join(chunk), "per-page": OPENALEX_OR, "select": "id,doi,cited_by_count"},
            )
            for w in r.json().get("results") or []:
                doi = (w.get("doi") or "").lower().removeprefix("https://doi.org/")
                if w.get("cited_by_count") is None:
                    continue
                for p in want.get(doi, []):
                    out[p["key"]] = (int(w["cited_by_count"]), w.get("id"))
        return out

    def semantic_scholar(papers: list[dict]) -> dict:
        """{key: (count, Semantic Scholar page)}, by arXiv id when there is one, else by DOI."""
        want: dict[str, list[dict]] = {}
        for p in papers:
            ident = f"ARXIV:{p['arxiv']}" if p["arxiv"] else f"DOI:{_doi(p)}" if _doi(p) else None
            if ident:
                want.setdefault(ident, []).append(p)
        if not want:
            return {}
        ids = list(want)
        r = plugin.http.post(S2_BATCH, params={"fields": "citationCount,url"}, json={"ids": ids})
        out = {}
        for ident, w in zip(ids, r.json(), strict=False):  # one answer per id, null when unknown
            if w and w.get("citationCount") is not None:
                for p in want[ident]:
                    out[p["key"]] = (int(w["citationCount"]), w.get("url"))
        return out

    def counts(papers: list[dict]) -> dict:
        use_s2 = plugin.settings["semantic_scholar"]
        articles = [p for p in papers if _doi(p)]
        preprints = [p for p in papers if not _doi(p) and p["arxiv"]]
        if not use_s2:
            articles, preprints = articles + preprints, []
        out: dict = {}
        failed: list[HttpError] = []

        def ask(fn, items, **kw) -> None:
            todo = [p for p in items if p["key"] not in out]
            if not todo:
                return
            try:
                out.update(fn(todo, **kw))
            except HttpError as e:
                plugin.log.warning("%s: %s", fn.__name__, e)
                failed.append(e)

        ask(openalex, [p for p in articles if _doi(p)])
        if use_s2:
            ask(semantic_scholar, preprints + articles)  # preprints first, then what OpenAlex missed
        ask(openalex, [p for p in articles + preprints if p["arxiv"]], by_arxiv=True)
        if failed and not out:
            raise failed[0]  # nothing could be asked: the whole batch again later
        # a paper no service found is "not found"; while one of them was down, ask again later
        later = plugin.fields.LATER
        return {**({p["key"]: later for p in papers} if failed else {}), **out}

    plugin.fields.declare(
        "citations",
        type="int",
        label="Citations",
        description="How often the paper is cited",
        source="OpenAlex / Semantic Scholar",
        refresh="7d",
        refresher=counts,
    )


def _doi(p: dict) -> str | None:
    """The paper's DOI, unless it is only arXiv's own (10.48550/arXiv.…): those go by the arXiv id."""
    doi = (p.get("doi") or "").lower()
    return doi if doi and not doi.startswith("10.48550/") else None
