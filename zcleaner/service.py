"""Application services: background jobs (scan, rules, PDF check, online
lookups, commit, roll-back) orchestrating the modules."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import duplicates, enrich, pdfs, rules
from . import textutil as tu
from .commit import Committer, _same, rollback, summarize
from .config import DB_FILE, WORK_DIR, Secrets, Settings
from .lookups import Lookups, NetworkDown, NotFound
from .schema import Schema
from .store import Store, jd, now
from .zotero import Zotero, ZoteroError

log = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str, params: dict):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.params = params
        self.status = "running"
        self.message = "starting …"
        self.n = 0
        self.total = None
        self.result = None
        self.error = None
        self.started = now()
        self.finished = None
        self._cancel = threading.Event()
        self.log: list[str] = []

    def progress(self, msg=None, n=None, total=None):
        if msg:
            self.message = msg
            if not self.log or self.log[-1] != msg:
                self.log.append(msg)
                self.log = self.log[-50:]
        if n is not None:
            self.n = n
        if total is not None:
            self.total = total

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self):
        if self._cancel.is_set():
            raise Cancelled()

    def as_dict(self):
        return {k: getattr(self, k) for k in ("id", "kind", "params", "status", "message", "n", "total", "result",
                                              "error", "started", "finished", "log")}


class App:
    def __init__(self):
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        self.settings = Settings()
        self.secrets = Secrets()
        self.store = Store(DB_FILE)
        n = self.store.recover_interrupted()
        if n:
            log.info("%d proposals that failed for technical reasons are 'accepted' again", n)
        self.purge_type_flips()
        self.store.purge_trashed()
        rn = self.store.restore_rename_decisions()
        if rn:
            log.info("%d accepted file renames carried over to their updated proposals", rn)
        noop = self.store.resolve_noop_edits()
        if noop:
            log.info("%d proposals edited back to the current value are now 'rejected'", len(noop))
        self.schema = Schema(WORK_DIR)
        self.schema.load()  # from cache if present
        self.zot = Zotero(self.settings, self.secrets)
        self.lookups = Lookups(self.settings, self.store)
        self.jobs: dict[str, Job] = {}
        self.current: Job | None = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ jobs
    def start(self, kind: str, params: dict | None = None) -> Job:
        fn = getattr(self, f"job_{kind}", None)
        if not fn:
            raise ValueError(f"unknown job {kind}")
        with self.lock:
            if self.current and self.current.status == "running":
                raise RuntimeError(f"Another task is running ({self.current.kind}). Wait for it or cancel it.")
            job = Job(kind, params or {})
            self.jobs[job.id] = job
            self.current = job

        def run():
            try:
                job.result = fn(job, **job.params)
                job.status = "done"
                job.message = "finished"
            except Cancelled:
                job.status = "cancelled"
                job.message = "cancelled"
            except Exception as e:
                log.error("job %s failed: %s", kind, traceback.format_exc())
                job.status = "failed"
                job.error = str(e)
                job.message = str(e)
            finally:
                job.finished = now()
        threading.Thread(target=run, daemon=True, name=f"job-{kind}").start()
        return job

    def cancel(self, job_id):
        j = self.jobs.get(job_id)
        if j:
            j._cancel.set()

    def purge_type_flips(self) -> int:
        """Remove still-open item-type proposals that repeat a type change you
        rejected (together with the fields that depend on them)."""
        hist = self.store.type_history()
        n = 0
        with self.store.conn() as c:
            rows = c.execute("SELECT id, item_key, new, group_id FROM proposals WHERE kind='itemType' "
                             "AND status IN ('pending','accepted','superseded','stale')").fetchall()
            for r in rows:
                h = hist.get(r["item_key"])
                target = (json.loads(r["new"]) or {}).get("itemType")
                publishing = (r["group_id"] or "").startswith("pub-")
                if not h or target not in h["rejected"]:
                    continue
                c.execute("DELETE FROM proposals WHERE id=?", (r["id"],))
                if r["group_id"]:
                    c.execute("DELETE FROM proposals WHERE item_key=? AND group_id=? AND status IN ('pending','accepted','superseded','stale')",
                              (r["item_key"], r["group_id"]))
                n += 1
        if n:
            log.info("removed %d item-type proposals that would flip a decided type back", n)
        return n

    # ------------------------------------------------------------------ helpers
    def ensure_schema(self):
        if not self.schema.loaded:
            self.schema.load(self.settings.get("zotero_api"))

    def context(self, items=None):
        self.ensure_schema()
        def arxiv_info(ax):
            v = self.store.cache_get("arxiv:" + ax) if ax else None
            return None if not v or v.get("_notfound") else v
        items = items if items is not None else self.store.items()
        bases = self.compute_bases(items)
        return rules.Context(self.settings, self.schema, items, type_history=self.store.type_history(),
                             arxiv_info=arxiv_info, bases=bases, user_fixed=self._user_fixed)

    # rules whose output is a pure function of the field's own previous value
    PIPELINE_RULES = {"whitespace", "markup", "latex", "latex_escape", "title_caps", "title_case", "title_period",
                      "case_fix", "recompute", "pages", "issn_isbn", "url", "language", "extra", "creators", "initials"}

    def compute_bases(self, items) -> dict:
        """For every field this tool changed automatically, the value it had before
        those automatic changes (its 'base').

        Local rules are then always computed from the base, never from their own
        earlier output: the target of a field is f(base), a single well-defined
        value, so repeated runs converge (applying f(base) and re-running yields
        f(base) again). When a rule improves, f_new(base) differs and a genuine
        correction is proposed. The chain stops at anything that is not a pure
        re-formatting of the same field: a value you typed or edited, data from
        Crossref/arXiv, values moved or filled from other fields, or a change
        made in Zotero after the tool wrote the field."""
        by_key = {i["key"]: i for i in items}
        self._user_fixed = set()
        chains: dict[tuple, list] = {}
        for p in self.store.proposals("status='applied' AND kind IN ('field','creators')", order="id"):
            chains.setdefault((p["item_key"], p["field"]), []).append(p)
        bases = {}
        for (key, field), steps in chains.items():
            it = by_key.get(key)
            if not it:
                continue
            cur = it["data"].get(field, [] if field == "creators" else "")
            last = steps[-1]
            if _same(last["value"], cur) and (last["edited"] is not None or last["source"] == "manual"):
                # the current value is one you typed yourself: rules must not argue with it
                self._user_fixed.add((key, field))
                continue
            base = cur
            for p in reversed(steps):
                if not _same(p["value"], base):
                    break  # changed outside this chain (e.g. in Zotero): that value is the base
                pure = (p["source"] == "rules" and p["edited"] is None and p["old"] not in ("", None, [])
                        and set(p["rules"] or []) <= self.PIPELINE_RULES
                        and not (field in ("DOI", "extra") and "doi" in (p["rules"] or [])))
                if not pure or (p["note"] or "").startswith("corrects an earlier case change"):
                    break  # (a correction made by the previous version: its result is the base)
                if p.get("base") is not None:
                    base = p["base"]  # recorded starting point: no need to walk further
                    break
                base = p["old"]
            if not _same(base, cur):
                bases[(key, field)] = base
        return bases

    # ------------------------------------------------------------------ tags
    def job_reassign_tags(self, job: Job, curated_tags: list[str], threshold: float = 0.5):
        from .tags import reassign_tags
        import json
        job.progress("Matching papers to tags...")
        items = list(self.store.items())
        result = reassign_tags(items, curated_tags, threshold=threshold, progress=job.progress)
        
        from .store import fingerprint, jd
        with self.store.conn() as c:
            c.execute("DELETE FROM proposals WHERE source='SemanticTagging' AND status='pending'")
            
            for key, matched_tags in result.items():
                it = self.store.item(key)
                if not it: continue
                d = it.get("data", {})
                if isinstance(d, str):
                    d = json.loads(d)
                old_tags = [t["tag"] for t in d.get("tags", [])]
                old_tags_lower = {t.lower() for t in old_tags}
                
                added = False
                new_tags_list = list(d.get("tags", []))
                for tag in matched_tags:
                    if tag.lower() not in old_tags_lower:
                        new_tags_list.append({"tag": tag})
                        old_tags_lower.add(tag.lower())
                        added = True
                        
                if added:
                    fp = fingerprint(key, "tags", "tags", new_tags_list)
                    c.execute("INSERT INTO proposals (item_key, kind, field, old, new, source, confidence, status, fingerprint, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (key, "tags", "tags", jd(old_tags), jd(new_tags_list), "SemanticTagging", 0.9, "pending", fp, now(), now()))
        job.progress("Done")
        return result

    def job_create_smart_folders(self, job: Job, tags: list[str]):
        job.progress("Creating Smart Folders...")
        for tag in tags:
            self.zot.create_saved_search(f"Topic: {tag}", [tag])
        job.progress("Done")
        return {"created": len(tags)}

    # ------------------------------------------------------------------ scan
    def job_scan(self, job: Job, full: bool = False, analyse: bool = True):
        job.progress("Connecting to Zotero …")
        info = self.zot.ping()
        self.ensure_schema()
        lib_version = self.store.meta_get("library_version")
        incremental_ok = not (full or lib_version is None or self.store.meta_get("server_id") != info["server_id"])
        if incremental_ok:
            try:
                deleted = self.zot.deleted_since(lib_version)
            except ZoteroError:
                incremental_ok = False  # endpoint unavailable: fall back to a full read
        if not incremental_ok:
            job.progress("Reading the whole library …")
            items, v = self.zot.fetch_items(progress=lambda n, t: job.progress(f"Read {n} of {t or '?'} items", n, t),
                                            cancelled=job.cancelled)
            self.store.clear_items()
            self.store.upsert_items(items)
            changed = len(items)
        else:
            job.progress("Reading changes since the last scan …")
            items, v = self.zot.fetch_items(since=lib_version, progress=lambda n, t: job.progress(f"Read {n} changed items", n, t),
                                            cancelled=job.cancelled)
            self.store.upsert_items(items)
            if deleted:
                self.store.delete_items(deleted)
            changed = len(items) + len(deleted)
        self.store.meta_set("library_version", v)
        self.store.meta_set("server_id", info["server_id"])
        self.store.meta_set("last_scan", now())
        res = {"changed": changed, "library_version": v}
        if analyse:
            res.update(self.job_analyse(job))
        return res

    def job_analyse(self, job: Job):
        """Re-run local rules + duplicate detection on the snapshot."""
        job.progress("Analysing metadata …")
        all_items = self.store.items()
        ctx = self.context(all_items)
        props, issues = [], []
        for n, it in enumerate(ctx.regular):
            if n % 500 == 0:
                job.progress(f"Checking item {n} of {len(ctx.regular)}", n, len(ctx.regular))
                job.check()
            p, i = rules.item_proposals(ctx, it)
            props += p
            issues += i
        st = self.store.add_proposals(props, ("rules",))
        self.store.replace_issues(issues, ("rules",))
        job.progress("Looking for duplicates …")
        groups = duplicates.find_duplicates(all_items, int(self.settings.get("duplicates", "threshold") or 90), job.cancelled)
        self.store.replace_dup_groups(groups)
        self._mark_stale(all_items)
        self.store.purge_trashed()
        return {"proposals": st, "issues": len(issues), "duplicate_groups": len(groups)}

    def _mark_stale(self, items):
        """Accepted/pending proposals whose 'old' no longer matches the snapshot."""
        by_key = {i["key"]: i for i in items}
        for p in self.store.proposals("status IN ('pending','accepted') AND kind IN ('field','creators')"):
            it = by_key.get(p["item_key"])
            if not it:
                continue
            cur = it["data"].get(p["field"], "" if p["kind"] == "field" else [])
            if not _same(cur, p["old"]):
                if _same(cur, p["value"]):
                    self.store.update_proposal(p["id"], status="applied", message="already in Zotero")
                else:
                    self.store.update_proposal(p["id"], status="stale", message="the field changed in Zotero since this was proposed")

    # ------------------------------------------------------------------ PDFs
    def job_pdfs(self, job: Job):
        if not self.store.count_items()["n"]:
            self.job_scan(job, analyse=False)
        job.progress("Checking PDF attachments …")
        issues, renames, data_dir = pdfs.scan(self.settings, self.store, self.zot,
                                              progress=lambda n, t: job.progress(f"Checked {n} of {t} PDFs", n, t),
                                              cancelled=job.cancelled)
        job.check()
        self.store.replace_issues(issues, ("pdf",))
        st = self.store.add_proposals(renames, ("pdf",))
        self.store.purge_trashed()
        counts = {}
        for i in issues:
            counts[i["code"]] = counts.get(i["code"], 0) + 1
        return {"issues": counts, "renames": st, "data_dir": data_dir}

    # ------------------------------------------------------------------ online lookups
    def job_lookups(self, job: Job, keys: list | None = None, only_problems: bool = False, refresh: bool = False):
        ctx = self.context()
        items = ctx.regular
        if keys:
            ks = set(keys)
            items = [i for i in items if i["key"] in ks]
        if only_problems:
            flagged = {r["item_key"] for r in self.store.issues("status='open'")} | \
                      {r["item_key"] for r in self.store.proposals("status='pending'")}
            items = [i for i in items if i["key"] in flagged]
        cfg = self.settings.get("lookups")
        by_doi, by_ax, to_search = [], {}, []
        for it in items:
            doi, ax = enrich.item_ids(it)
            if doi and not doi.lower().startswith("10.48550/"):
                by_doi.append((it, doi))
            elif ax:
                by_ax.setdefault(ax, []).append(it)
            elif doi:
                by_doi.append((it, doi))
            elif cfg.get("search_missing_doi") and it["itemType"] in enrich.SCHOLARLY and len(tu.plain(it["data"].get("title", ""))) >= 20:
                to_search.append(it)
        props, issues, done = [], [], set()
        total = len(by_doi) + len(by_ax) + len(to_search)
        counter = {"n": 0, "errors": 0, "notfound": 0}
        lock = threading.Lock()

        def tick(msg):
            with lock:
                counter["n"] += 1
                job.progress(msg, counter["n"], total)

        def do_doi(it, doi):
            if job.cancelled():
                return [], []
            rec = None
            try:
                if cfg.get("crossref", True):
                    rec = self.lookups.crossref(doi, refresh)
            except NotFound:
                rec = None
            if rec is None and cfg.get("doi_content_negotiation", True):
                try:
                    rec = self.lookups.doi_csl(doi, refresh)
                except NotFound:
                    rec = None
            tick(f"Looked up {doi}")
            if rec is None:
                with lock:
                    counter["notfound"] += 1
                return [], [{"item_key": it["key"], "code": "doi_not_found", "severity": "warning",
                             "message": f"DOI {doi} is not registered (Crossref/DataCite)", "details": {"doi": doi},
                             "source": "lookup"}]
            return enrich.compare(ctx, it, rec, via="crossref")

        def do_search(it):
            if job.cancelled():
                return [], []
            d = it["data"]
            fa = next((c.get("lastName") or c.get("name") for c in d.get("creators", []) if c.get("lastName") or c.get("name")), "")
            y = re.search(r"\d{4}", d.get("date", "") or "")
            cands = self.lookups.crossref_search(d.get("title", ""), fa or "", y.group(0) if y else "")
            tick(f"Searched “{tu.plain(d.get('title', ''))[:50]}”")
            best, score = None, 0
            for c in cands:
                s = enrich.search_score(it, c)
                if s > score:
                    best, score = c, s
            if not best or score < float(cfg.get("min_match_score") or 93):
                return [], []
            p, i = enrich.compare(ctx, it, best, via="crossref", matched_by_search=score)
            return p, i

        workers = 4
        network_error = None
        self.lookups.net_failures = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(do_doi, it, doi): it["key"] for it, doi in by_doi}
            futs.update({ex.submit(do_search, it): it["key"] for it in to_search})
            for f in as_completed(futs):
                try:
                    p, i = f.result()
                    props += p
                    issues += i
                    done.add(futs[f])
                except NetworkDown as e:
                    network_error = str(e)
                    job._cancel.set()
                except Exception as e:
                    counter["errors"] += 1
                    log.warning("lookup failed for %s: %s", futs[f], e)
                if job.cancelled():
                    ex.shutdown(wait=False, cancel_futures=True)
                    break
        if network_error:
            # keep what we have, then report
            self.store.add_proposals(props, ("crossref", "doi.org", "arxiv"), item_keys=sorted(done))
            self.store.replace_issues(issues, ("lookup",), item_keys=sorted(done))
            raise RuntimeError(network_error)
        job.check()
        # arXiv in batches
        if by_ax and cfg.get("arxiv", True):
            ids = list(by_ax)
            for k in range(0, len(ids), 100):
                job.check()
                chunk = ids[k:k + 100]
                try:
                    recs = self.lookups.arxiv_batch(chunk, refresh)
                except NetworkDown:
                    raise
                except Exception as e:
                    counter["errors"] += 1
                    log.warning("arXiv batch failed: %s", e)
                    continue
                for ax in chunk:
                    for it in by_ax[ax]:
                        tick(f"arXiv {ax}")
                        done.add(it["key"])
                        rec = recs.get(ax)
                        if rec:
                            p, i = enrich.compare(ctx, it, rec, via="arxiv")
                            props += p
                            issues += i
        # published versions of preprints: turn the finding into one grouped, reviewable fix
        for iss in [i for i in issues if i["code"] == "published_version"]:
            it = next((x for x in items if x["key"] == iss["item_key"]), None)
            new_doi = tu.clean_doi(iss["details"]["doi"])
            doi_prop = {"item_key": it["key"], "kind": "field", "field": "DOI", "old": it["data"].get("DOI", ""),
                        "new": new_doi, "rules": [], "source": "crossref", "confidence": 0.8,
                        "note": "DOI of the published version", "group_id": f"pub-{it['key']}"}
            if self.store.is_dismissed_proposal(doi_prop):
                issues.remove(iss)  # you chose to keep this item as it is
                continue
            try:
                rec = self.lookups.crossref(new_doi)
            except Exception:
                # keep the finding visible, since no fix could be built
                iss["message"] = (f"A published version exists (DOI {new_doi}), but its metadata could not be fetched "
                                  "— run the lookups again later, or set the DOI yourself.")
                continue
            target = rec.get("itemType") or "journalArticle"
            p, _ = enrich.compare(ctx, it, rec, via="crossref", matched_by_search=95, group=f"pub-{it['key']}",
                                  target_type=target, publishing=True)
            p.append(doi_prop)
            for x in p:
                x["note"] = ("published version: " + (x.get("note") or "")).strip()
                x["confidence"] = min(x["confidence"], 0.8)
            props += p
            issues.remove(iss)  # the fix is in Review ("switch to the published version")
        # orphan PDFs with a DOI -> propose a parent item
        orphan = self.store.issues("code='orphan_pdf' AND status='open'")
        for o in orphan:
            fd = (o["details"] or {}).get("found_doi")
            if not fd or (keys and o["item_key"] not in keys):
                continue
            try:
                rec = self.lookups.crossref(fd)
            except Exception:
                continue
            data = self.record_to_item(ctx, rec)
            if data:
                props.append({"item_key": o["item_key"], "kind": "create_parent", "field": "parentItem", "old": None,
                              "new": {"data": data}, "rules": [], "source": "crossref", "confidence": 0.7,
                              "note": f"create a parent item from DOI {fd} found in the PDF"})
                done.add(o["item_key"])
        self.store.add_proposals(props, ("crossref", "doi.org", "arxiv"), item_keys=sorted(done))
        self.store.replace_issues(issues, ("lookup",), item_keys=sorted(done))
        self.store.purge_trashed()
        return {"items": len(items), "looked_up": len(done), "proposals": len(props), "issues": len(issues),
                "not_found": counter["notfound"], "errors": counter["errors"]}

    def record_to_item(self, ctx, rec) -> dict | None:
        typ = rec.get("itemType") or "journalArticle"
        if self.schema.types and typ not in self.schema.types:
            typ = "journalArticle"
        data = {"itemType": typ, "creators": rules.normalize_creators(ctx, typ, rec.get("creators") or [], external=True)[0],
                "tags": [], "collections": [], "relations": {}}
        for base in enrich.FIELDS:
            v = rec.get(base)
            if not v or not isinstance(v, str):
                continue
            f = self.schema.actual(typ, base)
            if f and self.schema.has_field(typ, f):
                data[f] = rules.normalize_value(ctx, typ, f, v, external=True)[0]
        if rec.get("DOI") and self.schema.has_field(typ, "DOI"):
            data["DOI"] = rec["DOI"]
        return data if data.get("title") else None

    # ------------------------------------------------------------------ everything
    def job_find_pdfs(self, job: Job, keys: list | None = None, refresh: bool = False):
        """Look for open-access PDFs of items without one; verified finds become
        'attach PDF' proposals to review."""
        from . import filenames
        from .pdffind import PdfFinder
        finder = PdfFinder(self.settings, self.store, progress=lambda m: job.progress(m))
        if keys:
            targets = [k for k in keys]
        else:
            targets = sorted({i["item_key"] for i in self.store.issues("code='missing_pdf' AND status='open'")})
        open_props = {p["item_key"] for p in self.store.proposals("kind='attach_pdf' AND status IN ('pending','accepted')")}
        if not refresh:
            targets = [k for k in targets if k not in open_props]
        props, found, tried, errors, reasons = [], 0, 0, [], {}
        try:
            self._find_loop(job, finder, targets, props, errors, reasons)
        finally:
            finder.close()
        tried = sum(reasons.values()) + len(props) + 0
        found = len(props)
        self.store.add_proposals(props, ("pdffind",), item_keys=[k for k in targets])
        # refresh the missing-PDF warnings: found ones move to Review, the others say why
        self.job_pdfs(job)
        return {"searched": tried, "found": found, "not_found": tried - found - len(errors), "errors": len(errors),
                "reasons": reasons}

    def _find_loop(self, job, finder, targets, props, errors, reasons):
        from . import filenames
        for n, k in enumerate(targets):
            job.check()
            it = self.store.item(k)
            if not it or it["deleted"]:
                continue
            job.progress(f"Looking for a PDF: {tu.plain(it['data'].get('title', ''))[:60]}", n, len(targets))
            try:
                res = finder.find_for_item(it, cancelled=job.cancelled)
            except Exception as e:  # one bad response must not stop the whole search
                log.warning("PDF search failed for %s: %s", k, traceback.format_exc())
                res = {"found": False, "reason": f"search error: {e}"[:200], "attempts": []}
                errors.append(k)
            self.store.cache_set("pdffind:" + k, {kk: v for kk, v in res.items() if kk != "path"})
            if not res.get("found"):
                r = res.get("reason") or "no open-access copy found"
                reasons[r] = reasons.get(r, 0) + 1
                continue
            fname = filenames.filename_for(it["data"], it.get("meta"), "x.pdf", self.settings)
            conf = 0.9 if res["version"] in ("publishedVersion", "self") and res["score"] >= 90 else \
                0.75 if res["version"] in ("publishedVersion", "acceptedVersion", "self") else 0.6
            props.append({"item_key": k, "kind": "attach_pdf", "field": "attachment", "old": None,
                          "new": {kk: res[kk] for kk in ("path", "url", "source", "version", "version_label", "license",
                                                          "host", "score", "evidence", "pages", "size")} | {"filename": fname},
                          "rules": [], "source": "pdffind", "confidence": conf,
                          "note": ""})

    def needs_own_browser(self) -> list[str]:
        """Missing-PDF items whose publisher blocked the automated search."""
        out = []
        for i in self.store.issues("code='missing_pdf' AND status='open'"):
            r = self.store.cache_get("pdffind:" + i["item_key"]) or {}
            if r.get("needs_browser"):
                out.append(i["item_key"])
        return out

    def job_own_browser(self, job: Job, keys: list | None = None, open_tabs: bool = True, minutes: float = 15,
                        lookback_hours: float = 0, all_missing: bool = False):
        """Open the articles in your normal browser and pick the PDFs you download
        from your Downloads folder; each file is matched to its item by DOI/title."""
        from . import filenames, ownbrowser
        from .pdffind import STAGING, PdfFinder, VERSION_LABEL
        if keys:
            targets = list(keys)
        elif all_missing or not open_tabs:
            targets = sorted({i["item_key"] for i in self.store.issues("code='missing_pdf' AND status='open'")})
        else:
            targets = self.needs_own_browser()
        if not targets:
            return {"searched": 0, "found": 0, "not_found": 0, "errors": 0, "reasons": {}}
        items = {k: self.store.item(k) for k in targets}
        items = {k: v for k, v in items.items() if v and not v["deleted"]}
        folder = ownbrowser.downloads_dir(self.settings)
        if not folder.exists():
            raise RuntimeError(f"Downloads folder not found: {folder} (set it in Settings → PDFs)")
        since = time.time() - lookback_hours * 3600 - 2
        watcher = ownbrowser.DownloadWatcher(folder, since)
        if open_tabs:
            urls = []
            for k, it in items.items():
                doi, ax = enrich.item_ids(it)
                u = f"https://doi.org/{doi}" if doi else (it["data"].get("url") or "")
                if u:
                    urls.append(u)
            job.progress(f"Opening {len(urls)} article(s) in your browser …")
            ownbrowser.open_in_browser(urls[:25])
        remaining = dict(items)
        props, unmatched = [], []
        deadline = time.monotonic() + (minutes * 60 if open_tabs else 7)
        while remaining and time.monotonic() < deadline and not job.cancelled():
            left = int(deadline - time.monotonic())
            job.progress(f"ACTION NEEDED: download each paper's PDF in your browser — received {len(props)} of "
                         f"{len(items)} · watching {folder.name} for {left // 60}:{left % 60:02d} more"
                         if open_tabs else f"Checking PDFs in {folder.name} …", len(props), len(items))
            for pdf in watcher.poll():
                from .pdfs import analyze_pdf
                a = analyze_pdf(pdf, max_pages=6)
                if not a.get("is_pdf"):
                    unmatched.append(pdf.name)
                    continue
                scanned = not (a.get("text") or "").strip()
                best, best_score, best_ev = None, 0, ""
                for k, it in remaining.items():
                    doi, ax = enrich.item_ids(it)
                    isbns = [x.replace("-", "") for x in tu.normalize_isbn_field(it["data"].get("ISBN", "")).split()]
                    ok, score, ev = PdfFinder.verify_analysis(a, it["data"], doi, ax, isbns)
                    if ok and ev.startswith("title found") and "first author" not in ev \
                            and len(tu.canon(tu.plain(it["data"].get("title", "")))) < 30:
                        ok = False   # a short title alone ("Introduction") would match many downloads
                    if ok and score > best_score:
                        best, best_score, best_ev = k, score, ev
                # a scan cannot be checked: only accept it when exactly one item is being waited for
                if not best or (scanned and len(remaining) > 1):
                    unmatched.append(pdf.name)
                    continue
                it = remaining.pop(best)
                staged = ownbrowser.stage_copy(pdf, STAGING, best)
                props.append({"item_key": best, "kind": "attach_pdf", "field": "attachment", "old": None,
                              "new": {"path": str(staged), "url": str(pdf), "source": "your browser (Downloads folder)",
                                      "version": "publishedVersion", "version_label": VERSION_LABEL["publishedVersion"],
                                      "license": "", "host": "your download", "score": round(best_score),
                                      "evidence": best_ev + f"; file {pdf.name}", "pages": a.get("pages"),
                                      "size": staged.stat().st_size,
                                      "filename": filenames.filename_for(it["data"], it.get("meta"), "x.pdf", self.settings)},
                              "rules": [], "source": "ownbrowser", "confidence": 0.9 if best_score >= 90 else 0.75, "note": ""})
                self.store.cache_set("pdffind:" + best, {"found": True, "reason": "downloaded in your browser"})
            time.sleep(2)
        self.store.add_proposals(props, ("ownbrowser",), item_keys=[p["item_key"] for p in props])
        self.job_pdfs(job)
        return {"searched": len(items), "found": len(props), "not_found": len(remaining), "errors": 0,
                "reasons": ({f"not downloaded (yet): {len(remaining)} item(s)": len(remaining)} if remaining else {})
                           | ({f"downloaded PDFs that match no missing item: {', '.join(unmatched[:5])}": len(unmatched)}
                              if unmatched else {})}

    def job_all(self, job: Job, lookups: bool = True):
        res = {"scan": self.job_scan(job)}
        res["pdfs"] = self.job_pdfs(job)
        if lookups:
            res["lookups"] = self.job_lookups(job)
        return res

    # ------------------------------------------------------------------ commit / rollback
    def job_commit(self, job: Job):
        c = Committer(self, job.progress, job.cancelled)
        res = c.run()
        job.progress("Re-analysing changed items …")
        self.job_analyse(job)
        return res

    def job_rollback(self, job: Job, commit_id: int):
        res = rollback(self, int(commit_id), job.progress, job.cancelled)
        self.job_analyse(job)
        return res

    def commit_summary(self):
        return summarize(self.store)

    def job_sleep(self, job: Job, secs: float = 1):  # used by tests
        for i in range(int(secs * 10)):
            job.check()
            time.sleep(0.1)
        return {"slept": secs}
