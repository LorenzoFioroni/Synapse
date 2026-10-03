"""Local workspace database: library snapshot, proposals, issues, PDF checks,
lookup cache, commits/audit.  Nothing here is written to Zotero."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS items(
  key TEXT PRIMARY KEY, version INTEGER, item_type TEXT, parent TEXT, deleted INTEGER,
  title TEXT, data TEXT, meta TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS items_parent ON items(parent);
CREATE INDEX IF NOT EXISTS items_type ON items(item_type);
CREATE TABLE IF NOT EXISTS proposals(
  id INTEGER PRIMARY KEY, item_key TEXT, kind TEXT, field TEXT, old TEXT, new TEXT, edited TEXT,
  rules TEXT, source TEXT, confidence REAL, note TEXT, group_id TEXT, status TEXT,
  fingerprint TEXT, created TEXT, updated TEXT, commit_id INTEGER, message TEXT);
CREATE INDEX IF NOT EXISTS prop_item ON proposals(item_key);
CREATE INDEX IF NOT EXISTS prop_status ON proposals(status);
CREATE INDEX IF NOT EXISTS prop_fp ON proposals(fingerprint);
CREATE TABLE IF NOT EXISTS issues(
  id INTEGER PRIMARY KEY, item_key TEXT, code TEXT, severity TEXT, message TEXT, details TEXT,
  source TEXT, status TEXT DEFAULT 'open', fingerprint TEXT UNIQUE, created TEXT);
CREATE INDEX IF NOT EXISTS issues_item ON issues(item_key);
CREATE INDEX IF NOT EXISTS issues_code ON issues(code);
CREATE TABLE IF NOT EXISTS dismissals(kind TEXT, key TEXT, ts TEXT, PRIMARY KEY(kind, key));
CREATE TABLE IF NOT EXISTS dup_groups(
  id INTEGER PRIMARY KEY, keys TEXT, score REAL, reasons TEXT, status TEXT DEFAULT 'open', sig TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS pdf(
  att_key TEXT PRIMARY KEY, parent_key TEXT, path TEXT, link_mode TEXT, exists_ INTEGER, size INTEGER,
  mtime REAL, md5 TEXT, is_pdf INTEGER, pages INTEGER, has_text INTEGER, encrypted INTEGER,
  found_doi TEXT, title_score REAL, first_text TEXT, error TEXT, checked TEXT);
CREATE INDEX IF NOT EXISTS pdf_parent ON pdf(parent_key);
CREATE TABLE IF NOT EXISTS lookup_cache(k TEXT PRIMARY KEY, v TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS commits(
  id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, status TEXT, backup_dir TEXT, summary TEXT, parent INTEGER);
CREATE TABLE IF NOT EXISTS commit_items(
  id INTEGER PRIMARY KEY, commit_id INTEGER, item_key TEXT, action TEXT, before TEXT, after TEXT,
  status TEXT, message TEXT, version_after INTEGER, proposal_ids TEXT);
CREATE INDEX IF NOT EXISTS ci_commit ON commit_items(commit_id);
"""

OPEN_STATUSES = ("pending", "accepted", "stale")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def jd(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


def jl(x):
    return json.loads(x) if x not in (None, "") else None


def fingerprint(item_key, kind, field, new) -> str:
    return hashlib.sha1(jd([item_key, kind, field, new]).encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(proposals)")}
            if "base" not in cols:  # value a local rule was computed from (for convergence)
                c.execute("ALTER TABLE proposals ADD COLUMN base TEXT")

    @contextmanager
    def conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=60, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.c = c
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise

    # ------------------------------------------------------------------ meta
    def meta_get(self, k, default=None):
        with self.conn() as c:
            r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return jl(r["v"]) if r else default

    def meta_set(self, k, v):
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, jd(v)))

    # ------------------------------------------------------------------ items
    def upsert_items(self, items: list[dict]):
        rows = []
        for it in items:
            d = it.get("data", {})
            rows.append((it["key"], it.get("version", d.get("version")), d.get("itemType"),
                         d.get("parentItem") or None, 1 if d.get("deleted") else 0, d.get("title", ""),
                         jd(d), jd(it.get("meta", {})), now()))
        with self.conn() as c:
            c.executemany("INSERT OR REPLACE INTO items(key,version,item_type,parent,deleted,title,data,meta,updated)"
                          " VALUES(?,?,?,?,?,?,?,?,?)", rows)

    def delete_items(self, keys: list[str]):
        with self.conn() as c:
            c.executemany("DELETE FROM items WHERE key=?", [(k,) for k in keys])
            c.executemany("UPDATE proposals SET status='stale', message='item deleted in Zotero' "
                          "WHERE item_key=? AND status IN ('pending','accepted')", [(k,) for k in keys])

    def clear_items(self):
        with self.conn() as c:
            c.execute("DELETE FROM items")

    def item(self, key) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM items WHERE key=?", (key,)).fetchone()
        return self._item_row(r) if r else None

    @staticmethod
    def _item_row(r) -> dict:
        return {"key": r["key"], "version": r["version"], "itemType": r["item_type"], "parent": r["parent"],
                "deleted": bool(r["deleted"]), "data": jl(r["data"]), "meta": jl(r["meta"]) or {}}

    def items(self, where="1=1", args=()) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(f"SELECT * FROM items WHERE {where}", args).fetchall()
        return [self._item_row(r) for r in rows]

    def count_items(self):
        with self.conn() as c:
            r = c.execute("SELECT COUNT(*) n, SUM(item_type NOT IN ('note','attachment','annotation') AND deleted=0) regular,"
                          " SUM(item_type='attachment' AND deleted=0) att, SUM(deleted) trashed FROM items").fetchone()
        return dict(r)

    # ------------------------------------------------------------------ proposals
    def add_proposals(self, props: list[dict], sources: tuple[str, ...], item_keys: list[str] | None = None) -> dict:
        """Replace *pending* proposals of the given sources (optionally limited to
        some items) with *props*, keeping user decisions."""
        stats = {"new": 0, "kept": 0, "skipped_rejected": 0}
        ts = now()
        renames = [p for p in props if p["kind"] == "rename_file"]
        if renames:
            props = [p for p in props if p["kind"] != "rename_file"]
            self._merge_renames(renames, stats, ts)
        with self.conn() as c:
            # applied proposals are history (used for roll-back and loop prevention): never reuse them
            q = "SELECT id, fingerprint, status, edited, item_key, kind, field, old FROM proposals WHERE status<>'applied' AND source IN (%s)" % ",".join("?" * len(sources))
            args = list(sources)
            if item_keys is not None:
                if not item_keys:
                    return stats
                q += " AND item_key IN (%s)" % ",".join("?" * len(item_keys))
                args += item_keys
            existing = c.execute(q, args).fetchall()
            by_fp = {}
            for r in existing:
                by_fp.setdefault(r["fingerprint"], []).append(r)
            rejected = {r["key"] for r in c.execute("SELECT key FROM dismissals WHERE kind='proposal'")}
            new_fps = set()
            for p in props:
                fp = fingerprint(p["item_key"], p["kind"], p.get("field"), p["new"])
                new_fps.add(fp)
                if fp in rejected:
                    stats["skipped_rejected"] += 1
                    continue
                if fp in by_fp:
                    # identical proposal exists: refresh old value/meta, keep status
                    for r in by_fp[fp]:
                        # still suggested although applied/rolled back/conflicting -> needs a new decision
                        reopen = r["status"] in ("rolled_back", "stale", "applied", "failed", "conflict")
                        # a file rename you accepted that could not be done: keep it accepted
                        target = "accepted" if (r["kind"] == "rename_file" and r["status"] in ("failed", "conflict")) else "pending"
                        c.execute("UPDATE proposals SET base=? WHERE id=?", (jd(p["base"]) if "base" in p else None, r["id"]))
                        c.execute("UPDATE proposals SET old=?, rules=?, confidence=?, note=?, group_id=?, updated=?,"
                                  " status=CASE WHEN ? THEN ? ELSE status END,"
                                  " message=CASE WHEN ? THEN NULL ELSE message END WHERE id=?",
                                  (jd(p.get("old")), jd(p.get("rules", [])), p.get("confidence", 0.5), p.get("note", ""),
                                   p.get("group_id"), ts, reopen, target, reopen, r["id"]))
                    stats["kept"] += 1
                    continue
                c.execute("INSERT INTO proposals(item_key,kind,field,old,new,edited,rules,source,confidence,note,group_id,status,"
                          "fingerprint,created,updated,base) VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?)",
                          (p["item_key"], p["kind"], p.get("field"), jd(p.get("old")), jd(p["new"]), jd(p.get("rules", [])),
                           p.get("source", "rules"), p.get("confidence", 0.5), p.get("note", ""), p.get("group_id"),
                           p.get("status", "pending"), fp, ts, ts, jd(p["base"]) if "base" in p else None))
                stats["new"] += 1
            # pending proposals that are no longer generated -> remove; accepted ones -> stale
            renamed_items = {p["item_key"] for p in renames}
            for fp, rows in by_fp.items():
                if fp in new_fps:
                    continue
                for r in rows:
                    if r["kind"] == "rename_file" and r["item_key"] in renamed_items:
                        continue  # handled by _merge_renames
                    if r["status"] == "pending" and r["edited"] is None:
                        c.execute("DELETE FROM proposals WHERE id=?", (r["id"],))
                    elif r["status"] in ("pending", "accepted"):
                        c.execute("UPDATE proposals SET status='stale', message='no longer suggested (item changed?)', updated=? WHERE id=?", (ts, r["id"]))
        return stats

    def purge_trashed(self) -> tuple[int, int]:
        """Items in Zotero's trash (e.g. duplicates you merged) or deleted: their
        issues are dropped and their open proposals set aside."""
        with self.conn() as c:
            gone = "SELECT key FROM items WHERE deleted=1"
            n_iss = c.execute(f"DELETE FROM issues WHERE item_key IN ({gone}) OR item_key NOT IN (SELECT key FROM items)").rowcount
            n_prop = c.execute(f"UPDATE proposals SET status='stale', message='item is in the trash', updated=? "
                               f"WHERE status IN ('pending','accepted') AND (item_key IN ({gone}) OR item_key NOT IN (SELECT key FROM items))",
                               (now(),)).rowcount
        return n_iss, n_prop

    def is_dismissed_proposal(self, p: dict) -> bool:
        fp = fingerprint(p["item_key"], p["kind"], p.get("field"), p["new"])
        with self.conn() as c:
            return c.execute("SELECT 1 FROM dismissals WHERE kind='proposal' AND key=?", (fp,)).fetchone() is not None

    def recover_interrupted(self) -> int:
        """Proposals that failed only because of an authorisation/tooling problem
        (not because of your data) go back to 'accepted', so no review is lost."""
        with self.conn() as c:
            n = c.execute("UPDATE proposals SET status='accepted', message=NULL, commit_id=NULL, updated=? "
                          "WHERE (status='failed' AND (message LIKE '%(401)%' OR message LIKE '%(403)%' "
                          "      OR message LIKE 'Zotero accepted the upload but kept the old file name%')) "
                          "   OR (status='conflict' AND kind='rename_file' AND message LIKE 'file is now called%')",
                          (now(),)).rowcount
        return n

    def type_history(self) -> dict[str, dict]:
        """Per item: types it was moved away from by an applied change, and
        type changes you rejected — used to never propose flipping back."""
        hist: dict[str, dict] = {}
        with self.conn() as c:
            for r in c.execute("SELECT item_key, old, new, status FROM proposals WHERE kind='itemType' AND status IN ('applied','rejected')"):
                h = hist.setdefault(r["item_key"], {"left": set(), "rejected": set()})
                old, new = jl(r["old"]), jl(r["new"]) or {}
                if r["status"] == "applied":
                    h["left"].add(old)
                else:
                    h["rejected"].add(new.get("itemType"))
        return hist

    def restore_rename_decisions(self) -> int:
        """Earlier versions replaced an accepted rename by a new pending one when the
        parent's metadata changed; carry your acceptance over to the new proposal."""
        n = 0
        with self.conn() as c:
            for r in c.execute("SELECT id, item_key FROM proposals WHERE kind='rename_file' AND status='stale' AND edited IS NULL").fetchall():
                newer = c.execute("SELECT id FROM proposals WHERE kind='rename_file' AND item_key=? AND status='pending' AND id>? "
                                  "ORDER BY id DESC", (r["item_key"], r["id"])).fetchone()
                if newer:
                    c.execute("UPDATE proposals SET status='accepted', updated=? WHERE id=?", (now(), newer["id"]))
                    c.execute("DELETE FROM proposals WHERE id=?", (r["id"],))
                    n += 1
        return n

    def _merge_renames(self, renames: list[dict], stats: dict, ts: str):
        """File names are recomputed from the metadata, so a rename proposal is
        identified by its attachment, not by the proposed name: when metadata
        changes, the existing proposal is updated and keeps your decision."""
        keep_accepted = ("accepted", "failed", "conflict")
        with self.conn() as c:
            for p in renames:
                rows = c.execute("SELECT id, status, edited, fingerprint FROM proposals WHERE item_key=? AND kind='rename_file' "
                                 "AND status<>'applied' ORDER BY id DESC", (p["item_key"],)).fetchall()
                fp = fingerprint(p["item_key"], p["kind"], p.get("field"), p["new"])

                def decided(r):
                    return r["status"] in keep_accepted or (r["status"] == "stale" and r["edited"] is None) or r["status"] == "rejected"
                rows = sorted(rows, key=lambda r: (not decided(r), -r["id"]))
                if not rows:
                    c.execute("INSERT INTO proposals(item_key,kind,field,old,new,edited,rules,source,confidence,note,group_id,status,"
                              "fingerprint,created,updated) VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?)",
                              (p["item_key"], p["kind"], p.get("field"), jd(p.get("old")), jd(p["new"]), jd(p.get("rules", [])),
                               p.get("source", "pdf"), p.get("confidence", 0.9), p.get("note", ""), p.get("group_id"), "pending",
                               fp, ts, ts))
                    stats["new"] += 1
                    continue
                r = rows[0]
                if r["status"] == "rejected":
                    status = "rejected"   # you don't want this file renamed
                elif r["status"] in keep_accepted or r["status"] == "stale":
                    status = "accepted"
                else:
                    status = r["status"]
                c.execute("UPDATE proposals SET old=?, new=?, note=?, fingerprint=?, status=?, message=NULL, updated=? WHERE id=?",
                          (jd(p.get("old")), jd(p["new"]), p.get("note", ""), fp, status, ts, r["id"]))
                for extra in rows[1:]:
                    c.execute("DELETE FROM proposals WHERE id=?", (extra["id"],))
                stats["kept"] += 1

    def proposal(self, pid) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return self._prop_row(r) if r else None

    @staticmethod
    def _prop_row(r) -> dict:
        d = dict(r)
        for k in ("old", "new", "edited", "rules", "base"):
            d[k] = jl(d.get(k))
        d["value"] = d["edited"] if d["edited"] is not None else d["new"]
        return d

    def proposals(self, where="1=1", args=(), order="item_key, id", limit=None, offset=0) -> list[dict]:
        q = f"SELECT * FROM proposals WHERE {where} ORDER BY {order}"
        if limit:
            q += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        with self.conn() as c:
            return [self._prop_row(r) for r in c.execute(q, args).fetchall()]

    OPEN = ("pending", "accepted", "rejected", "superseded", "stale")

    def _expand_groups(self, c, ids: list[int], status: str) -> list[int]:
        """Proposals that only make sense together (an item-type change and the
        fields that go with it, a search match, a switch to the published
        version) are accepted/rejected/reset as a unit."""
        out, seen = [], set()
        for pid in ids:
            r = c.execute("SELECT item_key, group_id FROM proposals WHERE id=?", (pid,)).fetchone()
            members = [pid]
            if r and r["group_id"]:
                rows = c.execute("SELECT id, kind, status FROM proposals WHERE item_key=? AND group_id=?",
                                 (r["item_key"], r["group_id"])).fetchall()
                linked = r["group_id"].startswith(("pub-", "match-")) or any(x["kind"] == "itemType" for x in rows)
                if linked:
                    members += [x["id"] for x in rows if x["id"] != pid and x["status"] in self.OPEN]
            for m in members:
                if m not in seen:
                    seen.add(m)
                    out.append(m)
        return out

    def set_status(self, ids: list[int], status: str, message: str | None = None):
        ts = now()
        with self.conn() as c:
            for pid in self._expand_groups(c, ids, status):
                r = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
                if not r:
                    continue
                c.execute("UPDATE proposals SET status=?, updated=?, message=COALESCE(?, message) WHERE id=?", (status, ts, message, pid))
                if status == "rejected":
                    c.execute("INSERT OR REPLACE INTO dismissals(kind,key,ts) VALUES('proposal',?,?)", (r["fingerprint"], ts))
                elif r["status"] == "rejected":
                    c.execute("DELETE FROM dismissals WHERE kind='proposal' AND key=?", (r["fingerprint"],))
                if status == "accepted" and r["kind"] in ("field", "creators", "itemType"):
                    # only one accepted proposal per item/field
                    c.execute("UPDATE proposals SET status='superseded', updated=? WHERE item_key=? AND field=? AND kind=? "
                              "AND id<>? AND status IN ('pending','accepted')", (ts, r["item_key"], r["field"], r["kind"], pid))
                if status in ("pending", "rejected") and r["status"] == "accepted":
                    c.execute("UPDATE proposals SET status='pending', updated=? WHERE item_key=? AND field=? AND kind=? "
                              "AND id<>? AND status='superseded'", (ts, r["item_key"], r["field"], r["kind"], pid))

    def set_edited(self, pid: int, value) -> bool:
        """Store your edit. Returns True if the edit just keeps the current value
        (which means: no change wanted)."""
        with self.conn() as c:
            r = c.execute("SELECT new, old FROM proposals WHERE id=?", (pid,)).fetchone()
            if value is not None and jd(value) == r["old"]:
                c.execute("UPDATE proposals SET edited=NULL, updated=? WHERE id=?", (now(), pid))
                return True
            ed = None if value is None or jd(value) == r["new"] else jd(value)
            c.execute("UPDATE proposals SET edited=?, updated=? WHERE id=?", (ed, now(), pid))
        return False

    def resolve_noop_edits(self) -> list[int]:
        """Proposals you edited back to the current value = 'keep as is'."""
        with self.conn() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM proposals WHERE edited IS NOT NULL AND edited=old "
                                           "AND status IN ('pending','accepted','stale','superseded')")]
            c.execute("UPDATE proposals SET edited=NULL WHERE id IN (%s)" % ",".join("?" * len(ids)), ids) if ids else None
        if ids:
            self.set_status(ids, "rejected", "you kept the current value")
        return ids

    def update_proposal(self, pid, **kw):
        sets = ", ".join(f"{k}=?" for k in kw)
        vals = [jd(v) if k in ("old", "new", "edited", "rules") else v for k, v in kw.items()]
        with self.conn() as c:
            c.execute(f"UPDATE proposals SET {sets}, updated=? WHERE id=?", (*vals, now(), pid))

    # ------------------------------------------------------------------ issues
    def replace_issues(self, issues: list[dict], sources: tuple[str, ...], item_keys: list[str] | None = None):
        ts = now()
        with self.conn() as c:
            ignored = {r["key"] for r in c.execute("SELECT key FROM dismissals WHERE kind='issue'")}
            q = "DELETE FROM issues WHERE source IN (%s)" % ",".join("?" * len(sources))
            args = list(sources)
            if item_keys is not None:
                if not item_keys:
                    return
                q += " AND item_key IN (%s)" % ",".join("?" * len(item_keys))
                args += item_keys
            c.execute(q, args)
            for i in issues:
                fp = hashlib.sha1(jd([i["item_key"], i["code"], i.get("fp_extra", "")]).encode()).hexdigest()
                c.execute("INSERT OR REPLACE INTO issues(item_key,code,severity,message,details,source,status,fingerprint,created)"
                          " VALUES(?,?,?,?,?,?,?,?,?)",
                          (i["item_key"], i["code"], i.get("severity", "warning"), i["message"], jd(i.get("details", {})),
                           i.get("source", "rules"), "ignored" if fp in ignored else "open", fp, ts))

    def issues(self, where="1=1", args=(), limit=None, offset=0) -> list[dict]:
        q = f"SELECT * FROM issues WHERE {where} ORDER BY code, item_key"
        if limit:
            q += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        with self.conn() as c:
            rows = c.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = jl(d["details"]) or {}
            out.append(d)
        return out

    def ignore_issues(self, ids: list[int], ignore=True):
        with self.conn() as c:
            for i in ids:
                r = c.execute("SELECT fingerprint FROM issues WHERE id=?", (i,)).fetchone()
                if not r:
                    continue
                c.execute("UPDATE issues SET status=? WHERE id=?", ("ignored" if ignore else "open", i))
                if ignore:
                    c.execute("INSERT OR REPLACE INTO dismissals(kind,key,ts) VALUES('issue',?,?)", (r["fingerprint"], now()))
                else:
                    c.execute("DELETE FROM dismissals WHERE kind='issue' AND key=?", (r["fingerprint"],))

    # ------------------------------------------------------------------ duplicates
    def replace_dup_groups(self, groups: list[dict]):
        with self.conn() as c:
            dismissed = {r["key"] for r in c.execute("SELECT key FROM dismissals WHERE kind='dup'")}
            old = {r["sig"]: r["status"] for r in c.execute("SELECT sig, status FROM dup_groups")}
            c.execute("DELETE FROM dup_groups")
            for g in groups:
                sig = "|".join(sorted(g["keys"]))
                status = "dismissed" if sig in dismissed else old.get(sig, "open")
                c.execute("INSERT OR REPLACE INTO dup_groups(keys,score,reasons,status,sig) VALUES(?,?,?,?,?)",
                          (jd(sorted(g["keys"])), g["score"], jd(g["reasons"]), status, sig))

    def dup_groups(self, status="open", limit=50, offset=0):
        with self.conn() as c:
            rows = c.execute("SELECT * FROM dup_groups WHERE status=? ORDER BY score DESC LIMIT ? OFFSET ?",
                             (status, limit, offset)).fetchall()
            total = c.execute("SELECT COUNT(*) FROM dup_groups WHERE status=?", (status,)).fetchone()[0]
        return [dict(r, keys=jl(r["keys"]), reasons=jl(r["reasons"])) for r in rows], total

    def dup_set_status(self, gid: int, status: str):
        with self.conn() as c:
            r = c.execute("SELECT sig FROM dup_groups WHERE id=?", (gid,)).fetchone()
            if not r:
                return
            c.execute("UPDATE dup_groups SET status=? WHERE id=?", (status, gid))
            if status == "dismissed":
                c.execute("INSERT OR REPLACE INTO dismissals(kind,key,ts) VALUES('dup',?,?)", (r["sig"], now()))
            else:
                c.execute("DELETE FROM dismissals WHERE kind='dup' AND key=?", (r["sig"],))

    # ------------------------------------------------------------------ pdf
    def pdf_upsert(self, row: dict):
        cols = ["att_key", "parent_key", "path", "link_mode", "exists_", "size", "mtime", "md5", "is_pdf", "pages",
                "has_text", "encrypted", "found_doi", "title_score", "first_text", "error", "checked"]
        row = dict(row, checked=now())
        with self.conn() as c:
            c.execute(f"INSERT OR REPLACE INTO pdf({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      [row.get(k) for k in cols])

    def pdf_rows(self, where="1=1", args=()) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(f"SELECT * FROM pdf WHERE {where}", args).fetchall()]

    # ------------------------------------------------------------------ cache
    def cache_get(self, k):
        with self.conn() as c:
            r = c.execute("SELECT v FROM lookup_cache WHERE k=?", (k,)).fetchone()
        return jl(r["v"]) if r else None

    def cache_set(self, k, v):
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO lookup_cache(k,v,ts) VALUES(?,?,?)", (k, jd(v), now()))

    # ------------------------------------------------------------------ commits
    def new_commit(self, kind: str, backup_dir: str | None, parent: int | None = None) -> int:
        with self.conn() as c:
            cur = c.execute("INSERT INTO commits(ts,kind,status,backup_dir,summary,parent) VALUES(?,?,?,?,?,?)",
                            (now(), kind, "running", backup_dir, "{}", parent))
            return cur.lastrowid

    def finish_commit(self, cid: int, status: str, summary: dict):
        with self.conn() as c:
            c.execute("UPDATE commits SET status=?, summary=? WHERE id=?", (status, jd(summary), cid))

    def add_commit_item(self, cid, item_key, action, before, after, status, message="", version_after=None, proposal_ids=None):
        with self.conn() as c:
            c.execute("INSERT INTO commit_items(commit_id,item_key,action,before,after,status,message,version_after,proposal_ids)"
                      " VALUES(?,?,?,?,?,?,?,?,?)", (cid, item_key, action, jd(before), jd(after), status, message,
                                                     version_after, jd(proposal_ids or [])))

    def update_commit_item_status(self, row_id, status):
        with self.conn() as c:
            c.execute("UPDATE commit_items SET status=? WHERE id=?", (status, row_id))
            r = c.execute("SELECT proposal_ids FROM commit_items WHERE id=?", (row_id,)).fetchone()
            for pid in jl(r["proposal_ids"]) or []:
                c.execute("UPDATE proposals SET status='pending', message='rolled back — review again', commit_id=NULL, updated=? WHERE id=?", (now(), pid))

    def commits(self):
        with self.conn() as c:
            rows = c.execute("SELECT * FROM commits ORDER BY id DESC").fetchall()
        return [dict(r, summary=jl(r["summary"])) for r in rows]

    def commit_items(self, cid):
        with self.conn() as c:
            rows = c.execute("SELECT * FROM commit_items WHERE commit_id=? ORDER BY id", (cid,)).fetchall()
        return [dict(r, before=jl(r["before"]), after=jl(r["after"]), proposal_ids=jl(r["proposal_ids"])) for r in rows]
