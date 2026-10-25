"""How well arXiv's HTML versions give reference lists (backend/references/arxiv_html.py), for a
day's new papers: how many have an HTML version, how many references each, and how many of those
carry a DOI, an arXiv id or a title (what matching to the library needs). It also asks Semantic
Scholar, to compare with what it knows on announcement day.

    python tools/arxiv_refs.py quant-ph                  today's new papers in quant-ph (at most 20)
    python tools/arxiv_refs.py quant-ph --limit 50
    python tools/arxiv_refs.py 2610.01234 2610.04321     these papers

Needs the network; waits between requests to arXiv as arXiv asks.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.library.identifiers import norm_arxiv  # noqa: E402
from backend.online.http import arxiv_wait  # noqa: E402
from backend.references import arxiv_html  # noqa: E402
from backend.references.sources import semantic_scholar  # noqa: E402

RSS = "https://rss.arxiv.org/rss/{}"


def todays(category: str, limit: int) -> list[str]:
    arxiv_wait()
    r = requests.get(RSS.format(category), timeout=30, headers={"User-Agent": "Synapse (reference coverage check)"})
    r.raise_for_status()
    # new submissions only (not cross-lists or replacements)
    ids = re.findall(r"arXiv:(\d{4}\.\d{4,5})v\d+\s+Announce Type:\s*new", r.text)
    return list(dict.fromkeys(ids))[:limit]


def pct(n: int, total: int) -> str:
    return f"{100 * n / total:.0f}%" if total else "-"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="+", help="an arXiv category, or arXiv ids")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    ids = [norm_arxiv(w) for w in args.what]
    if not all(ids):
        ids = todays(args.what[0], args.limit)
        print(f"{len(ids)} new papers in {args.what[0]}\n")
    rows = []
    print(f"{'paper':<14}{'html refs':>10}{'doi':>6}{'arXiv':>7}{'title':>7}{'S2 refs':>9}")
    for aid in ids:
        assert aid
        try:
            refs = arxiv_html.fetch(aid)
        except Exception as e:  # noqa: BLE001 - reported, the next paper is tried
            print(f"{aid:<14}  error: {e}")
            continue
        try:
            s2 = len(semantic_scholar(None, aid))
        except Exception:  # noqa: BLE001
            s2 = -1
        doi = sum(1 for r in refs if r["doi"])
        arx = sum(1 for r in refs if r["arxiv_id"])
        title = sum(1 for r in refs if r["title"])
        rows.append((len(refs), doi, arx, title, s2))
        print(f"{aid:<14}{len(refs):>10}{doi:>6}{arx:>7}{title:>7}{s2 if s2 >= 0 else '?':>9}")
    if not rows:
        return
    total = sum(r[0] for r in rows)
    with_html = sum(1 for r in rows if r[0])
    usable = sum(r[1] + r[2] for r in rows)
    print(
        f"\n{with_html}/{len(rows)} papers have references in their HTML version ({total} references): "
        f"{pct(sum(r[1] for r in rows), total)} with a DOI, {pct(sum(r[2] for r in rows), total)} with an arXiv id, "
        f"{pct(sum(r[3] for r in rows), total)} with a title; {usable} identifiers in all."
    )
    known = [r for r in rows if r[4] >= 0]
    print(f"Semantic Scholar knows references for {sum(1 for r in known if r[4])}/{len(known)} of them today.")


if __name__ == "__main__":
    main()
