"""Find freely available (open-access) PDFs for items that have none.

Only legal open-access sources are used:
  * Unpaywall      (needs your e-mail, set in Settings)
  * OpenAlex       (open-access locations, incl. repositories and PubMed Central)
  * Semantic Scholar (open-access PDF link, and the arXiv id of published papers)
  * arXiv          (the item's own arXiv id, or one found by the services above)

Every downloaded file is checked before it is proposed: it must really be a PDF
(not a login page) and it must be the right paper (its DOI/arXiv id or title on
the first pages).  Nothing is attached until you accept the proposal.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from rapidfuzz import fuzz

from . import textutil as tu
from .config import WORK_DIR
from .lookups import RateLimiter, UA
from .pdfs import analyze_pdf

log = logging.getLogger(__name__)
STAGING = WORK_DIR / "pdf_staging"
VERSION_RANK = {"publishedVersion": 0, "self": 0, "acceptedVersion": 1, "submittedVersion": 2, None: 3}
VERSION_LABEL = {"publishedVersion": "published version", "acceptedVersion": "accepted manuscript (post-print)",
                 "submittedVersion": "arXiv/preprint version — may differ from the published article",
                 "self": "arXiv version (this item is the preprint)", None: "version not stated"}


class PdfFinder:
    def __init__(self, settings, store, progress=None):
        self.settings = settings
        self.store = store
        self.http = requests.Session()
        mailto = (settings.get("mailto") or "").strip()
        self.mailto = mailto
        self.http.headers["User-Agent"] = f"{UA}{' mailto:' + mailto if mailto else ''}"
        self.api_rl = RateLimiter(3)
        self.s2_rl = RateLimiter(0.9)
        self.arxiv_rl = RateLimiter(1 / 3.1)
        self.dl_rl = RateLimiter(1.5)
        self.max_bytes = int(settings.get("pdf", "max_download_mb") or 80) * 1024 * 1024
        self._publisher = None
        self.last_publisher_url = None
        self.progress = progress

    # ------------------------------------------------------------------ http
    def _json(self, url, rl, params=None, cache_key=None):
        if cache_key:
            v = self.store.cache_get(cache_key)
            if v is not None:
                return None if isinstance(v, dict) and v.get("_notfound") else v
        rl.wait()
        try:
            r = self.http.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            log.info("%s: %s", url, e)
            return None
        if r.status_code == 404:
            if cache_key:
                self.store.cache_set(cache_key, {"_notfound": True})
            return None
        if r.status_code != 200:
            log.info("%s -> HTTP %s", url, r.status_code)
            return None
        try:
            j = r.json()
        except ValueError:
            return None
        if cache_key:
            self.store.cache_set(cache_key, j)
        return j

    # ------------------------------------------------------------------ identification
    def identify(self, it: dict) -> dict:
        """All identifiers of an item, resolving a DOI from PMID/PMCID, ISBN or
        (last resort) a title search when the item has none."""
        from .enrich import item_ids, title_match, first_last
        d = it["data"]
        doi, ax = item_ids(it)
        extra = d.get("extra", "") or ""
        m = re.search(r"^\s*PMID:\s*(\d+)", extra, re.I | re.M)
        pmid = m.group(1) if m else ""
        m = re.search(r"^\s*PMCID:\s*(PMC\d+)", extra, re.I | re.M)
        pmcid = m.group(1).upper() if m else ""
        isbns = [re.sub(r"[\s-]", "", x).upper() for x in tu.normalize_isbn_field(d.get("ISBN", "")).split() if tu.isbn_valid(x)]
        url = d.get("url", "") or ""
        m = re.search(r"https?://[^/]*hal[^/]*/((?:hal|tel|halshs|inria|cea|in2p3|insu|pasteur|inserm)-\d+)", url)
        hal = m.group(1) if m else ""
        how = []
        # PubMed identifiers -> DOI / PMCID (Europe PMC)
        if (pmid or pmcid) and (not doi or not pmcid):
            q = f"PMCID:{pmcid}" if pmcid else f"EXT_ID:{pmid} AND SRC:MED"
            j = self._json("https://www.ebi.ac.uk/europepmc/webservices/rest/search", self.api_rl,
                           params={"query": q, "resultType": "core", "format": "json"}, cache_key="epmc:" + (pmcid or pmid))
            r = ((j or {}).get("resultList") or {}).get("result") or []
            if r:
                doi = doi or (r[0].get("doi") or "")
                pmcid = pmcid or (r[0].get("pmcid") or "")
                if doi:
                    how.append("DOI found through PubMed")
        # ISBN -> DOI (Crossref registers many books and chapters by ISBN)
        if not doi and isbns:
            for isbn in isbns[:2]:
                j = self._json("https://api.crossref.org/works", self.api_rl,
                               params={"filter": f"isbn:{isbn}", "rows": 20, "select": "DOI,title,type",
                                       **({"mailto": self.mailto} if self.mailto else {})}, cache_key="crisbn:" + isbn)
                items = ((j or {}).get("message") or {}).get("items") or []
                best = max(items, key=lambda x: title_match(d.get("title", ""), (x.get("title") or [""])[0]), default=None)
                if best and title_match(d.get("title", ""), (best.get("title") or [""])[0]) >= 90:
                    doi = best["DOI"]
                    how.append("DOI found through the ISBN")
                    break
        # title (+ first author, year) -> OpenAlex work, as a last resort
        oa_work = None
        if not doi and not ax and len(tu.plain(d.get("title", ""))) >= 15:
            title = tu.plain(d.get("title", ""))
            j = self._json("https://api.openalex.org/works", self.api_rl,
                           params={"search": title[:250], "per-page": 5, **({"mailto": self.mailto} if self.mailto else {})},
                           cache_key="oasearch:" + hashlib.sha1(title.lower().encode()).hexdigest())
            y = re.search(r"\d{4}", d.get("date", "") or "")
            fa = first_last(d.get("creators"))
            for w in (j or {}).get("results") or []:
                if title_match(title, w.get("display_name") or w.get("title") or "") < 93:
                    continue
                wy = w.get("publication_year")
                if y and wy and abs(int(y.group(0)) - int(wy)) > 1:
                    continue
                names = [tu.canon((a.get("author") or {}).get("display_name", "")) for a in w.get("authorships") or []]
                if fa and names and not any(n.split(" ")[-1:] == [fa] or fa in n for n in names):
                    continue
                oa_work = w
                doi = tu.clean_doi(w.get("doi") or "")
                how.append("identified by a title search (OpenAlex)")
                break
        return {"doi": doi, "arxiv": ax, "pmid": pmid, "pmcid": pmcid, "isbns": isbns, "hal": hal, "url": url,
                "oa_work": oa_work, "how": how}

    def candidates_for(self, it: dict, ids: dict) -> list[dict]:
        """Candidates from every identifier the item has."""
        cands = self.candidates(ids["doi"], ids["arxiv"])
        seen = {c["url"] for c in cands}
        cfg = self.settings.get("pdf") or {}

        def add(url, source, version=None, license=None):
            if url and url not in seen:
                seen.add(url)
                cands.append({"url": url, "source": source, "version": version, "license": license or "",
                              "host": urlparse(url).hostname or ""})

        # OpenAlex work found by title (no DOI): its open-access locations
        w = ids.get("oa_work")
        if w and not ids["doi"]:
            for loc in [w.get("best_oa_location")] + list(w.get("locations") or []):
                if loc and loc.get("pdf_url") and loc.get("is_oa", True):
                    add(loc["pdf_url"], f"OpenAlex ({((loc.get('source') or {}).get('display_name')) or 'repository'})",
                        loc.get("version"), loc.get("license"))
        # Europe PMC (PubMed Central) open-access full text
        if ids["pmcid"] and cfg.get("find_europepmc", True):
            add(f"https://europepmc.org/articles/{ids['pmcid']}?pdf=render", "Europe PMC", "publishedVersion")
        # open-access books: OAPEN library and Open Library / Internet Archive (public-domain scans)
        for isbn in ids["isbns"][:2]:
            if cfg.get("find_oapen", True):
                j = self._json("https://library.oapen.org/rest/search", self.api_rl,
                               params={"query": isbn, "expand": "bitstreams"}, cache_key="oapen:" + isbn)
                for rec in j if isinstance(j, list) else []:
                    for b in rec.get("bitstreams") or []:
                        if (b.get("mimeType") == "application/pdf" or str(b.get("name", "")).lower().endswith(".pdf")) \
                                and b.get("bundleName", "ORIGINAL") == "ORIGINAL" and b.get("retrieveLink"):
                            add("https://library.oapen.org" + b["retrieveLink"], "OAPEN", "publishedVersion")
            if cfg.get("find_openlibrary", True):
                j = self._json(f"https://openlibrary.org/api/volumes/brief/isbn/{isbn}.json", self.api_rl,
                               cache_key="olib:" + isbn)
                for itm in (j or {}).get("items") or []:
                    m = re.search(r"archive\.org/details/([^/?#]+)", itm.get("itemURL", "") or "")
                    if itm.get("status") == "full access" and m:
                        add(f"https://archive.org/download/{m.group(1)}/{m.group(1)}.pdf",
                            "Internet Archive (Open Library)", "publishedVersion", "public domain / full access")
        # the publisher's own PDF through your institution's access (VPN / campus network)
        if cfg.get("institutional") and ids["doi"] and not ids["doi"].lower().startswith("10.48550/"):
            add("publisher:" + ids["doi"], "the publisher (your institutional access)", "publishedVersion")
        # HAL open archive and a direct PDF link stored in the item
        if ids["hal"]:
            add(f"https://hal.science/{ids['hal']}/document", "HAL", None)
        if re.search(r"\.pdf(?:$|[?#])", ids["url"] or "", re.I):
            add(ids["url"], "the item's URL", None)
        cands.sort(key=lambda c: (VERSION_RANK.get(c["version"], 3), c["url"].startswith("publisher:"), "arxiv.org" in c["host"]))
        return cands

    # ------------------------------------------------------------------ candidates
    def candidates(self, doi: str, arxiv: str) -> list[dict]:
        """Open-access PDF URLs for a work, best first."""
        cands: list[dict] = []
        seen = set()
        cfg = self.settings.get("pdf") or {}

        def add(url, source, version=None, license=None, host=None):
            if not url or url in seen:
                return
            seen.add(url)
            cands.append({"url": url, "source": source, "version": version, "license": license or "",
                          "host": host or urlparse(url.removeprefix("landing:")).hostname or ""})

        found_arxiv = arxiv
        if doi and self.mailto and cfg.get("find_unpaywall", True):
            j = self._json(f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}", self.api_rl,
                           params={"email": self.mailto}, cache_key="unpaywall:" + doi.lower())
            if j:
                locs = [j.get("best_oa_location")] + list(j.get("oa_locations") or [])
                for loc in locs:
                    if loc and loc.get("url_for_pdf"):
                        add(loc["url_for_pdf"], "Unpaywall", loc.get("version"), loc.get("license"), None)
                    elif loc and loc.get("url") and "doi.org" not in loc["url"]:
                        # repository landing page without a direct link: the PDF link is read from the page
                        add("landing:" + loc["url"], f"Unpaywall ({loc.get('host_type') or 'repository'})",
                            loc.get("version"), loc.get("license"), None)
        if doi and cfg.get("find_openalex", True):
            params = {"mailto": self.mailto} if self.mailto else None
            j = self._json(f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}", self.api_rl, params=params,
                           cache_key="openalex:" + doi.lower())
            if j:
                locs = [j.get("best_oa_location")] + list(j.get("locations") or [])
                for loc in locs:
                    if not loc:
                        continue
                    lu = loc.get("landing_page_url") or ""
                    hal = re.search(r"https?://hal[^/]*/((?:hal|tel|halshs|inria|cea|in2p3)-\d+)", lu)
                    if hal:  # HAL records often have the full text even when not flagged open access
                        add(f"https://hal.science/{hal.group(1)}/document", "HAL", None)
                    if not loc.get("is_oa", True):
                        continue
                    src = ((loc.get("source") or {}).get("display_name")) or "OpenAlex"
                    if loc.get("pdf_url"):
                        add(loc.get("pdf_url"), f"OpenAlex ({src})", loc.get("version"), loc.get("license"))
                    elif lu and "doi.org" not in lu:
                        add("landing:" + lu, f"OpenAlex ({src})", loc.get("version"), loc.get("license"))
                    if not found_arxiv and "arxiv.org" in lu:
                        found_arxiv = tu.arxiv_from(lu)
        if doi and cfg.get("find_semanticscholar", True):
            j = self._json(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='/')}", self.s2_rl,
                           params={"fields": "openAccessPdf,externalIds"}, cache_key="s2:" + doi.lower())
            if j:
                oa = j.get("openAccessPdf") or {}
                add(oa.get("url"), "Semantic Scholar", None, None)
                if not found_arxiv:
                    found_arxiv = ((j.get("externalIds") or {}).get("ArXiv") or "")
        if found_arxiv and cfg.get("find_arxiv", True):
            same_work = not doi or doi.lower().startswith("10.48550/")
            add(f"https://arxiv.org/pdf/{found_arxiv}", "arXiv", "self" if same_work else "submittedVersion",
                "arXiv", "arxiv.org")
        # arXiv links reported by the services are preprints of a published work
        for c in cands:
            if "arxiv.org" in c["host"] and doi and not doi.lower().startswith("10.48550/"):
                c["version"] = c["version"] or "submittedVersion"
        cands.sort(key=lambda c: (VERSION_RANK.get(c["version"], 3), "arxiv.org" in c["host"]))
        return cands

    # ------------------------------------------------------------------ download + verify
    def pdf_link_from_landing(self, page_url: str) -> str | None:
        """Repositories (DSpace, Invenio, HAL, EPrints, OJS, publishers...) expose the full
        text as <meta name="citation_pdf_url"> for Google Scholar."""
        self.dl_rl.wait()
        try:
            r = self.http.get(page_url, timeout=30, headers={"Accept": "text/html"})
        except requests.RequestException:
            return None
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", ""):
            return None
        html = r.text[:400000]
        for pat in (r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url',
                    r'<link[^>]+type=["\']application/pdf["\'][^>]+href=["\']([^"\']+)'):
            m = re.search(pat, html, re.I)
            if m:
                from urllib.parse import urljoin
                return urljoin(r.url, m.group(1).replace("&amp;", "&"))
        return None

    @property
    def publisher(self):
        if self._publisher is None:
            from .publisher import PublisherFetcher
            self._publisher = PublisherFetcher(self.settings)
            self._publisher.progress = self.progress
        return self._publisher

    def close(self):
        if self._publisher is not None:
            self._publisher.close()

    def download(self, url: str, dest: Path) -> tuple[bool, str]:
        if url.startswith("publisher:"):
            ok, msg, how = self.publisher.fetch(url[len("publisher:"):], dest)
            self.last_publisher_url = msg if ok else None
            if not ok and self.publisher.blocked:
                return False, "BLOCKED: " + msg
            return ok, ("" if ok else msg)
        if url.startswith("landing:"):
            link = self.pdf_link_from_landing(url[len("landing:"):])
            if not link:
                return False, "the repository page has no PDF link"
            url = link
        host = urlparse(url).hostname or ""
        if "arxiv.org" in host:
            self.arxiv_rl.wait()
        else:
            self.dl_rl.wait()
        try:
            with self.http.get(url, stream=True, timeout=60, allow_redirects=True,
                               headers={"Accept": "application/pdf,*/*;q=0.5"}) as r:
                if r.status_code != 200:
                    return False, f"HTTP {r.status_code}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                t0 = time.monotonic()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        size += len(chunk)
                        if size > self.max_bytes:
                            return False, "file too large"
                        if time.monotonic() - t0 > 180:
                            return False, "download too slow (gave up after 3 minutes)"
                        f.write(chunk)
        except requests.RequestException as e:
            return False, str(e)[:200]
        return True, ""

    @staticmethod
    def verify(path: Path, data: dict, doi: str, arxiv: str, isbns: list | None = None) -> tuple[bool, float, str]:
        """Is this the right paper? Returns (ok, score 0-100, evidence)."""
        book = data.get("itemType") in ("book", "bookSection", "thesis", "report")
        return PdfFinder.verify_analysis(analyze_pdf(path, max_pages=6 if book else 2), data, doi, arxiv, isbns)

    @staticmethod
    def verify_analysis(a: dict, data: dict, doi: str, arxiv: str, isbns: list | None = None) -> tuple[bool, float, str]:
        """verify() on an already analysed PDF (so one file can be checked against many items)."""
        if not a.get("is_pdf"):
            return False, 0, a.get("error") or "not a PDF (probably a login or cookie page)"
        text = a.get("text") or ""
        if not text.strip():
            return True, 60, "scanned PDF without text layer: could not check the title"
        ct = tu.canon(text)
        low = text.lower()
        if doi and doi.lower() in low.replace(" ", ""):
            return True, 100, "the item's DOI is printed in the PDF"
        if arxiv and arxiv in text:
            return True, 95, "the arXiv id is printed in the PDF"
        digits = re.sub(r"[^0-9Xx]", "", text)
        for isbn in isbns or []:
            if isbn.upper() in digits.upper():
                return True, 98, "the ISBN is printed in the PDF"
        title = tu.canon(tu.plain(data.get("title", "")))
        score = fuzz.partial_ratio(title[:150], ct) if title else 0
        found = tu.find_doi(text)
        if found and doi and found.lower() != doi.lower() and score < 90:
            return False, score, f"the PDF belongs to another work (DOI {found})"
        if score >= 85:
            last = next((c.get("lastName") for c in data.get("creators", []) if c.get("lastName")), "")
            ev = f"title found on the first page ({score:.0f}%)"
            if last and tu.canon(tu.plain(last)) in ct:
                ev += ", first author too"
                score = min(100, score + 5)
            return True, score, ev
        return False, score, f"title not found on the first pages ({score:.0f}%)"

    def find_for_item(self, it: dict, doi: str | None = None, arxiv: str | None = None, cancelled=None) -> dict:
        """Try the candidates in order; return the first verified PDF (or not found + the attempts)."""
        ids = self.identify(it)
        if doi:
            ids["doi"] = doi
        if arxiv:
            ids["arxiv"] = arxiv
        if not any(ids[k] for k in ("doi", "arxiv", "pmcid", "isbns", "hal")) and not ids.get("oa_work") \
                and not re.search(r"\.pdf", ids["url"] or "", re.I):
            return {"found": False, "reason": "no DOI, arXiv id, ISBN or PubMed id, and a title search found nothing",
                    "attempts": []}
        attempts = []
        for c in self.candidates_for(it, ids)[:10]:
            if cancelled and cancelled():
                break
            tmp = STAGING / it["key"] / (hashlib.sha1(c["url"].encode()).hexdigest()[:12] + ".pdf")
            ok, why = self.download(c["url"], tmp)
            if not ok:
                attempts.append({**c, "result": why})
                tmp.unlink(missing_ok=True)
                continue
            good, score, ev = self.verify(tmp, it["data"], ids["doi"], ids["arxiv"], ids["isbns"])
            if not good:
                attempts.append({**c, "result": ev})
                tmp.unlink(missing_ok=True)
                continue
            if ids["how"]:
                ev += "; " + "; ".join(ids["how"])
            if c["url"].startswith("publisher:"):
                c = {**c, "url": self.last_publisher_url or c["url"], "host": urlparse(self.last_publisher_url or "").hostname or "publisher"}
            a = analyze_pdf(tmp, max_pages=1)
            return {"found": True, "path": str(tmp), "url": c["url"], "source": c["source"], "version": c["version"],
                    "version_label": VERSION_LABEL.get(c["version"], "version not stated"), "license": c["license"],
                    "host": c["host"], "score": round(score), "evidence": ev, "pages": a.get("pages"),
                    "size": tmp.stat().st_size, "attempts": attempts}
        pub = next((a for a in attempts if a["url"].startswith("publisher:")), None)
        if pub and str(pub.get("result", "")).startswith("BLOCKED"):
            reason = ("blocked by the publisher's captcha — not a missing PDF: use 'Get with my own browser' "
                      "on the PDFs page")
            return {"found": False, "attempts": attempts, "reason": reason, "needs_browser": True}
        elif pub:
            reason = "no open-access copy, and the publisher did not deliver the PDF (no subscription?)"
        elif attempts or ids["doi"]:
            reason = "no open-access copy found" + ("" if (self.settings.get("pdf") or {}).get("institutional")
                                                   else " (institutional access is off in Settings)")
        else:
            reason = "no open-access source knows this item"
        return {"found": False, "attempts": attempts, "reason": reason}
