"""End-to-end: mock Zotero → scan → PDFs → lookups → review → commit → rollback."""
import importlib
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("zc")
    os.environ["ZCLEANER_HOME"] = str(tmp / "ws")
    (tmp / "ws").mkdir()
    shutil.copy(ROOT / "tests" / "fixture_schema.json", tmp / "ws" / "zotero_schema.json")
    import zcleaner.config as cfg
    importlib.reload(cfg)
    for m in ("zcleaner.backup", "zcleaner.service", "zcleaner.server"):
        if m in sys.modules:
            importlib.reload(sys.modules[m])
    from tests.mock_zotero import MockZotero, build_app, populate
    mz = MockZotero(tmp / "zotero")
    keys = populate(mz)
    srv = make_server("127.0.0.1", 23998, build_app(mz), threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    from zcleaner.service import App, Job
    app = App()
    app.settings.update({"zotero_api": "http://127.0.0.1:23998/api", "mailto": "test@example.org"})
    yield {"app": app, "mz": mz, "keys": keys, "Job": Job, "tmp": tmp}
    srv.shutdown()


def run(env, kind, **kw):
    j = env["Job"](kind, kw)
    return getattr(env["app"], f"job_{kind}")(j, **kw)


def props(app, **where):
    return app.store.proposals(" AND ".join(f"{k}=?" for k in where) or "1=1", list(where.values()))


def test_scan_and_rules(env):
    app, keys = env["app"], env["keys"]
    r = run(env, "scan")
    assert r["changed"] > 10
    counts = app.store.count_items()
    assert counts["trashed"] == 1
    k1 = keys["k1"]
    ps = {p["field"]: p for p in props(app, item_key=k1)}
    assert ps["DOI"]["new"] == "10.1038/s41592-019-0001-1"
    assert ps["pages"]["new"] == "12--19"
    assert ps["title"]["new"].startswith("Deep learning for protein structure prediction: A review of CNN")
    assert ps["date"]["new"] == "2019-03-05"
    assert ps["ISSN"]["new"] == "1548-7091"
    assert ps["language"]["new"] == "en"
    assert "utm_source" not in ps["url"]["new"]
    cr = ps["creators"]["new"]
    assert cr[0]["lastName"] == "Smith" and cr[0]["firstName"] == "John" and cr[1]["firstName"] == "J. R."
    k2 = {p["field"]: p for p in props(app, item_key=keys["k2"])}
    t = k2["title"]["new"]
    assert "Schr{\\\"o}dinger" in t and "$\\alpha$" in t and "CO$_{2}$" in t and "\\&" in t and not t.endswith(".")
    assert k2["creators"]["new"][0] == {"creatorType": "author", "firstName": "Hans", "lastName": "M{\\\"u}ller"}
    assert k2["creators"]["new"][1]["lastName"] == "Erd{\\H o}s"
    # HTML -> LaTeX, DOI from extra
    garcia = [p for p in props(app) if p["field"] == "title" and "E. coli" in (p["old"] or "")][0]
    assert garcia["new"] == "Synthesis of CO$_{2}$ in \\textit{E. coli} \\& friends"
    doi_extra = [p for p in props(app, item_key=garcia["item_key"]) if p["field"] == "DOI"][0]
    assert doi_extra["new"] == "10.1000/demo.123"
    # journal name variant
    assert any(p["field"] == "publicationTitle" and p["new"] == "Physical Review Letters" for p in props(app))
    assert any(p["field"] == "journalAbbreviation" and p["new"] == "Phys. Rev. Lett." for p in props(app))
    # arXiv stored as journal article -> preprint
    assert any(p["kind"] == "itemType" and p["value"]["itemType"] == "preprint" for p in props(app))
    # issues
    codes = {i["code"] for i in app.store.issues()}
    assert {"missing_title", "missing_creators", "type_suspect"} <= codes
    # no proposals for notes/trashed items
    trashed = [i for i in app.store.items() if i["deleted"]][0]
    assert not props(app, item_key=trashed["key"])


def test_duplicates(env):
    app = env["app"]
    groups, total = app.store.dup_groups()
    titles = [[app.store.item(k)["data"]["title"] for k in g["keys"]] for g in groups]
    assert any(any("Attention" in t for t in ts) for ts in titles)
    assert not any(any("Part I" in t for t in ts) for ts in titles)


def test_pdfs(env):
    app, keys = env["app"], env["keys"]
    r = run(env, "pdfs")
    codes = r["issues"]
    for c in ("pdf_not_pdf", "pdf_file_missing", "pdf_mismatch", "orphan_pdf", "missing_pdf"):
        assert c in codes, (c, codes)
    ren = [p for p in props(app, kind="rename_file")]
    assert any(p["new"] == "Kim - 2022 - Graph neural networks for chemistry.pdf" for p in ren)
    assert app.store.meta_get("data_dir")


def test_lookups(env, monkeypatch):
    app = env["app"]
    from zcleaner.lookups import Lookups, NotFound

    def fake_crossref(self, doi, refresh=False):
        if doi.lower() == "10.1039/demo.gnn":
            return Lookups.crossref_record({"DOI": doi, "type": "journal-article", "title": ["Graph Neural Networks for Chemistry"],
                                            "author": [{"given": "Soo-Min", "family": "Kim"}], "issued": {"date-parts": [[2022, 4, 2]]},
                                            "container-title": ["Chemical Science"], "short-container-title": ["Chem. Sci."],
                                            "volume": "13", "issue": "4", "page": "100-110", "ISSN": ["2041-6520"]})
        if doi.lower() == "10.1016/right.doi":
            return Lookups.crossref_record({"DOI": doi, "type": "journal-article", "title": ["Something else entirely about cats"],
                                            "author": [{"given": "Z", "family": "Zed"}], "issued": {"date-parts": [[2001]]}})
        if doi.lower() == "10.1234/orphan.5":
            return Lookups.crossref_record({"DOI": doi, "type": "journal-article", "title": ["An orphan paper"],
                                            "author": [{"given": "O", "family": "Rphan"}], "issued": {"date-parts": [[2010]]},
                                            "container-title": ["Journal of Orphans"]})
        raise NotFound(doi)

    monkeypatch.setattr(Lookups, "crossref", fake_crossref)
    monkeypatch.setattr(Lookups, "doi_csl", lambda self, doi, refresh=False: (_ for _ in ()).throw(NotFound(doi)))
    monkeypatch.setattr(Lookups, "crossref_search", lambda self, *a, **k: [])
    monkeypatch.setattr(Lookups, "arxiv_batch", lambda self, ids, refresh=False: {
        "1706.03762": {"source": "arxiv", "itemType": "preprint", "title": "Attention Is All You Need",
                       "creators": [{"creatorType": "author", "firstName": "Ashish", "lastName": "Vaswani"}], "date": "2017-06-12",
                       "DOI": "10.48550/arXiv.1706.03762", "published_doi": "", "repository": "arXiv", "archiveID": "arXiv:1706.03762"}})
    r = run(env, "lookups")
    assert r["looked_up"] >= 3
    gnn = env["keys"]["p1"]
    ps = {p["field"]: p for p in props(app, item_key=gnn) if p["source"] == "crossref"}
    assert ps["volume"]["new"] == "13" and ps["pages"]["new"] == "100--110"
    assert ps["date"]["new"] == "2022-04-02"
    assert ps["creators"]["new"][0]["firstName"] == "Soo-Min"
    assert "title" not in ps  # only case differs
    assert any(i["code"] == "doi_mismatch" for i in app.store.issues())
    assert any(p["kind"] == "create_parent" for p in props(app))


def test_commit_and_rollback(env):
    app, mz, keys = env["app"], env["mz"], env["keys"]
    k1 = keys["k1"]
    ids = [p["id"] for p in props(app, item_key=k1, status="pending")]
    app.store.set_status(ids, "accepted")
    # edit one value before accepting
    t = [p for p in props(app, item_key=k1) if p["field"] == "title"][0]
    app.store.set_edited(t["id"], "Deep learning for protein structure prediction: a review of CNN methods")
    # a rename + create parent
    ren = [p for p in props(app, kind="rename_file") if p["item_key"] == keys["a1"]][0]
    app.store.set_status([ren["id"]], "accepted")
    cp = [p for p in props(app, kind="create_parent")][0]
    app.store.set_status([cp["id"]], "accepted")
    # simulate a concurrent edit in Zotero on another accepted field -> conflict expected
    mz.items[k1]["data"]["pages"] = "12-20"
    mz.bump(k1)
    app.zot.authorize()
    res = run(env, "commit")
    d = mz.items[k1]["data"]
    assert d["title"] == "Deep learning for protein structure prediction: a review of CNN methods"
    assert d["DOI"] == "10.1038/s41592-019-0001-1"
    assert d["pages"] == "12-20"  # conflict: untouched
    assert res.get("conflict", 0) >= 1
    assert Path(res["backup"], "library.json.gz").exists() and Path(res["backup"], "zotero.sqlite").exists()
    att = mz.items[keys["a1"]]["data"]
    assert att["filename"] == "Kim - 2022 - Graph neural networks for chemistry.pdf"
    assert (mz.data_dir / "storage" / keys["a1"] / att["filename"]).exists()
    assert mz.items[keys["a5"]]["data"].get("parentItem")
    # rollback
    cid = res["commit_id"]
    rb = run(env, "rollback", commit_id=cid)
    d = mz.items[k1]["data"]
    assert d["title"].startswith("DEEP LEARNING")
    assert d["DOI"] == "https://doi.org/10.1038/s41592-019-0001-1"
    assert mz.items[keys["a1"]]["data"]["filename"] == "ssrn-12345.pdf"
    assert not mz.items[keys["a5"]]["data"].get("parentItem")
    assert rb.get("failed", 0) == 0


def test_linked_rename(env):
    app, mz, keys = env["app"], env["mz"], env["keys"]
    from tests.mock_zotero import make_pdf
    time.sleep(0.01)
    # the user replaced the wrong PDF with the right one -> mismatch gone, rename proposed
    make_pdf(Path(mz.items[keys["a4"]]["data"]["path"]), ["Paper with the wrong PDF", "Li Wu", "doi:10.1016/right.doi"] + ["text"] * 20)
    run(env, "pdfs")
    assert not [i for i in app.store.issues("code='pdf_mismatch'") if i["item_key"] == mz.items[keys["a4"]]["data"]["parentItem"]]
    ren = [p for p in props(app, kind="rename_file", status="pending") if p["item_key"] == keys["a4"]]
    assert ren
    app.store.set_status([ren[0]["id"]], "accepted")
    run(env, "commit")
    p = Path(mz.items[keys["a4"]]["data"]["path"])
    assert p.exists() and p.name.startswith("Wu - 2019")


def test_single_use_key_is_refused_before_anything_happens(env):
    app, mz = env["app"], env["mz"]
    from zcleaner.zotero import AuthError
    run(env, "scan")
    p = props(app, status="pending")[0]
    app.store.set_status([p["id"]], "accepted")
    mz.remember = False           # the user clicks plain 'Allow'
    app.zot.forget_key()
    n_commits = len(app.store.commits())
    with pytest.raises(AuthError, match="Always Allow"):
        run(env, "commit")
    assert app.store.proposal(p["id"])["status"] == "accepted"   # review kept
    assert len(app.store.commits()) == n_commits                  # no backup, no commit
    mz.remember = True


def test_revoked_key_is_renewed_automatically(env):
    app, mz = env["app"], env["mz"]
    app.zot.authorize()
    mz.keys.clear()               # key revoked/expired in Zotero
    before = mz.auth_dialogs
    accepted = props(app, status="accepted")
    assert accepted
    res = run(env, "commit")
    assert mz.auth_dialogs == before + 1
    assert res.get("applied", 0) >= 1 and not res.get("failed")


def test_recover_technical_failures(env):
    app = env["app"]
    p = props(app, status="pending")[0]
    app.store.update_proposal(p["id"], status="failed", message="Zotero refused the write (401). The key may have been revoked")
    assert app.store.recover_interrupted() >= 1
    assert app.store.proposal(p["id"])["status"] == "accepted"
    app.store.set_status([p["id"]], "pending")


def test_preprint_type_converges(env, monkeypatch):
    """Local rule and online lookups must agree on the item type, so that
    applying the proposals and re-running proposes nothing new."""
    app, mz = env["app"], env["mz"]
    from zcleaner.lookups import Lookups, NotFound
    k = mz.add({"itemType": "journalArticle", "title": "Markovian noise modelling for quantum devices",
                "creators": [{"creatorType": "author", "lastName": "Rossi", "firstName": "Anna"}], "date": "2022",
                "DOI": "10.48550/arXiv.2202.04474", "url": "http://arxiv.org/abs/2202.04474",
                "extra": "arXiv:2202.04474 [quant-ph]"})
    mz.bump(k)
    run(env, "scan")
    # nothing known about a published version yet: the local rule says 'preprint'
    assert any(p["kind"] == "itemType" and p["status"] == "pending" for p in props(app, item_key=k))

    pub_doi = "10.1038/s41598-024-54598-5"
    monkeypatch.setattr(Lookups, "arxiv_batch", lambda self, ids, refresh=False: {
        "2202.04474": {"source": "arxiv", "itemType": "preprint", "title": "Markovian noise modelling for quantum devices",
                       "creators": [{"creatorType": "author", "firstName": "Anna", "lastName": "Rossi"}], "date": "2022-02-09",
                       "DOI": "10.48550/arXiv.2202.04474", "published_doi": pub_doi, "repository": "arXiv",
                       "archiveID": "arXiv:2202.04474"}})

    def fake_crossref(self, doi, refresh=False):
        if doi.lower() == pub_doi:
            return Lookups.crossref_record({"DOI": pub_doi, "type": "journal-article", "title": ["Markovian noise modelling for quantum devices"],
                                            "author": [{"given": "Anna", "family": "Rossi"}], "issued": {"date-parts": [[2024, 3, 1]]},
                                            "container-title": ["Scientific Reports"], "volume": "14", "page": "5123", "ISSN": ["2045-2322"]})
        raise NotFound(doi)
    monkeypatch.setattr(Lookups, "crossref", fake_crossref)
    monkeypatch.setattr(Lookups, "doi_csl", lambda self, doi, refresh=False: (_ for _ in ()).throw(NotFound(doi)))
    monkeypatch.setattr(Lookups, "crossref_search", lambda self, *a, **kw: [])
    app.lookups.store.cache_set("arxiv:2202.04474", {"published_doi": pub_doi})
    run(env, "lookups", keys=[k])
    run(env, "analyse")
    ps = [p for p in props(app, item_key=k) if p["status"] == "pending"]
    # now every source agrees: it is the published journal article, not a preprint
    assert not [p for p in ps if p["kind"] == "itemType"]
    assert not app.store.issues("item_key=? AND code='published_version'", (k,))
    pub = [p for p in ps if (p["group_id"] or "").startswith("pub-")]
    fields = {p["field"]: p for p in pub}
    assert fields["DOI"]["new"] == pub_doi and fields["publicationTitle"]["new"] == "Scientific Reports"
    # accepting one member accepts the whole group
    app.store.set_status([fields["DOI"]["id"]], "accepted")
    assert all(app.store.proposal(p["id"])["status"] == "accepted" for p in pub)
    app.store.set_status([p["id"] for p in props(app, item_key=k, status="pending")], "accepted")
    run(env, "commit")
    d = mz.items[k]["data"]
    assert d["DOI"] == pub_doi and d["publicationTitle"] == "Scientific Reports" and d["itemType"] == "journalArticle"
    # fixed point: running everything again proposes nothing for this item
    for _ in range(2):
        run(env, "scan")
        run(env, "lookups", keys=[k])
        assert not props(app, item_key=k, status="pending"), [(p["field"], p["old"], p["new"]) for p in props(app, item_key=k, status="pending")]


def test_edit_back_to_current_value_means_reject(env):
    app = env["app"]
    p = [x for x in props(app, status="pending") if x["kind"] == "field" and isinstance(x["old"], str)][0]
    assert app.store.set_edited(p["id"], p["old"]) is True
    app.store.set_status([p["id"]], "rejected", "you kept the current value")
    assert app.store.proposal(p["id"])["status"] == "rejected"
    # the old way (edit stored equal to the current value) is cleaned up at start-up
    q = [x for x in props(app, status="pending") if x["kind"] == "field" and isinstance(x["old"], str)][0]
    app.store.update_proposal(q["id"], edited=q["old"])
    assert q["id"] in app.store.resolve_noop_edits()
    assert app.store.proposal(q["id"])["status"] == "rejected"


def test_earlier_automatic_change_is_recomputed_and_converges(env):
    """A field changed by an older, worse version of a rule ('S-K' -> 's-k') is
    recomputed from its value before the automatic changes. Applying the result
    and re-running must propose nothing (no ping-pong), but a real change of the
    rules would still be picked up because the target is always f(base)."""
    app, mz = env["app"], env["mz"]
    from zcleaner.store import jd, now
    orig = "A sequence of approximated solutions to the S-K model for spin glasses"
    bad = "A sequence of approximated solutions to the s-k model for spin glasses"
    k = mz.add({"itemType": "journalArticle", "title": bad, "creators": [{"creatorType": "author", "lastName": "Z", "firstName": "A"}],
                "date": "1990", "publicationTitle": "J. Phys. A"})
    mz.bump(k)
    run(env, "scan")
    with app.store.conn() as c:
        c.execute("INSERT INTO proposals(item_key,kind,field,old,new,rules,source,confidence,status,fingerprint,created,updated)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (k, "field", "title", jd(orig), jd(bad), jd(["title_case"]), "rules", 0.45,
                                                      "applied", "y-" + k, now(), now()))
    assert app.compute_bases(app.store.items())[(k, "title")] == orig
    run(env, "analyse")
    fix = [p for p in props(app, item_key=k, status="pending") if p["field"] == "title"]
    assert len(fix) == 1 and fix[0]["new"] == orig
    app.store.set_status([fix[0]["id"]], "accepted")
    app.zot.authorize()
    run(env, "commit")
    assert mz.items[k]["data"]["title"] == orig
    for _ in range(3):
        run(env, "scan")
        assert not [p for p in props(app, item_key=k, status="pending") if p["field"] == "title"]
    # a genuine improvement of the rules is still picked up (target = f_new(base))
    from zcleaner import textutil
    textutil.PROPER_STRONG.add("glasses")
    app.settings.update({"title_case": "sentence"})
    try:
        run(env, "analyse")
        upd = [p for p in props(app, item_key=k, status="pending") if p["field"] == "title"]
        assert len(upd) == 1 and upd[0]["new"].endswith("Glasses")
    finally:
        textutil.PROPER_STRONG.discard("glasses")
        app.settings.update({"title_case": "keep"})
    run(env, "analyse")
    assert not [p for p in props(app, item_key=k, status="pending") if p["field"] == "title"]
    # a value you type yourself in Zotero becomes the new base
    mz.items[k]["data"]["title"] = "My own Title For This"
    mz.bump(k)
    run(env, "scan")
    assert (k, "title") not in app.compute_bases(app.store.items())


def test_whole_library_converges(env, monkeypatch):
    """Accept everything that is proposed, apply it, run everything again:
    nothing new may be proposed (all rules and sources agree on one target)."""
    app, mz = env["app"], env["mz"]
    from zcleaner.lookups import Lookups, NotFound
    monkeypatch.setattr(Lookups, "crossref", lambda self, doi, refresh=False: (_ for _ in ()).throw(NotFound(doi)))
    monkeypatch.setattr(Lookups, "doi_csl", lambda self, doi, refresh=False: (_ for _ in ()).throw(NotFound(doi)))
    monkeypatch.setattr(Lookups, "crossref_search", lambda self, *a, **kw: [])
    monkeypatch.setattr(Lookups, "arxiv_batch", lambda self, ids, refresh=False: {})
    app.settings.update({"title_case": "sentence"})
    app.zot.authorize()

    def everything():
        run(env, "scan")
        run(env, "pdfs")
        run(env, "lookups")

    everything()
    for rnd in range(3):
        pending = props(app, status="pending")
        if not pending:
            break
        # accept the highest-confidence proposal of each field (alternatives are superseded)
        app.store.set_status([p["id"] for p in sorted(pending, key=lambda p: -p["confidence"])], "accepted")
        run(env, "commit")
        everything()
    left = [(p["item_key"], p["field"], p["old"], p["value"]) for p in props(app, status="pending")
            if p["kind"] != "rename_file" or mz.items.get(p["item_key"])]
    # converges: at most one cascade round (e.g. a PDF gets a parent item, then its file can be renamed)
    assert rnd <= 2 and not left, left


def test_trashed_items_leave_no_issues(env):
    """Merging duplicates in Zotero moves the extra copies to the trash: their
    issues (also from earlier online lookups) and open proposals must go away."""
    app, mz = env["app"], env["mz"]
    k = mz.add({"itemType": "journalArticle", "title": "Some duplicate copy", "creators": [], "date": ""})
    mz.bump(k)
    run(env, "scan")
    app.store.replace_issues([{"item_key": k, "code": "published_version", "severity": "info", "message": "x",
                               "details": {"doi": "10.1/x"}, "source": "lookup"}], ("lookup",), item_keys=[k])
    assert app.store.issues("item_key=?", (k,))
    mz.items[k]["data"]["deleted"] = 1
    mz.bump(k)
    run(env, "scan")
    assert not app.store.issues("item_key=?", (k,))
    assert not [p for p in props(app, item_key=k) if p["status"] in ("pending", "accepted")]


def test_find_and_attach_missing_pdf(env, monkeypatch):
    """Missing PDF -> open-access candidates -> the login page is rejected, the
    wrong paper is rejected, the right PDF is proposed -> attach on commit -> roll back."""
    app, mz = env["app"], env["mz"]
    from tests.mock_zotero import make_pdf
    from zcleaner.pdffind import PdfFinder
    web = mz.data_dir / "web"
    web.mkdir(exist_ok=True)
    (web / "login.pdf").write_text("<!DOCTYPE html><html>Please log in</html>")
    make_pdf(web / "other.pdf", ["A completely different article", "doi:10.9999/other"] + ["x"] * 20)
    make_pdf(web / "right.pdf", ["No PDF at all here", "N. Nopdf", "doi: 10.1126/nopdf"] + ["body"] * 20)
    base = "http://127.0.0.1:23998/web/"
    monkeypatch.setattr(PdfFinder, "candidates", lambda self, doi, ax: [
        {"url": base + "login.pdf", "source": "Unpaywall", "version": "publishedVersion", "license": "", "host": "publisher"},
        {"url": base + "other.pdf", "source": "OpenAlex", "version": "publishedVersion", "license": "", "host": "repo"},
        {"url": base + "right.pdf", "source": "OpenAlex (PMC)", "version": "publishedVersion", "license": "cc-by", "host": "pmc"},
    ] if doi == "10.1126/nopdf" else [])
    run(env, "scan")
    run(env, "pdfs")
    key = next(k for k, it in mz.items.items() if it["data"].get("title") == "No PDF at all here")
    assert app.store.issues("item_key=? AND code='missing_pdf'", (key,))
    res = run(env, "find_pdfs")
    assert res["found"] >= 1
    p = [x for x in props(app, item_key=key) if x["kind"] == "attach_pdf"][0]
    assert p["value"]["source"] == "OpenAlex (PMC)" and p["value"]["score"] == 100
    # the warning is replaced by the proposal to review
    assert not app.store.issues("item_key=? AND code='missing_pdf'", (key,))
    app.store.set_status([p["id"]], "accepted")
    app.zot.authorize()
    out = run(env, "commit")
    kids = [i for i in mz.items.values() if i["data"].get("parentItem") == key and not i["data"].get("deleted")]
    assert len(kids) == 1 and kids[0]["data"]["filename"].startswith("Nopdf - 2012 - No PDF at all here")
    assert (mz.data_dir / "storage" / kids[0]["key"] / kids[0]["data"]["filename"]).exists()
    run(env, "scan")
    run(env, "pdfs")
    assert not app.store.issues("item_key=? AND code='missing_pdf'", (key,))
    run(env, "rollback", commit_id=out["commit_id"])
    assert mz.items[kids[0]["key"]]["data"].get("deleted")


def test_pdf_candidates_order_and_verification(env, monkeypatch, tmp_path):
    from tests.mock_zotero import make_pdf
    from zcleaner.pdffind import PdfFinder
    app = env["app"]
    f = PdfFinder(app.settings, app.store)
    answers = {
        "unpaywall": {"best_oa_location": {"url_for_pdf": "https://repo.org/accepted.pdf", "version": "acceptedVersion"},
                      "oa_locations": [{"url_for_pdf": "https://pub.org/final.pdf", "version": "publishedVersion", "license": "cc-by"}]},
        "openalex": {"locations": [{"is_oa": True, "pdf_url": "https://arxiv.org/pdf/2101.00001", "version": "submittedVersion",
                                    "landing_page_url": "https://arxiv.org/abs/2101.00001"}]},
        "s2": {"openAccessPdf": {"url": "https://pub.org/final.pdf"}, "externalIds": {"ArXiv": "2101.00001"}},
    }
    monkeypatch.setattr(PdfFinder, "_json", lambda self, url, rl, params=None, cache_key=None: answers[cache_key.split(":")[0]])
    c = f.candidates("10.1103/demo.1", "")
    assert [x["url"] for x in c] == ["https://pub.org/final.pdf", "https://repo.org/accepted.pdf", "https://arxiv.org/pdf/2101.00001"]
    assert c[-1]["version"] == "submittedVersion"
    good = tmp_path / "g.pdf"
    make_pdf(good, ["Graph neural networks for chemistry", "Soo Kim"] + ["x"] * 30)
    ok, score, ev = PdfFinder.verify(good, {"title": "Graph neural networks for chemistry",
                                            "creators": [{"lastName": "Kim"}]}, "", "")
    assert ok and score >= 90 and "first author" in ev


def test_pdf_candidates_from_isbn_pmid_and_title(env, monkeypatch, tmp_path):
    """Items without DOI: ISBN (OAPEN, Open Library, Crossref), PubMed ids
    (Europe PMC) and a title search (OpenAlex) must all lead to candidates."""
    from tests.mock_zotero import make_pdf
    from zcleaner.pdffind import PdfFinder
    app = env["app"]
    f = PdfFinder(app.settings, app.store)
    answers = {
        "crisbn": {"message": {"items": [{"DOI": "10.1515/demo-book", "title": ["Open Book of Physics"], "type": "monograph"}]}},
        "oapen": [{"name": "Open Book of Physics", "bitstreams": [
            {"name": "cover.jpg", "mimeType": "image/jpeg", "bundleName": "ORIGINAL", "retrieveLink": "/rest/bitstreams/c/retrieve"},
            {"name": "book.pdf", "mimeType": "application/pdf", "bundleName": "ORIGINAL", "retrieveLink": "/rest/bitstreams/b/retrieve"}]}],
        "olib": {"items": [{"status": "full access", "itemURL": "https://archive.org/details/openbookphys00"}]},
        "epmc": {"resultList": {"result": [{"doi": "10.1371/demo.pmc", "pmcid": "PMC123456"}]}},
        "oasearch": {"results": [{"display_name": "A forgotten conference talk on lattice models", "doi": None,
                                  "publication_year": 2011, "authorships": [{"author": {"display_name": "Anna Rossi"}}],
                                  "best_oa_location": {"pdf_url": "https://zenodo.org/record/1/files/talk.pdf", "version": "publishedVersion"}}]},
    }
    monkeypatch.setattr(PdfFinder, "_json", lambda self, url, rl, params=None, cache_key=None: answers.get((cache_key or "").split(":")[0]))
    monkeypatch.setattr(PdfFinder, "candidates", lambda self, doi, ax: [])
    book = {"key": "B1", "itemType": "book", "deleted": False, "meta": {},
            "data": {"itemType": "book", "title": "Open Book of Physics", "ISBN": "978-3-16-148410-0", "creators": []}}
    ids = f.identify(book)
    assert ids["doi"] == "10.1515/demo-book" and ids["isbns"] == ["9783161484100"]
    urls = [c["url"] for c in f.candidates_for(book, ids)]
    assert "https://library.oapen.org/rest/bitstreams/b/retrieve" in urls
    assert "https://archive.org/download/openbookphys00/openbookphys00.pdf" in urls
    art = {"key": "P1", "itemType": "journalArticle", "deleted": False, "meta": {},
           "data": {"itemType": "journalArticle", "title": "Something medical", "extra": "PMID: 998877", "creators": []}}
    ids = f.identify(art)
    assert ids["doi"] == "10.1371/demo.pmc" and ids["pmcid"] == "PMC123456"
    assert "https://europepmc.org/articles/PMC123456?pdf=render" in [c["url"] for c in f.candidates_for(art, ids)]
    talk = {"key": "T1", "itemType": "conferencePaper", "deleted": False, "meta": {},
            "data": {"itemType": "conferencePaper", "title": "A forgotten conference talk on lattice models", "date": "2011",
                     "creators": [{"creatorType": "author", "lastName": "Rossi", "firstName": "Anna"}]}}
    ids = f.identify(talk)
    assert ids["oa_work"] and [c["url"] for c in f.candidates_for(talk, ids)] == ["https://zenodo.org/record/1/files/talk.pdf"]
    # a book PDF is recognised by the ISBN printed on its copyright page
    pdf = tmp_path / "b.pdf"
    make_pdf(pdf, ["Some cover text", "ISBN 978-3-16-148410-0", "Printed somewhere"] + ["x"] * 30)
    ok, score, ev = PdfFinder.verify(pdf, book["data"], "", "", ["9783161484100"])
    assert ok and "ISBN" in ev


def test_pdf_search_is_robust(env, monkeypatch):
    """Cached list responses (OAPEN) must not crash; repository landing pages are
    followed to their PDF; an error on one item does not stop the search."""
    app, mz = env["app"], env["mz"]
    from tests.mock_zotero import make_pdf
    from zcleaner.pdffind import PdfFinder
    f = PdfFinder(app.settings, app.store)
    app.store.cache_set("oapen:9999999999999", [])
    assert f._json("https://library.oapen.org/rest/search", f.api_rl, cache_key="oapen:9999999999999") == []
    web = mz.data_dir / "web"
    web.mkdir(exist_ok=True)
    make_pdf(web / "thesis.pdf", ["New perspectives with lattices", "A. Student", "ETH Zurich"] + ["x"] * 20)
    (web / "record.html").write_text('<html><head><meta name="citation_pdf_url" content="/web/thesis.pdf"></head></html>')
    ok, why = f.download("landing:http://127.0.0.1:23998/web/record.html", mz.data_dir / "dl" / "t.pdf")
    assert ok, why
    calls = []

    def flaky(self, it, doi=None, arxiv=None, cancelled=None):
        calls.append(it["key"])
        if len(calls) == 1:
            raise ValueError("boom")
        return {"found": False, "reason": "no open-access copy found", "attempts": []}
    monkeypatch.setattr(PdfFinder, "find_for_item", flaky)
    res = run(env, "find_pdfs", refresh=True)
    assert res["errors"] == 1 and len(calls) > 1 and res["reasons"]


def test_publisher_pdf_through_institutional_access(env, tmp_path):
    """Publisher PDFs reached like a reader would: a 'Download PDF' link, a PDF
    inside a viewer frame, and a button that only a real browser can click."""
    app, mz = env["app"], env["mz"]
    from tests.mock_zotero import make_pdf
    from zcleaner.publisher import PublisherFetcher
    web = mz.data_dir / "web"
    web.mkdir(exist_ok=True)
    for n in ("a.pdf", "b.pdf", "c.pdf"):
        make_pdf(web / n, [f"Publisher copy {n}", "doi:10.5555/demo"] + ["x"] * 10)
    app.settings.update({"pdf": {"doi_resolver": "http://127.0.0.1:23998/doi/", "browser_headless": True, "use_browser": True,
                                 "browser_wait_seconds": 3}})
    f = PublisherFetcher(app.settings)
    try:
        ok, url = f.fetch_http("10.5555/link", tmp_path / "a.pdf")
        assert ok and url.endswith("/pub/files/a.pdf")
        ok, url = f.fetch_http("10.5555/viewer", tmp_path / "b.pdf")
        assert ok and url.endswith("/pub/files/b.pdf")
        ok, msg = f.fetch_http("10.5555/jsbutton", tmp_path / "c.pdf")
        assert not ok
        try:
            import playwright  # noqa: F401
            has_browser = True
        except ImportError:
            has_browser = False
        ok, url, how = f.fetch("10.5555/jsbutton", tmp_path / "c.pdf")
        if has_browser:
            assert ok and how == "browser", url
            assert (tmp_path / "c.pdf").read_bytes()[:5] == b"%PDF-"
        else:
            assert not ok and "playwright" in url.lower()
        ok, msg, how = f.fetch("10.5555/closed", tmp_path / "d.pdf")
        assert not ok
    finally:
        f.close()
        app.settings.update({"pdf": {"doi_resolver": "https://doi.org/", "browser_headless": False}})


def test_find_uses_publisher_when_enabled(env, monkeypatch):
    app, mz = env["app"], env["mz"]
    from tests.mock_zotero import make_pdf
    from zcleaner.pdffind import PdfFinder
    make_pdf(mz.data_dir / "web" / "a.pdf", ["Paywalled but licensed article", "Ada Lovelace", "doi:10.5555/link"] + ["x"] * 10)
    monkeypatch.setattr(PdfFinder, "candidates", lambda self, doi, ax: [])
    app.settings.update({"pdf": {"institutional": True, "use_browser": False, "doi_resolver": "http://127.0.0.1:23998/doi/"}})
    try:
        f = PdfFinder(app.settings, app.store)
        it = {"key": "PUB1", "itemType": "journalArticle", "deleted": False, "meta": {},
              "data": {"itemType": "journalArticle", "title": "Paywalled but licensed article", "DOI": "10.5555/link",
                       "creators": [{"creatorType": "author", "lastName": "Lovelace", "firstName": "Ada"}]}}
        r = f.find_for_item(it)
        f.close()
        assert r["found"] and r["source"].startswith("the publisher") and r["url"].endswith("/pub/files/a.pdf"), r
        assert r["score"] == 100
    finally:
        app.settings.update({"pdf": {"institutional": False, "use_browser": True, "doi_resolver": "https://doi.org/"}})


def test_captcha_is_not_reported_as_missing_pdf(env, tmp_path):
    """A captcha page is recognised: it is reported as 'blocked', not as 'no PDF';
    a check that passes by itself (Cloudflare style) is waited for."""
    app, mz = env["app"], env["mz"]
    from tests.mock_zotero import make_pdf
    from zcleaner.publisher import PublisherFetcher, is_challenge
    assert is_challenge('<html><head><title>Just a moment...</title></head></html>')
    assert not is_challenge('<html><head><title>Graph neural networks</title></head><body>article</body></html>')
    make_pdf(mz.data_dir / "web" / "a.pdf", ["Publisher copy", "doi:10.5555/x"] + ["x"] * 10)
    app.settings.update({"pdf": {"doi_resolver": "http://127.0.0.1:23998/doi/", "use_browser": False}})
    f = PublisherFetcher(app.settings)
    try:
        ok, msg, how = f.fetch("10.5555/captcha", tmp_path / "x.pdf")
        assert not ok and f.blocked and "captcha" in msg
        ok, msg, how = f.fetch("10.5555/closed", tmp_path / "x.pdf")
        assert not ok and not f.blocked
    finally:
        f.close()
    try:
        import playwright  # noqa: F401
    except ImportError:
        return
    app.settings.update({"pdf": {"use_browser": True, "browser_headless": True, "browser_wait_seconds": 5}})
    f = PublisherFetcher(app.settings)
    try:
        ok, url, how = f.fetch("10.5555/autocheck", tmp_path / "y.pdf")
        assert ok and how == "browser" and url.endswith("/pub/files/a.pdf"), url
    finally:
        f.close()
        app.settings.update({"pdf": {"doi_resolver": "https://doi.org/", "browser_headless": False}})


class _FakePage:
    """A browser tab showing a check that comes back after every 'solve' (reload)."""
    def __init__(self, reloads_at=(1, 2), closes_at=None):
        self.t, self.cb, self.reloads_at, self.closes_at, self.closed = 0, None, reloads_at, closes_at, False
        self.main_frame = object()

    def content(self):
        return '<html><head><title>Just a moment...</title></head><body><div id="challenge-form"></div></body></html>'

    def title(self):
        return "Just a moment..."

    def bring_to_front(self):
        pass

    def on(self, ev, cb):
        self.cb = cb

    def remove_listener(self, ev, cb):
        self.cb = None

    def wait_for_timeout(self, ms):
        self.t += 1
        if self.t in self.reloads_at and self.cb:
            self.cb(self.main_frame)
        if self.closes_at and self.t >= self.closes_at:
            self.closed = True

    def is_closed(self):
        return self.closed


def test_captcha_that_keeps_coming_back_is_given_up(env):
    """Solved check -> reload -> the same check again: the tool stops waiting
    (instead of looping for minutes) and marks the site as rejecting automation."""
    from zcleaner.publisher import PublisherFetcher
    app = env["app"]
    f = PublisherFetcher(app.settings)
    f.headless, f.captcha_wait = False, 60
    msgs = []
    f.progress = msgs.append
    assert f._wait_for_human(_FakePage(), "test") is False
    assert f.rejects_automation and any("came back" in m for m in msgs)
    # closing the tab also hands the item over to your own browser, immediately
    f2 = PublisherFetcher(app.settings)
    f2.headless, f2.captcha_wait = False, 60
    assert f2._wait_for_human(_FakePage(reloads_at=(), closes_at=2), "test") is False and f2.rejects_automation
    f.close(), f2.close()


def test_own_browser_picks_up_downloads(env, monkeypatch, tmp_path):
    """Blocked item -> opened in your own browser -> the PDF you download is matched
    by its DOI and proposed; an unrelated download is left alone."""
    app, mz = env["app"], env["mz"]
    from tests.mock_zotero import make_pdf
    from zcleaner import ownbrowser
    run(env, "scan")
    run(env, "pdfs")
    missing = [i["item_key"] for i in app.store.issues("code='missing_pdf' AND status='open'")]
    key = next(k for k in missing if (mz.items[k]["data"].get("DOI") or ""))
    doi = mz.items[key]["data"]["DOI"]
    app.store.cache_set("pdffind:" + key, {"found": False, "needs_browser": True, "reason": "blocked by the publisher's captcha"})
    assert key in app.needs_own_browser()
    dl = tmp_path / "Downloads"
    dl.mkdir()
    app.settings.update({"pdf": {"downloads_dir": str(dl)}})
    opened = []

    def fake_open(urls, pause=1.0):
        opened.extend(urls)
        make_pdf(dl / "old-unrelated.pdf", ["Holiday photos", "nothing to see"] + ["x"] * 5)
        make_pdf(dl / "S0001-main.pdf", ["Some article", f"https://doi.org/{doi}"] + ["body"] * 10)
        (dl / "partial.pdf.crdownload").write_bytes(b"%PDF-1.4 partial")
    monkeypatch.setattr(ownbrowser, "open_in_browser", fake_open)
    res = run(env, "own_browser", minutes=0.3)
    assert opened == [f"https://doi.org/{doi}"]
    assert res["found"] == 1 and res["not_found"] == 0, res
    p = [x for x in props(app, item_key=key) if x["kind"] == "attach_pdf" and x["status"] == "pending"][0]
    assert p["source"] == "ownbrowser" and p["value"]["score"] == 100 and "S0001-main.pdf" in p["value"]["evidence"]
    assert (dl / "S0001-main.pdf").exists()          # your download is left untouched
    assert not app.store.issues("item_key=? AND code='missing_pdf' AND status='open'", (key,))
    # picking up files already downloaded: nothing new matches -> reported, not attached
    res = run(env, "own_browser", open_tabs=False, lookback_hours=24)
    assert res["found"] == 0 and any("match no missing item" in r for r in res["reasons"]), res
    app.store.set_status([p["id"]], "rejected")
    app.settings.update({"pdf": {"downloads_dir": ""}})
