"""Local web server for the review GUI.

Security: binds to 127.0.0.1 only, rejects foreign Host headers (DNS
rebinding) and requires a per-session token on every API call (CSRF)."""
from __future__ import annotations

import csv
import io
import json
import re
import secrets as pysecrets
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request

from . import filenames, latex
from . import textutil as tu
from .config import BACKUP_DIR, RULES
from .duplicates import completeness
from .enrich import item_ids
from .pdfs import is_pdf_attachment
from .service import App
from .store import fingerprint, jd, now

STATIC = Path(__file__).parent / "static"
TOKEN = pysecrets.token_urlsafe(24)
PAGE = 50


def create_app(port: int = 5000) -> Flask:
    core = App()
    web = Flask(__name__, static_folder=None)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    web.config["core"] = core

    # ------------------------------------------------------------------ security
    @web.before_request
    def guard():
        if request.host not in allowed_hosts:
            abort(403, "bad host")
        if request.path.startswith("/api/") and request.headers.get("X-Token") != TOKEN and request.args.get("token") != TOKEN:
            abort(403, "missing token")

    @web.errorhandler(Exception)
    def err(e):
        code = getattr(e, "code", 500)
        if not isinstance(code, int):
            code = 500
        return jsonify(error=str(getattr(e, "description", None) or e)), code

    @web.after_request
    def headers(r):
        r.headers["X-Frame-Options"] = "DENY"
        r.headers["Referrer-Policy"] = "no-referrer"
        r.headers["Cache-Control"] = "no-store"
        return r

    # ------------------------------------------------------------------ static
    @web.get("/")
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
        return Response(html, mimetype="text/html")

    @web.get("/static/<path:name>")
    def static(name):
        p = (STATIC / name).resolve()
        if STATIC.resolve() not in p.parents or not p.exists():
            abort(404)
        mt = {"js": "text/javascript", "css": "text/css", "svg": "image/svg+xml"}.get(p.suffix[1:], "application/octet-stream")
        return Response(p.read_bytes(), mimetype=mt)

    def body():
        return request.get_json(silent=True) or {}

    # ------------------------------------------------------------------ helpers
    def item_brief(it) -> dict:
        if not it:
            return {}
        d = it["data"]
        parent = core.store.item(it["parent"]) if it.get("parent") else None
        cr = d.get("creators") or []
        names = [tu.plain(c.get("lastName") or c.get("name") or "") for c in cr]
        y = re.search(r"\d{4}", (it.get("meta") or {}).get("parsedDate") or d.get("date", "") or "")
        return {"key": it["key"], "itemType": it["itemType"], "title": d.get("title", ""),
                "title_plain": tu.plain(d.get("title", "")) or tu.plain(d.get("filename", "")) or "(untitled)",
                "creators": ", ".join(names[:3]) + (" et al." if len(names) > 3 else ""),
                "year": y.group(0) if y else "", "doi": tu.clean_doi(d.get("DOI", "")),
                "publication": tu.plain(d.get("publicationTitle") or d.get("proceedingsTitle") or d.get("bookTitle") or ""),
                "deleted": it.get("deleted"), "parent": item_brief(parent) if parent else None,
                "filename": d.get("filename") or Path(d.get("path") or "").name if it["itemType"] == "attachment" else None,
                "zotero": zlink(it["key"])}

    def zlink(key):
        lib = core.settings.get("library")
        if lib.startswith("groups/"):
            return f"zotero://select/groups/{lib.split('/')[1]}/items/{key}"
        return f"zotero://select/library/items/{key}"

    def render_value(v):
        if isinstance(v, str):
            return tu.plain(v)
        return None

    def prop_out(p):
        p = dict(p)
        p["old_plain"] = render_value(p["old"])
        p["new_plain"] = render_value(p["value"])
        p["latex_problems"] = latex.check(p["value"]) if isinstance(p["value"], str) and p["kind"] == "field" else []
        if p["kind"] == "creators":
            def names(cs):
                out = []
                for c in cs or []:
                    n = tu.plain(c.get("name")) if c.get("name") is not None else \
                        tu.plain(c.get("lastName", "")) + (", " + tu.plain(c.get("firstName", "")) if c.get("firstName") else "")
                    out.append(f"{n} ({c.get('creatorType', 'author')})")
                return out
            p["old_names"], p["new_names"] = names(p["old"]), names(p["value"])
        return p

    def prop_filter(args) -> tuple[str, list]:
        w, a = ["1=1"], []
        st = args.get("status", "pending")
        if st and st != "all":
            w.append("p.status IN (%s)" % ",".join("?" * len(st.split(","))))
            a += st.split(",")
        for k, col in (("source", "p.source"), ("kind", "p.kind"), ("field", "p.field"), ("item_type", "i.item_type")):
            if args.get(k):
                w.append(f"{col}=?")
                a.append(args[k])
        if args.get("rule"):
            w.append("p.rules LIKE ?")
            a.append(f'%"{args["rule"]}"%')
        if args.get("min_conf"):
            w.append("p.confidence>=?")
            a.append(float(args["min_conf"]))
        if args.get("max_conf"):
            w.append("p.confidence<=?")
            a.append(float(args["max_conf"]))
        if args.get("group"):
            w.append("p.group_id=?")
            a.append(args["group"])
        if args.get("q"):
            w.append("(i.title LIKE ? OR p.new LIKE ? OR p.old LIKE ? OR p.item_key=?)")
            q = f"%{args['q']}%"
            a += [q, q, q, args["q"]]
        return " AND ".join(w), a

    # ------------------------------------------------------------------ status
    @web.get("/api/status")
    def status():
        s = core.store
        with s.conn() as c:
            props = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM proposals GROUP BY status")}
            issues = {r["code"]: r["n"] for r in c.execute("SELECT code, COUNT(*) n FROM issues WHERE status='open' GROUP BY code")}
            sev = {r["severity"]: r["n"] for r in c.execute("SELECT severity, COUNT(*) n FROM issues WHERE status='open' GROUP BY severity")}
            dups = c.execute("SELECT COUNT(*) FROM dup_groups WHERE status='open'").fetchone()[0]
            pdfc = c.execute("SELECT COUNT(*) n, SUM(exists_=1) present, SUM(is_pdf=1) ok FROM pdf").fetchone()
        cur = core.current.as_dict() if core.current else None
        return jsonify({
            "items": s.count_items(), "proposals": props, "issues": issues, "severity": sev, "duplicates": dups,
            "pdf": dict(pdfc), "last_scan": s.meta_get("last_scan"), "server_id": core.zot.server_id,
            "write_authorised": core.zot.key_reusable, "data_dir": core.settings.get("data_dir") or s.meta_get("data_dir"),
            "schema_loaded": core.schema.loaded, "job": cur, "mailto": bool(core.settings.get("mailto")),
            "commit": core.commit_summary(),
        })

    @web.post("/api/connect")
    def connect():
        return jsonify(core.zot.ping())

    @web.post("/api/authorize")
    def authorize():
        res = core.zot.authorize()
        if not res.get("remember"):
            core.zot.forget_key()  # 'Allow' = one write only: useless for applying many changes
        return jsonify(res)

    @web.post("/api/forget-key")
    def forget_key():
        core.zot.forget_key()
        return jsonify(ok=True)

    @web.post("/api/reset-connection")
    def reset_connection():
        core.zot.reset_connection()
        core.store.meta_set("library_version", None)
        return jsonify(ok=True)

    # ------------------------------------------------------------------ jobs
    @web.post("/api/jobs/<kind>")
    def job_start(kind):
        if kind not in ("scan", "analyse", "pdfs", "lookups", "all", "commit", "rollback", "sleep", "find_pdfs", "own_browser", "reassign_tags", "create_smart_folders"):
            abort(404)
        try:
            j = core.start(kind, body())
        except RuntimeError as e:
            return jsonify(error=str(e)), 409
        return jsonify(j.as_dict())

    @web.get("/api/jobs/<jid>")
    def job_get(jid):
        j = core.jobs.get(jid)
        if not j:
            abort(404)
        return jsonify(j.as_dict())

    @web.post("/api/jobs/<jid>/cancel")
    def job_cancel(jid):
        core.cancel(jid)
        return jsonify(ok=True)

    # ------------------------------------------------------------------ proposals
    @web.get("/api/proposals")
    def proposals():
        where, args = prop_filter(request.args)
        off = int(request.args.get("offset", 0))
        lim = min(200, int(request.args.get("limit", PAGE)))
        order = request.args.get("order", "title")
        order_sql = {"title": "MIN(i.title)", "confidence": "MIN(p.confidence) DESC", "low": "MIN(p.confidence)",
                     "changed": "MAX(p.updated) DESC"}.get(order, "MIN(i.title)")
        with core.store.conn() as c:
            total = c.execute(f"SELECT COUNT(DISTINCT p.item_key) FROM proposals p LEFT JOIN items i ON i.key=p.item_key WHERE {where}", args).fetchone()[0]
            nprops = c.execute(f"SELECT COUNT(*) FROM proposals p LEFT JOIN items i ON i.key=p.item_key WHERE {where}", args).fetchone()[0]
            keys = [r[0] for r in c.execute(
                f"SELECT p.item_key FROM proposals p LEFT JOIN items i ON i.key=p.item_key WHERE {where} "
                f"GROUP BY p.item_key ORDER BY {order_sql}, p.item_key LIMIT ? OFFSET ?", args + [lim, off])]
        out = []
        by_key = {}
        if keys:
            with core.store.conn() as c:
                rows = c.execute(f"SELECT p.* FROM proposals p LEFT JOIN items i ON i.key=p.item_key WHERE {where} "
                                 f"AND p.item_key IN ({','.join('?' * len(keys))}) ORDER BY p.kind='rename_file', p.field, p.confidence DESC",
                                 args + keys).fetchall()
            for r in rows:
                by_key.setdefault(r["item_key"], []).append(core.store._prop_row(r))
        for k in keys:
            it = core.store.item(k)
            ps = by_key.get(k, [])
            out.append({"item": item_brief(it) if it else {"key": k, "title_plain": "(not in snapshot)"},
                        "proposals": [prop_out(p) for p in ps],
                        "issues": core.store.issues("item_key=? AND status='open'", (k,))})
        return jsonify({"total_items": total, "total_proposals": nprops, "offset": off, "items": out})

    @web.get("/api/pdfs/needs-browser")
    def needs_browser():
        from .ownbrowser import downloads_dir
        return jsonify({"keys": core.needs_own_browser(), "downloads": str(downloads_dir(core.settings))})

    @web.get("/api/staged/<int:pid>")
    def staged_pdf(pid):
        """Show a downloaded (not yet attached) PDF so you can check it."""
        p = core.store.proposal(pid)
        if not p or p["kind"] != "attach_pdf":
            abort(404)
        from .pdffind import STAGING
        path = Path(p["value"]["path"]).resolve()
        if STAGING.resolve() not in path.parents or not path.exists():
            abort(404, "file no longer available — run 'Find PDFs' again")
        return Response(path.read_bytes(), mimetype="application/pdf",
                        headers={"Content-Disposition": f"inline; filename={p['value'].get('filename', 'paper.pdf')}"})

    @web.get("/api/proposals/facets")
    def facets():
        st = request.args.get("status", "pending")
        stw = "1=1" if st == "all" else "status IN (%s)" % ",".join("?" * len(st.split(",")))
        sa = [] if st == "all" else st.split(",")
        res = {}
        with core.store.conn() as c:
            for col in ("source", "kind", "field"):
                res[col] = {r[0]: r[1] for r in c.execute(f"SELECT {col}, COUNT(*) FROM proposals WHERE {stw} GROUP BY {col} ORDER BY 2 DESC", sa)}
            rc = {}
            for r in c.execute(f"SELECT rules FROM proposals WHERE {stw}", sa):
                for x in json.loads(r[0] or "[]"):
                    rc[x] = rc.get(x, 0) + 1
            res["rule"] = dict(sorted(rc.items(), key=lambda kv: -kv[1]))
            res["status"] = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM proposals GROUP BY status")}
            res["confidence"] = {r[0]: r[1] for r in c.execute(
                f"SELECT CASE WHEN confidence>=0.85 THEN 'high' WHEN confidence>=0.6 THEN 'medium' ELSE 'low' END b, COUNT(*) FROM proposals WHERE {stw} GROUP BY b", sa)}
        res["rule_labels"] = {k: v[0] for k, v in RULES.items()} | {"pdf_name": "PDF file name"}
        return jsonify(res)

    @web.post("/api/proposals/set")
    def proposals_set():
        b = body()
        ids = [int(x) for x in b.get("ids", [])]
        kept = False
        if "edited" in b and len(ids) == 1:
            kept = core.store.set_edited(ids[0], b["edited"])
        if kept:  # edited back to the current value: nothing to change
            core.store.set_status(ids, "rejected", "you kept the current value")
        elif b.get("status"):
            if b["status"] not in ("pending", "accepted", "rejected"):
                abort(400)
            core.store.set_status(ids, b["status"])
        return jsonify(ok=True, kept_current=kept, proposals=[prop_out(core.store.proposal(i)) for i in ids])

    @web.post("/api/proposals/bulk")
    def proposals_bulk():
        b = body()
        action = b.get("status")
        if action not in ("pending", "accepted", "rejected"):
            abort(400)
        where, args = prop_filter(b.get("filter") or {})
        with core.store.conn() as c:
            ids = [r[0] for r in c.execute(f"SELECT p.id FROM proposals p LEFT JOIN items i ON i.key=p.item_key WHERE {where} ORDER BY p.confidence DESC", args)]
        core.store.set_status(ids, action)
        return jsonify(ok=True, count=len(ids))

    @web.post("/api/items/<key>/propose")
    def propose_manual(key):
        """A change typed by the user (any field, creators or tags)."""
        b = body()
        it = core.store.item(key)
        if not it:
            abort(404)
        field, value = b.get("field"), b.get("value")
        kind = "creators" if field == "creators" else ("tags" if field == "tags" else "field")
        old = it["data"].get(field, [] if kind != "field" else "")
        if kind == "field" and not core.schema.has_field(it["itemType"], field):
            return jsonify(error=f"'{field}' is not a field of {it['itemType']}"), 400
        p = {"item_key": key, "kind": kind, "field": field, "old": old, "new": value, "rules": [], "source": "manual",
             "confidence": 1.0, "note": "your edit", "status": "pending"}
        with core.store.conn() as c:
            c.execute("INSERT INTO proposals(item_key,kind,field,old,new,rules,source,confidence,note,status,fingerprint,created,updated)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (key, kind, field, jd(old), jd(value), "[]", "manual", 1.0, "your edit",
                                                            "pending", fingerprint(key, kind, field, value), now(), now()))
            pid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        core.store.set_status([pid], "accepted")
        return jsonify(ok=True, id=pid)

    @web.get("/api/items/<key>")
    def item_detail(key):
        it = core.store.item(key)
        if not it:
            abort(404)
        kids = core.store.items("parent=? AND deleted=0", (key,))
        pdf = {r["att_key"]: r for r in core.store.pdf_rows("parent_key=? OR att_key=?", (key, key))}
        return jsonify({"item": item_brief(it), "data": it["data"], "meta": it["meta"],
                        "plain": {k: tu.plain(v) for k, v in it["data"].items() if isinstance(v, str) and v},
                        "fields": core.schema.fields(it["itemType"]) or [k for k in it["data"] if isinstance(it["data"][k], str)],
                        "proposals": [prop_out(p) for p in core.store.proposals("item_key=?", (key,))],
                        "issues": core.store.issues("item_key=?", (key,)),
                        "attachments": [dict(item_brief(k), linkMode=k["data"].get("linkMode"), contentType=k["data"].get("contentType"),
                                             pdf={kk: v for kk, v in (pdf.get(k["key"]) or {}).items() if kk != "first_text"}) for k in kids]})

    # ------------------------------------------------------------------ issues
    def issue_filter(args):
        w, a = ["1=1"], []
        st = args.get("status", "open")
        if st != "all":
            w.append("s.status=?")
            a.append(st)
        for k in ("code", "severity", "source"):
            if args.get(k):
                vals = args[k].split(",")
                w.append(f"s.{k} IN ({','.join('?' * len(vals))})")
                a += vals
        if args.get("q"):
            w.append("(i.title LIKE ? OR s.message LIKE ? OR s.item_key=?)")
            a += [f"%{args['q']}%", f"%{args['q']}%", args["q"]]
        return " AND ".join(w), a

    @web.get("/api/issues")
    def issues():
        where, args = issue_filter(request.args)
        off = int(request.args.get("offset", 0))
        lim = min(500, int(request.args.get("limit", PAGE)))
        with core.store.conn() as c:
            total = c.execute(f"SELECT COUNT(*) FROM issues s LEFT JOIN items i ON i.key=s.item_key WHERE {where}", args).fetchone()[0]
            rows = c.execute(f"SELECT s.* FROM issues s LEFT JOIN items i ON i.key=s.item_key WHERE {where} "
                             f"ORDER BY CASE s.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, s.code, i.title LIMIT ? OFFSET ?",
                             args + [lim, off]).fetchall()
            codes = {r[0]: {"n": r[1], "severity": r[2], "source": r[3]} for r in c.execute(
                "SELECT code, COUNT(*), MIN(severity), MIN(source) FROM issues WHERE status=? GROUP BY code ORDER BY 2 DESC",
                (request.args.get("status", "open") if request.args.get("status", "open") != "all" else "open",))}
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d["details"] or "{}")
            it = core.store.item(d["item_key"])
            d["item"] = item_brief(it) if it else {"key": d["item_key"]}
            if it and d["code"] in ("missing_pdf", "pdf_file_missing", "pdf_not_pdf", "pdf_empty", "pdf_mismatch"):
                doi, ax = item_ids(it)
                d["links"] = {k: v for k, v in {
                    "doi": f"https://doi.org/{doi}" if doi else "",
                    "arxiv_pdf": f"https://arxiv.org/pdf/{ax}" if ax else "",
                    "unpaywall": f"https://unpaywall.org/{doi}" if doi else "",
                    "scholar": "https://scholar.google.com/scholar?q=" + re.sub(r"\s+", "+", tu.plain(it["data"].get("title", "")))[:300],
                }.items() if v}
            out.append(d)
        return jsonify({"total": total, "offset": off, "rows": out, "codes": codes})

    @web.post("/api/issues/ignore")
    def issues_ignore():
        b = body()
        core.store.ignore_issues([int(x) for x in b.get("ids", [])], bool(b.get("ignore", True)))
        return jsonify(ok=True)

    @web.post("/api/issues/tag")
    def issues_tag():
        """Propose (accepted) a tag on the items of the selected/filtered issues."""
        b = body()
        tag = (b.get("tag") or "").strip()
        if not tag:
            abort(400, "tag required")
        if b.get("ids"):
            rows = core.store.issues("id IN (%s)" % ",".join("?" * len(b["ids"])), [int(x) for x in b["ids"]])
        else:
            where, args = issue_filter(b.get("filter") or {})
            with core.store.conn() as c:
                ids = [r[0] for r in c.execute(f"SELECT s.id FROM issues s LEFT JOIN items i ON i.key=s.item_key WHERE {where}", args)]
            rows = core.store.issues("id IN (%s)" % ",".join("?" * len(ids)), ids) if ids else []
        n = add_tag_proposals(sorted({r["item_key"] for r in rows}), tag, "issue tag")
        return jsonify(ok=True, count=n)

    def add_tag_proposals(keys, tag, note):
        n = 0
        with core.store.conn() as c:
            for k in keys:
                it = core.store.item(k)
                if not it or it["itemType"] in ("note", "annotation"):
                    continue
                old = it["data"].get("tags", [])
                if any(t.get("tag") == tag for t in old):
                    continue
                new = old + [{"tag": tag}]
                fp = fingerprint(k, "tags", "tags+" + tag, new)
                if c.execute("SELECT 1 FROM proposals WHERE fingerprint=? AND status IN ('accepted','pending')", (fp,)).fetchone():
                    continue
                c.execute("INSERT INTO proposals(item_key,kind,field,old,new,rules,source,confidence,note,status,fingerprint,created,updated)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (k, "tags", "tags", jd(old), jd(new), "[]", "manual", 1.0,
                                                                f"add tag “{tag}” ({note})", "accepted", fp, now(), now()))
                n += 1
        return n

    @web.get("/api/issues/export.csv")
    def issues_csv():
        where, args = issue_filter(request.args)
        with core.store.conn() as c:
            rows = c.execute(f"SELECT s.* FROM issues s LEFT JOIN items i ON i.key=s.item_key WHERE {where} ORDER BY s.code", args).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["code", "severity", "message", "key", "title", "authors", "year", "doi", "doi_link", "zotero_link"])
        for r in rows:
            it = core.store.item(r["item_key"])
            b = item_brief(it) if it else {}
            w.writerow([r["code"], r["severity"], r["message"], r["item_key"], b.get("title_plain", ""), b.get("creators", ""),
                        b.get("year", ""), b.get("doi", ""), f"https://doi.org/{b['doi']}" if b.get("doi") else "", zlink(r["item_key"])])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=zotero-issues.csv"})

    # ------------------------------------------------------------------ duplicates
    @web.get("/api/duplicates")
    def dups():
        st = request.args.get("status", "open")
        off = int(request.args.get("offset", 0))
        groups, total = core.store.dup_groups(st, min(100, int(request.args.get("limit", 30))), off)
        out = []
        for g in groups:
            members = []
            for k in g["keys"]:
                it = core.store.item(k)
                if not it:
                    continue
                kids = core.store.items("parent=? AND deleted=0", (k,))
                npdf = sum(1 for x in kids if is_pdf_attachment(x["data"]))
                b = item_brief(it)
                b.update(n_attachments=len(kids), n_pdf=npdf, dateAdded=it["data"].get("dateAdded", ""),
                         completeness=completeness(it, len(kids)), url=it["data"].get("url", ""))
                members.append(b)
            if len(members) < 2:
                continue
            best = max(members, key=lambda m: (m["completeness"], -len(m["dateAdded"] or "")))
            for m in members:
                m["suggested_master"] = m["key"] == best["key"]
            out.append(dict(g, members=members))
        return jsonify({"total": total, "offset": off, "groups": out})

    @web.post("/api/duplicates/<int:gid>")
    def dup_action(gid):
        b = body()
        a = b.get("action")
        if a == "dismiss":
            core.store.dup_set_status(gid, "dismissed")
        elif a == "reopen":
            core.store.dup_set_status(gid, "open")
        elif a == "tag":
            with core.store.conn() as c:
                r = c.execute("SELECT keys FROM dup_groups WHERE id=?", (gid,)).fetchone()
            tag = core.settings.get("tags", "duplicate") or "_duplicate"
            add_tag_proposals(json.loads(r["keys"]), tag, "duplicate group")
            core.store.dup_set_status(gid, "tagged")
        else:
            abort(400)
        return jsonify(ok=True)

    # ------------------------------------------------------------------ commit / history
    @web.get("/api/commit/preview")
    def commit_preview():
        ps = core.store.proposals("status='accepted'", order="item_key, id", limit=int(request.args.get("limit", 300)))
        return jsonify({"summary": core.commit_summary(), "write_authorised": core.zot.key_reusable,
                        "data_dir": core.settings.get("data_dir") or core.store.meta_get("data_dir"),
                        "db_backup": core.settings.get("commit", "db_backup"),
                        "sample": [dict(prop_out(p), item=item_brief(core.store.item(p["item_key"]))) for p in ps]})

    @web.get("/api/commits")
    def commits():
        cs = core.store.commits()
        rolled = {c["parent"] for c in cs if c["kind"] == "rollback" and c["status"] != "failed"}
        for c in cs:
            c["rolled_back"] = c["id"] in rolled
        return jsonify(cs)

    @web.get("/api/commits/<int:cid>")
    def commit_detail(cid):
        rows = core.store.commit_items(cid)
        for r in rows:
            it = core.store.item(r["item_key"])
            r["item"] = item_brief(it) if it else {"key": r["item_key"]}
            r["before"] = {k: v for k, v in (r["before"] or {}).items() if k != "__full__"} | ({"itemType": r["before"]["__full__"].get("itemType")} if "__full__" in (r["before"] or {}) else {})
            r["after"] = {k: v for k, v in (r["after"] or {}).items() if k != "__full__"} | ({"itemType": r["after"]["__full__"].get("itemType")} if "__full__" in (r["after"] or {}) else {})
        return jsonify(rows)

    # ------------------------------------------------------------------ settings & tools
    @web.get("/api/settings")
    def settings_get():
        return jsonify({"settings": core.settings.data, "rules": {k: {"label": v[0], "description": v[2]} for k, v in RULES.items()},
                        "item_types": core.schema.item_types(), "backup_dir": str(BACKUP_DIR)})

    @web.post("/api/settings")
    def settings_set():
        b = body()
        if "zotero_api" in b and not re.match(r"^http://(127\.0\.0\.1|localhost):\d+/api/?$", b["zotero_api"] or ""):
            b.pop("zotero_api")  # only local Zotero instances are allowed
        core.settings.update(b)
        core.lookups = type(core.lookups)(core.settings, core.store)
        return jsonify(core.settings.data)

    @web.post("/api/tools/latex")
    def tool_latex():
        t = body().get("text", "")
        enc = latex.to_latex(t, style=core.settings.get("latex", "style"), escape_specials=core.settings.get("latex", "escape_specials"))
        return jsonify({"latex": enc, "unicode": latex.to_unicode(t), "problems": latex.check(t), "unmapped": latex.unmapped_chars(t)})

    @web.post("/api/tools/filename-preview")
    def tool_filename():
        tpl = body().get("template")
        s2 = type(core.settings)()
        s2.data = json.loads(json.dumps(core.settings.data))
        if tpl:
            s2.data["pdf"]["filename_template"] = tpl
        for k in ("ascii_filenames", "max_title_words", "max_length"):
            if k in body():
                s2.data["pdf"][k] = body()[k]
        atts = core.store.items("item_type='attachment' AND parent IS NOT NULL AND deleted=0 LIMIT 400")
        out = []
        for a in atts:
            if not is_pdf_attachment(a["data"]):
                continue
            p = core.store.item(a["parent"])
            if not p:
                continue
            cur = a["data"].get("filename") or Path(a["data"].get("path") or "").name
            out.append({"current": cur, "new": filenames.filename_for(p["data"], p.get("meta"), cur, s2)})
            if len(out) >= 8:
                break
        return jsonify(out)

    return web
