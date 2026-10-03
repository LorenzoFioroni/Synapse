"""A small in-memory imitation of the Zotero 10 local API, for tests/demos.

python tests/mock_zotero.py --port 23999  (creates a messy demo library)
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import string
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request

SERVER_ID = "mockserver123"


def rkey():
    return "".join(random.choice("ABCDEFGHIJKLMNPQRSTUVWXYZ23456789") for _ in range(8))


class MockZotero:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        (data_dir / "storage").mkdir(parents=True, exist_ok=True)
        (data_dir / "zotero.sqlite").write_bytes(b"")
        import sqlite3
        c = sqlite3.connect(data_dir / "zotero.sqlite")
        c.execute("CREATE TABLE IF NOT EXISTS items(itemID INTEGER PRIMARY KEY)")
        c.execute("INSERT INTO items VALUES(1)")
        c.commit()
        c.close()
        self.items: dict[str, dict] = {}
        self.version = 1
        self.deleted: dict[str, int] = {}
        self.uploads = {}
        self.keys = set()
        self.single_use = set()
        self.remember = True
        self.auth_dialogs = 0
        self.lock = threading.Lock()
        self.write_log = []

    def add(self, data: dict, meta: dict | None = None) -> str:
        k = data.get("key") or rkey()
        data = dict(data, key=k, version=self.version)
        data.setdefault("tags", [])
        data.setdefault("collections", [])
        data.setdefault("relations", {})
        data.setdefault("dateAdded", "2020-01-01T00:00:00Z")
        data.setdefault("dateModified", "2020-01-01T00:00:00Z")
        if data["itemType"] not in ("attachment", "note"):
            data.setdefault("creators", [])
        self.items[k] = {"key": k, "version": self.version, "meta": meta or {}, "data": data}
        return k

    def bump(self, k):
        self.version += 1
        self.items[k]["version"] = self.version
        self.items[k]["data"]["version"] = self.version
        return self.version

    def out(self, it):
        it = copy.deepcopy(it)
        d = it["data"]
        meta = it.setdefault("meta", {})
        from zcleaner.textutil import plain  # noqa
        date = d.get("date", "")
        import re
        m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", date)
        months = {m_: i for i, m_ in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
        if m:
            meta["parsedDate"] = "-".join(x for x in m.groups() if x)
        elif date:
            y = re.search(r"\d{4}", date)
            mo = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", date.lower())
            dd = re.search(r"\b(\d{1,2})\b", date)
            if y:
                parts = [y.group(0)]
                if mo:
                    parts.append(f"{months[mo.group(1)]:02d}")
                    if dd:
                        parts.append(f"{int(dd.group(1)):02d}")
                meta["parsedDate"] = "-".join(parts)
        cr = d.get("creators") or []
        if cr:
            ln = [c.get("lastName") or c.get("name") for c in cr if c.get("creatorType") == "author"]
            meta["creatorSummary"] = ln[0] + (" et al." if len(ln) > 2 else (f" and {ln[1]}" if len(ln) == 2 else "")) if ln else ""
        return it


def build_app(mz: MockZotero) -> Flask:
    app = Flask("mockzotero")
    P = "/api/users/0"

    @app.after_request
    def h(r):
        r.headers["Zotero-Server-ID"] = SERVER_ID
        r.headers["Zotero-API-Version"] = "3"
        r.headers["Last-Modified-Version"] = str(mz.version)
        return r

    def need_write():
        if request.headers.get("Zotero-Server-ID") != SERVER_ID:
            return Response("server id", 428)
        k = request.headers.get("Zotero-API-Key")
        if k not in mz.keys:
            return Response("Invalid or expired API key", 401)
        if k in mz.single_use:  # 'Allow' (not 'Always Allow'): consumed by the first write
            mz.keys.discard(k)
        return None

    @app.get("/api/")
    def root():
        return "Nothing to see here."

    @app.get("/api/schema")
    def schema():
        return Response("not found", 404)

    @app.post("/api/local/authorize")
    def auth():
        if request.headers.get("Zotero-Server-ID") != SERVER_ID:
            return Response("server id", 428)
        k = "".join(random.choice(string.ascii_letters) for _ in range(32))
        mz.keys.add(k)
        mz.auth_dialogs += 1
        if not mz.remember:
            mz.single_use.add(k)
        return jsonify(key=k, remember=mz.remember)

    @app.get(P + "/items")
    def items():
        a = request.args
        its = list(mz.items.values())
        if a.get("since"):
            its = [i for i in its if i["version"] > int(a["since"])]
        if a.get("itemKey"):
            ks = a["itemKey"].split(",")
            its = [i for i in its if i["key"] in ks]
        if not a.get("includeTrashed") or a.get("includeTrashed") == "0":
            its = [i for i in its if not i["data"].get("deleted")]
        total = len(its)
        start, limit = int(a.get("start", 0)), int(a.get("limit", 100000))
        r = jsonify([mz.out(i) for i in its[start:start + limit]])
        r.headers["Total-Results"] = str(total)
        return r

    @app.get(P + "/items/<key>")
    def item(key):
        if key not in mz.items:
            return Response("nf", 404)
        return jsonify(mz.out(mz.items[key]))

    @app.get(P + "/deleted")
    def deleted():
        s = int(request.args.get("since", 0))
        return jsonify(items=[k for k, v in mz.deleted.items() if v > s], collections=[], searches=[], tags=[])

    def apply_patch(key, patch, version, full=False):
        it = mz.items[key]
        if version is not None and int(version) != it["version"]:
            return 412, "version mismatch"
        allowed = {"title", "date", "creators", "tags", "DOI", "url", "extra", "pages", "volume", "issue", "publicationTitle",
                   "journalAbbreviation", "ISSN", "ISBN", "language", "abstractNote", "shortTitle", "itemType", "publisher",
                   "place", "proceedingsTitle", "bookTitle", "repository", "archiveID", "parentItem", "path", "deleted",
                   "collections", "relations", "conferenceName", "university", "institution", "series", "edition",
                   "websiteTitle", "genre", "accessDate", "key", "version", "dateAdded", "dateModified", "filename",
                   "linkMode", "contentType", "md5", "mtime", "note", "charset"}
        bad = [k for k in patch if k not in allowed]
        if bad:
            return 400, f"invalid field {bad}"
        if full:
            it["data"] = {k: v for k, v in patch.items()}
            it["data"]["key"] = key
        else:
            for k, v in patch.items():
                if k in ("key", "version"):
                    continue
                if k == "parentItem" and v is False:
                    it["data"].pop("parentItem", None)
                else:
                    it["data"][k] = v
        mz.bump(key)
        mz.write_log.append((key, patch))
        return 204, ""

    @app.route(P + "/items/<key>", methods=["PATCH", "PUT"])
    def write(key):
        e = need_write()
        if e:
            return e
        if key not in mz.items:
            return Response("nf", 404)
        code, msg = apply_patch(key, request.get_json(), request.headers.get("If-Unmodified-Since-Version"), request.method == "PUT")
        r = Response(msg, code)
        return r

    @app.post(P + "/items")
    def post():
        e = need_write()
        if e:
            return e
        objs = request.get_json()
        res = {"successful": {}, "success": {}, "unchanged": {}, "failed": {}}
        for i, o in enumerate(objs):
            s = str(i)
            k = o.get("key")
            if k and k in mz.items:
                code, msg = apply_patch(k, o, o.get("version"))
                if code == 204:
                    res["success"][s] = k
                    res["successful"][s] = mz.out(mz.items[k])
                else:
                    res["failed"][s] = {"key": k, "code": code, "message": msg}
            else:
                nk = mz.add(dict(o))
                mz.bump(nk)
                res["success"][s] = nk
                res["successful"][s] = mz.out(mz.items[nk])
        return jsonify(res)

    # --- a fake publisher reached through a DOI resolver --------------------------------
    @app.get("/doi/<path:doi>")
    def doi_resolver(doi):
        from flask import redirect
        return redirect("/pub/article/" + doi.replace("/", "_"))

    @app.get("/pub/article/<slug>")
    def pub_article(slug):
        if slug.endswith("link"):      # an ordinary 'Download PDF' link (also after a passed check)
            return '<html><body><h1>Article</h1><a class="btn" href="/pub/files/a.pdf">Download PDF</a></body></html>'
        if slug.endswith("viewer"):    # the PDF lives inside a viewer frame
            return '<html><body><a href="/pub/viewer/b">View PDF</a></body></html>'
        if slug.endswith("captcha"):   # a bot check that never goes away (for the plain web tier)
            return Response('<html><head><title>Are you a robot?</title></head><body><div class="g-recaptcha"></div></body></html>', 403)
        if slug.endswith("autocheck"):  # a check that passes by itself, then shows the article
            return ('<html><head><title>Just a moment...</title></head><body><div id="challenge-form"></div>'
                    '<script>setTimeout(function(){location.href="/pub/article/after_check_link"},2500)</script></body></html>')
        if slug.endswith("jsbutton"):  # the button only exists after JavaScript runs
            return ('<html><body><div id="x"></div><script>setTimeout(function(){var b=document.createElement("button");'
                    'b.textContent="Download PDF";b.onclick=function(){location.href="/pub/files/c.pdf?download=1"};'
                    'document.getElementById("x").appendChild(b)},300)</script></body></html>')
        return "<html><body>No access</body></html>"

    @app.get("/pub/viewer/<name>")
    def pub_viewer(name):
        return f'<html><body><iframe src="/pub/files/{name}.pdf"></iframe></body></html>'

    @app.get("/pub/files/<name>")
    def pub_file(name):
        f = mz.data_dir / "web" / name
        headers = {"Content-Disposition": f"attachment; filename={name}"} if request.args.get("download") else {}
        return Response(f.read_bytes(), mimetype="application/pdf", headers=headers)

    @app.get("/web/<name>")
    def web(name):  # stands in for publisher / repository web sites
        f = mz.data_dir / "web" / name
        if not f.exists():
            return Response("nf", 404)
        return Response(f.read_bytes(), mimetype="application/pdf" if name.endswith(".pdf") else "text/html")

    @app.get(P + "/items/<key>/children")
    def children(key):
        return jsonify([mz.out(i) for i in mz.items.values() if i["data"].get("parentItem") == key])

    @app.get(P + "/items/<key>/file/view/url")
    def fileurl(key):
        it = mz.items.get(key)
        if not it or it["data"].get("itemType") != "attachment":
            return Response("nf", 404)
        d = it["data"]
        if d.get("linkMode") == "linked_file":
            return "file://" + d["path"]
        if d.get("linkMode") in ("imported_file", "imported_url"):
            return "file://" + str(mz.data_dir / "storage" / key / d.get("filename", ""))
        return Response("nf", 404)

    @app.post(P + "/items/<key>/file")
    def file_auth(key):
        e = need_write()
        if e:
            return e
        f = request.form
        if "upload" in f:
            up = mz.uploads.pop(f["upload"])
            d = mz.items[key]["data"]
            folder = mz.data_dir / "storage" / key
            folder.mkdir(exist_ok=True)
            old = folder / d.get("filename", "")
            if d.get("filename") and old.is_file():
                old.unlink()
            (folder / up["filename"]).write_bytes(up["bytes"])
            d["filename"] = up["filename"]
            d["md5"] = up["md5"]
            mz.bump(key)
            return Response("", 204)
        uk = rkey()
        mz.uploads[uk] = {"md5": f["md5"], "filename": f["filename"]}
        return jsonify(url=f"http://127.0.0.1:{request.host.split(':')[1]}/api/local/uploads/{uk}", uploadKey=uk,
                       contentType="application/pdf", prefix="", suffix="")

    @app.post("/api/local/uploads/<uk>")
    def upload(uk):
        data = request.get_data()
        up = mz.uploads[uk]
        if hashlib.md5(data).hexdigest() != up["md5"]:
            return Response("md5 mismatch", 400)
        up["bytes"] = data
        return Response("", 201)

    return app


# ----------------------------------------------------------------------
# demo library
# ----------------------------------------------------------------------

def make_pdf(path: Path, lines: list[str]):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    for ln in lines:
        c.drawString(60, y, ln)
        y -= 18
    c.showPage()
    c.save()


def A(last, first=""):
    return {"creatorType": "author", "lastName": last, "firstName": first}


def populate(mz: MockZotero):
    J = "journalArticle"
    k1 = mz.add({"itemType": J, "title": "DEEP LEARNING FOR PROTEIN STRUCTURE PREDICTION: A REVIEW OF CNN METHODS", "creators": [A("SMITH", "JOHN"), A("Doe", "J.R.")],
                 "date": "March 5, 2019", "publicationTitle": "Nature Methods", "DOI": "https://doi.org/10.1038/s41592-019-0001-1",
                 "pages": "pp. 12 - 19", "ISSN": "15487091", "language": "English", "url": "https://www.nature.com/articles/x?utm_source=feed&id=3"})
    k2 = mz.add({"itemType": J, "title": "Schrödinger equation for the α-helix: a  study of CO₂ &  H₂O.", "creators": [{"creatorType": "author", "name": "Müller, Hans"}, A("Erdős", "Pál")],
                 "date": "2020-01-15", "publicationTitle": "Physical Review Letters", "journalAbbreviation": "Phys. Rev. Lett.", "DOI": "10.1103/PhysRevLett.1.1"})
    mz.add({"itemType": J, "title": "Another PRL paper on quantum dots", "creators": [A("Lee", "K.")], "date": "2021",
            "publicationTitle": "Physical review letters", "DOI": "10.1103/PhysRevLett.2.2", "extra": "tex.foo: bar\n\n\ntex.foo: bar"})
    mz.add({"itemType": J, "title": "Synthesis of CO<sub>2</sub> in <i>E. coli</i> &amp; friends", "creators": [A("Garcia", "Maria")],
            "date": "2018", "publicationTitle": "Physical Review Letters", "extra": "DOI: 10.1000/demo.123"})
    # duplicates
    mz.add({"itemType": J, "title": "Attention is all you need", "creators": [A("Vaswani", "Ashish")], "date": "2017",
            "publicationTitle": "arXiv preprint arXiv:1706.03762", "url": "https://arxiv.org/abs/1706.03762"})
    mz.add({"itemType": "conferencePaper", "title": "Attention Is All You Need", "creators": [A("Vaswani", "A.")], "date": "2017",
            "proceedingsTitle": "Advances in Neural Information Processing Systems"})
    mz.add({"itemType": J, "title": "Quantum field theory in curved spacetime, Part I", "creators": [A("Wald", "Robert")], "date": "1990", "publicationTitle": "J. Math. Phys."})
    mz.add({"itemType": J, "title": "Quantum field theory in curved spacetime, Part II", "creators": [A("Wald", "Robert")], "date": "1991", "publicationTitle": "J. Math. Phys."})
    mz.add({"itemType": "webpage", "title": "Some article page", "creators": [], "url": "https://doi.org/10.5555/web.1", "websiteTitle": "Publisher"})
    mz.add({"itemType": J, "title": "", "creators": [], "date": ""})
    mz.add({"itemType": J, "title": "A trashed item", "creators": [A("X", "Y")], "deleted": 1})
    mz.add({"itemType": "note", "note": "<p>just a note</p>"})
    # PDFs
    p1 = mz.add({"itemType": J, "title": "Graph neural networks for chemistry", "creators": [A("Kim", "Soo")], "date": "2022",
                 "publicationTitle": "Chem. Sci.", "DOI": "10.1039/demo.gnn"})
    a1 = mz.add({"itemType": "attachment", "parentItem": p1, "linkMode": "imported_file", "title": "Full Text PDF",
                 "contentType": "application/pdf", "filename": "ssrn-12345.pdf"})
    make_pdf(mz.data_dir / "storage" / a1 / "ssrn-12345.pdf", ["Graph neural networks for chemistry", "Soo Kim", "doi:10.1039/demo.gnn"] + ["Lorem ipsum dolor sit amet"] * 10)
    mz.items[a1]["data"]["md5"] = hashlib.md5((mz.data_dir / "storage" / a1 / "ssrn-12345.pdf").read_bytes()).hexdigest()
    p2 = mz.add({"itemType": J, "title": "Paper whose PDF is a login page", "creators": [A("Roe", "Ann")], "date": "2015", "publicationTitle": "Science"})
    a2 = mz.add({"itemType": "attachment", "parentItem": p2, "linkMode": "imported_url", "contentType": "application/pdf", "filename": "fulltext.pdf", "title": "PDF"})
    (mz.data_dir / "storage" / a2).mkdir(parents=True, exist_ok=True)
    (mz.data_dir / "storage" / a2 / "fulltext.pdf").write_text("<!DOCTYPE html><html><body>Please log in</body></html>")
    p3 = mz.add({"itemType": J, "title": "Paper with missing file", "creators": [A("Poe", "Ed")], "date": "2011", "publicationTitle": "Science"})
    mz.add({"itemType": "attachment", "parentItem": p3, "linkMode": "imported_file", "contentType": "application/pdf", "filename": "gone.pdf", "title": "PDF"})
    p4 = mz.add({"itemType": J, "title": "Paper with the wrong PDF", "creators": [A("Wu", "Li")], "date": "2019", "publicationTitle": "Cell", "DOI": "10.1016/right.doi"})
    a4 = mz.add({"itemType": "attachment", "parentItem": p4, "linkMode": "linked_file", "contentType": "application/pdf",
                 "path": str(mz.data_dir / "linked" / "wu2019.pdf"), "title": "PDF"})
    make_pdf(mz.data_dir / "linked" / "wu2019.pdf", ["Completely different paper about bananas", "doi: 10.1016/other.doi"] + ["text"] * 20)
    a5 = mz.add({"itemType": "attachment", "linkMode": "imported_file", "contentType": "application/pdf", "filename": "orphan.pdf", "title": "orphan.pdf"})
    make_pdf(mz.data_dir / "storage" / a5 / "orphan.pdf", ["An orphan paper", "https://doi.org/10.1234/orphan.5"] + ["body"] * 20)
    mz.add({"itemType": J, "title": "No PDF at all here", "creators": [A("Nopdf", "N.")], "date": "2012", "publicationTitle": "Science", "DOI": "10.1126/nopdf"})
    return {"k1": k1, "k2": k2, "p1": p1, "a1": a1, "a4": a4, "a5": a5}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=23999)
    ap.add_argument("--dir", default="/tmp/mockzotero")
    a = ap.parse_args()
    import shutil
    shutil.rmtree(a.dir, ignore_errors=True)
    mz = MockZotero(Path(a.dir))
    print(json.dumps(populate(mz)))
    build_app(mz).run(port=a.port, threaded=True)
