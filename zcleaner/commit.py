"""Apply accepted proposals to Zotero (after a backup) and roll commits back.

Safety properties
* nothing is written unless a backup was created successfully;
* every item is re-read from Zotero right before writing; a proposal is only
  applied if the field still holds the value it was reviewed against
  (three-way check), otherwise it is marked as a conflict;
* writes carry the item version (Zotero rejects them if the item changed in
  the meantime);
* before/after values are recorded for every change, which makes roll-back
  possible through the API.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from pathlib import Path

from . import filenames
from .backup import make_backup
from .pdfs import resolve_path
from .store import jd
from .zotero import AuthError, ConflictError, ZoteroError

log = logging.getLogger(__name__)
WRITABLE_KINDS = ("field", "creators", "tags", "itemType")


def _same(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return (a or "") == (b or "")
    return jd(a) == jd(b)


def _tag_delta(old: list, new: list):
    o = {t.get("tag") for t in old or []}
    n = {t.get("tag") for t in new or []}
    return o - n, [t for t in new or [] if t.get("tag") not in o]


def summarize(store) -> dict:
    props = store.proposals("status='accepted'")
    by_kind = defaultdict(int)
    items = set()
    for p in props:
        by_kind[p["kind"]] += 1
        items.add(p["item_key"])
    return {"proposals": len(props), "items": len(items), "by_kind": dict(by_kind)}


class Committer:
    def __init__(self, app, progress, cancelled):
        self.app = app
        self.store = app.store
        self.zot = app.zot
        self.schema = app.schema
        self.settings = app.settings
        self.progress = progress
        self.cancelled = cancelled
        self.stats = defaultdict(int)

    # ------------------------------------------------------------------ helpers
    def _finish_props(self, ids, status, cid=None, message=None):
        for pid in ids:
            self.store.update_proposal(pid, status=status, commit_id=cid, message=message)
        self.stats[status] += len(ids)

    # ------------------------------------------------------------------ commit
    def run(self) -> dict:
        accepted = self.store.proposals("status='accepted'")
        if not accepted:
            raise RuntimeError("Nothing to apply: no accepted proposals.")
        self.zot.ping()
        self.zot.ensure_reusable_key()
        touched = sorted({p["item_key"] for p in accepted})
        parents = {self.store.item(p["item_key"])["parent"] for p in accepted if p["kind"] == "rename_file" and self.store.item(p["item_key"])}
        touched_all = sorted(set(touched) | {x for x in parents if x})
        self.progress("Creating backup …", 0, 1)
        bdir = make_backup(self.settings, self.store, self.zot, touched_all, "commit",
                           progress=lambda m: self.progress(m, 0, 1))
        cid = self.store.new_commit("commit", str(bdir))
        try:
            self._apply_items(cid, [p for p in accepted if p["kind"] in WRITABLE_KINDS])
            self._create_parents(cid, [p for p in accepted if p["kind"] == "create_parent"])
            self._renames(cid, [p for p in accepted if p["kind"] == "rename_file"])
            self._attach_pdfs(cid, [p for p in accepted if p["kind"] == "attach_pdf"])
        except Exception as e:
            self.store.finish_commit(cid, "failed", dict(self.stats, error=str(e)))
            raise
        finally:
            # refresh snapshot of everything we touched
            try:
                fresh = self.zot.get_items(touched_all)
                self.store.upsert_items(fresh)
            except Exception as e:  # pragma: no cover
                log.warning("refresh after commit failed: %s", e)
        status = "done" if not (self.stats["failed"] or self.stats["conflict"]) else "done_with_problems"
        self.store.finish_commit(cid, status, dict(self.stats, backup=str(bdir)))
        return {"commit_id": cid, "backup": str(bdir), "touched": touched_all, **self.stats}

    def _apply_items(self, cid, props):
        by_item = defaultdict(list)
        for p in props:
            by_item[p["item_key"]].append(p)
        keys = list(by_item)
        fresh = {i["key"]: i for i in self.zot.get_items(keys)} if keys else {}
        batch = []   # (key, obj, before, after, pids)
        puts = []
        for n, key in enumerate(keys):
            if self.cancelled():
                break
            self.progress(f"Preparing {n + 1}/{len(keys)}", n, len(keys))
            ps = by_item[key]
            it = fresh.get(key)
            if not it or it["data"].get("deleted"):
                self._finish_props([p["id"] for p in ps], "failed", cid, "item no longer exists (or is in the trash)")
                continue
            d = it["data"]
            work = dict(d)
            before, after, ok_ids = {}, {}, []
            type_p = [p for p in ps if p["kind"] == "itemType"]
            full_put = False
            if type_p:
                p = type_p[0]
                target = p["value"]["itemType"]
                if d.get("itemType") != p["old"]:
                    self._finish_props([p["id"]], "conflict", cid, f"item type is now {d.get('itemType')}")
                else:
                    work, dropped = self.schema.convert_type(d, target)
                    for k, v in (p["value"].get("set") or {}).items():
                        work[k] = v
                    before["__full__"] = d
                    full_put = True
                    ok_ids.append(p["id"])
            eff_type = work.get("itemType")
            for p in ps:
                if p["kind"] == "itemType":
                    continue
                f = p["field"]
                val = p["value"]
                if p["kind"] == "tags":
                    removed, added = _tag_delta(p["old"], val)
                    cur = [t for t in work.get("tags", []) if t.get("tag") not in removed]
                    names = {t.get("tag") for t in cur}
                    cur += [t for t in added if t.get("tag") not in names]
                    if _same(cur, d.get("tags", [])):
                        self._finish_props([p["id"]], "applied", cid, "already up to date")
                        continue
                    before["tags"], after["tags"], work["tags"] = d.get("tags", []), cur, cur
                    ok_ids.append(p["id"])
                    continue
                if not self.schema.has_field(eff_type, f):
                    self._finish_props([p["id"]], "conflict", cid, f"field '{f}' does not exist for type {eff_type}")
                    continue
                current = d.get(f, "" if f != "creators" else [])
                if full_put and f not in d:
                    current = work.get(f, "")  # field created by the type conversion
                if _same(current, val):
                    self._finish_props([p["id"]], "applied", cid, "already up to date")
                    continue
                if not _same(current, p["old"]) and not (full_put and _same(work.get(f), p["old"])):
                    self._finish_props([p["id"]], "conflict", cid, "the field was changed in Zotero after the scan")
                    continue
                before[f] = current
                after[f] = val
                work[f] = val
                ok_ids.append(p["id"])
            if not ok_ids:
                continue
            if full_put:
                after["__full__"] = {k: v for k, v in work.items() if k not in ("version",)}
                puts.append((key, work, before, after, ok_ids, it["version"]))
            else:
                obj = {"key": key, "version": it["version"]}
                obj.update({k: v for k, v in after.items()})
                batch.append((key, obj, before, after, ok_ids))
        # full replacements (type changes)
        for key, work, before, after, ids, version in puts:
            work = dict(work, key=key, version=version)
            try:
                v = self.zot.put_item(key, work)
                self.store.add_commit_item(cid, key, "update_type", before, after, "applied", "", v, ids)
                self._finish_props(ids, "applied", cid)
            except ConflictError as e:
                self.store.add_commit_item(cid, key, "update_type", before, after, "conflict", str(e), None, ids)
                self._finish_props(ids, "conflict", cid, str(e))
            except AuthError:
                raise  # stop the commit; proposals stay 'accepted'
            except ZoteroError as e:
                self.store.add_commit_item(cid, key, "update_type", before, after, "failed", str(e), None, ids)
                self._finish_props(ids, "failed", cid, str(e))
        # batched partial updates
        size = int(self.settings.get("commit", "batch_size") or 50)
        for i in range(0, len(batch), size):
            if self.cancelled():
                break
            chunk = batch[i:i + size]
            self.progress(f"Writing items {i + 1}–{i + len(chunk)} of {len(batch)}", i, len(batch))
            try:
                res = self.zot.post_items([c[1] for c in chunk])
            except AuthError:
                raise  # stop the commit; proposals stay 'accepted'
            except ZoteroError as e:
                if e.status in (400, 413) or e.status is None:
                    res = None
                else:
                    raise
            if res is None:  # isolate the bad object: fall back to one PATCH per item
                for key, obj, before, after, ids in chunk:
                    self._patch_one(cid, key, obj, before, after, ids)
                continue
            succ = {**(res.get("success") or {}), **{k: v.get("key") if isinstance(v, dict) else v for k, v in (res.get("successful") or {}).items()}}
            unchanged = res.get("unchanged") or {}
            failed = res.get("failed") or {}
            for idx, (key, obj, before, after, ids) in enumerate(chunk):
                s = str(idx)
                if s in succ or s in unchanged:
                    v = ((res.get("successful") or {}).get(s) or {}).get("version") if isinstance((res.get("successful") or {}).get(s), dict) else None
                    self.store.add_commit_item(cid, key, "update", before, after, "applied", "unchanged" if s in unchanged else "", v, ids)
                    self._finish_props(ids, "applied", cid)
                else:
                    f = failed.get(s, {})
                    status = "conflict" if f.get("code") == 412 else "failed"
                    msg = f"{f.get('code', '?')}: {f.get('message', 'no response for this object')}"
                    self.store.add_commit_item(cid, key, "update", before, after, status, msg, None, ids)
                    self._finish_props(ids, status, cid, msg)

    def _patch_one(self, cid, key, obj, before, after, ids):
        patch = {k: v for k, v in obj.items() if k not in ("key", "version")}
        try:
            v = self.zot.patch_item(key, obj["version"], patch)
            self.store.add_commit_item(cid, key, "update", before, after, "applied", "", v, ids)
            self._finish_props(ids, "applied", cid)
        except ConflictError as e:
            self.store.add_commit_item(cid, key, "update", before, after, "conflict", str(e), None, ids)
            self._finish_props(ids, "conflict", cid, str(e))
        except AuthError:
            raise  # stop the commit; proposals stay 'accepted'
        except ZoteroError as e:
            self.store.add_commit_item(cid, key, "update", before, after, "failed", str(e), None, ids)
            self._finish_props(ids, "failed", cid, str(e))

    # ------------------------------------------------------------------ new parents for orphan PDFs
    def _create_parents(self, cid, props):
        for p in props:
            if self.cancelled():
                break
            att_key = p["item_key"]
            try:
                att = self.zot.get_item(att_key)
                if att["data"].get("parentItem"):
                    self._finish_props([p["id"]], "conflict", cid, "attachment already has a parent")
                    continue
                data = dict(p["value"]["data"])
                data.pop("key", None)
                data.pop("version", None)
                data["collections"] = att["data"].get("collections", [])
                res = self.zot.post_items([data])
                new_key = (res.get("success") or {}).get("0") or ((res.get("successful") or {}).get("0") or {}).get("key")
                if not new_key:
                    raise ZoteroError(f"item creation failed: {json.dumps(res.get('failed'))[:300]}")
                self.zot.patch_item(att_key, att["version"], {"parentItem": new_key, "collections": []})
                self.store.add_commit_item(cid, att_key, "create_parent", {"parentItem": None, "collections": att["data"].get("collections", [])},
                                           {"parentItem": new_key, "created": new_key}, "applied", "", None, [p["id"]])
                self._finish_props([p["id"]], "applied", cid)
                try:
                    self.store.upsert_items(self.zot.get_items([new_key]))
                except Exception:
                    pass
            except AuthError:
                raise  # stop the commit; proposals stay 'accepted'
            except ZoteroError as e:
                self.store.add_commit_item(cid, att_key, "create_parent", {}, {}, "failed", str(e), None, [p["id"]])
                self._finish_props([p["id"]], "failed", cid, str(e))

    # ------------------------------------------------------------------ attach found PDFs
    def _attach_pdfs(self, cid, props):
        import shutil
        from datetime import datetime, timezone
        from .pdfs import is_pdf_attachment
        data_dir = self.settings.get("data_dir") or self.store.meta_get("data_dir")
        for n, p in enumerate(props):
            if self.cancelled():
                break
            self.progress(f"Attaching PDFs {n + 1}/{len(props)}", n, len(props))
            key, v = p["item_key"], p["value"]
            new_key = None
            try:
                parent = self.zot.get_item(key)
                if parent["data"].get("deleted"):
                    self._finish_props([p["id"]], "failed", cid, "item is in the trash")
                    continue
                kids = self.zot.children(key)
                if any(is_pdf_attachment(k["data"]) and not k["data"].get("deleted") for k in kids):
                    self._finish_props([p["id"]], "conflict", cid, "the item has a PDF by now")
                    continue
                src = Path(v["path"])
                if not src.exists():
                    self._finish_props([p["id"]], "failed", cid, "the downloaded file is gone — run 'Find PDFs' again")
                    continue
                fname = filenames.filename_for(parent["data"], parent.get("meta"), "x.pdf", self.settings)
                res = self.zot.post_items([{
                    "itemType": "attachment", "parentItem": key, "linkMode": "imported_url", "title": "Full Text PDF",
                    "url": v.get("url", ""), "accessDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "contentType": "application/pdf", "filename": fname, "tags": [], "relations": {}}])
                new_key = (res.get("success") or {}).get("0") or ((res.get("successful") or {}).get("0") or {}).get("key")
                if not new_key:
                    raise ZoteroError(f"could not create the attachment: {json.dumps(res.get('failed'))[:300]}")
                how = "upload"
                try:
                    self.zot.upload_file(new_key, src, fname, None)
                    ok = (self.zot.file_path(new_key) or Path("/nonexistent")).exists()
                except AuthError:
                    raise
                except ZoteroError:
                    ok = False
                if not ok and data_dir:  # fallback: put the file where Zotero expects it
                    dest = Path(data_dir) / "storage" / new_key / fname
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    how = "copied into Zotero storage"
                    fp = self.zot.file_path(new_key)
                    ok = bool(fp and fp.exists())
                if not ok:
                    raise ZoteroError("Zotero does not see the attached file")
                self.store.add_commit_item(cid, key, "attach", {}, {"created": new_key, "filename": fname, "how": how},
                                           "applied", how, None, [p["id"]])
                self._finish_props([p["id"]], "applied", cid)
                src.unlink(missing_ok=True)
                try:
                    self.store.upsert_items(self.zot.get_items([new_key]))
                except Exception:
                    pass
            except AuthError:
                raise
            except (ZoteroError, OSError) as e:
                if new_key:  # don't leave an empty attachment behind
                    try:
                        a = self.zot.get_item(new_key)
                        self.zot.patch_item(new_key, a["version"], {"deleted": 1})
                    except Exception:
                        pass
                self.store.add_commit_item(cid, key, "attach", {}, {}, "failed", str(e), None, [p["id"]])
                self._finish_props([p["id"]], "failed", cid, str(e))

    # ------------------------------------------------------------------ renames
    def _renames(self, cid, props):
        data_dir = self.settings.get("data_dir") or self.store.meta_get("data_dir")
        for n, p in enumerate(props):
            if self.cancelled():
                break
            self.progress(f"Renaming files {n + 1}/{len(props)}", n, len(props))
            key = p["item_key"]
            try:
                att = self.zot.get_item(key)
                d = att["data"]
                cur_name = d.get("filename") or Path(d.get("path") or "").name
                # (if the file was renamed meanwhile, e.g. by Zotero, we still normalise it: the
                #  target name comes from the template, not from the old name)
                new_name = p["value"]
                if p["edited"] is None and d.get("parentItem"):
                    parent = self.zot.get_item(d["parentItem"])  # final metadata after this commit
                    new_name = filenames.filename_for(parent["data"], parent.get("meta"), cur_name, self.settings)
                if new_name == cur_name:
                    self._finish_props([p["id"]], "applied", cid, "name already matches")
                    continue
                path = resolve_path(self.zot, data_dir, att)
                if not path or not path.exists():
                    raise ZoteroError("file not found on disk")
                before = {"filename": cur_name, "path": d.get("path"), "linkMode": d.get("linkMode")}
                after = self._rename(att, path, new_name)
                self.store.add_commit_item(cid, key, "rename", before, after, "applied", "", None, [p["id"]])
                self.store.update_proposal(p["id"], edited=new_name if new_name != p["new"] else None)
                self._finish_props([p["id"]], "applied", cid)
            except AuthError:
                raise  # stop the commit; proposals stay 'accepted'
            except (ZoteroError, OSError) as e:
                self.store.add_commit_item(cid, key, "rename", {}, {}, "failed", str(e), None, [p["id"]])
                self._finish_props([p["id"]], "failed", cid, str(e))

    def _rename(self, att: dict, path: Path, new_name: str) -> dict:
        d = att["data"]
        if d.get("linkMode") == "linked_file":
            target = path.with_name(new_name)
            k = 2
            while target.exists() and target != path:
                target = path.with_name(f"{Path(new_name).stem} ({k}){Path(new_name).suffix}")
                k += 1
            os.rename(path, target)
            old_p = d.get("path") or str(path)
            new_p = old_p[: len(old_p) - len(path.name)] + target.name if old_p.endswith(path.name) else str(target)
            try:
                self.zot.patch_item(att["key"], att["version"], {"path": new_p})
            except Exception:
                os.rename(target, path)  # undo on failure
                raise
            return {"filename": target.name, "path": new_p, "linkMode": "linked_file"}
        # Stored file (storage/<key>/<name>). Re-uploading identical bytes does not rename
        # (Zotero answers "file exists"), so: point the attachment to the new name, move the
        # file on disk, and verify that Zotero resolves the new path. Undo everything on failure.
        target = path.with_name(new_name)
        same = target.exists() and os.path.samefile(target, path)  # e.g. case-only rename on macOS
        if target.exists() and not same:
            raise ZoteroError(f"a file named {new_name} already exists in the attachment folder")
        old_name = path.name
        self.zot.patch_item(att["key"], att["version"], {"filename": new_name})
        moved = False
        try:
            chk = self.zot.get_item(att["key"])
            if chk["data"].get("filename") != new_name:
                raise ZoteroError("Zotero did not accept the new file name")
            if path.exists() and (same or not target.exists()):
                os.rename(path, target)
                moved = True
            resolved = self.zot.file_path(att["key"])
            if not resolved or not resolved.exists() or resolved.name != new_name:
                raise ZoteroError(f"after renaming, Zotero resolves the file to {resolved}")
        except Exception:
            if moved:
                os.rename(target, path)
            cur = self.zot.get_item(att["key"])
            if cur["data"].get("filename") != old_name:
                self.zot.patch_item(att["key"], cur["version"], {"filename": old_name})
            raise
        return {"filename": new_name, "path": chk["data"].get("path"), "linkMode": d.get("linkMode")}


# ----------------------------------------------------------------------
# Roll-back
# ----------------------------------------------------------------------

def rollback(app, commit_id: int, progress, cancelled) -> dict:
    store, zot = app.store, app.zot
    rows = [r for r in store.commit_items(commit_id) if r["status"] == "applied"]
    if not rows:
        raise RuntimeError("Nothing to roll back in this commit.")
    zot.ping()
    zot.ensure_reusable_key()
    keys = sorted({r["item_key"] for r in rows})
    bdir = make_backup(app.settings, store, zot, keys, f"before-rollback-{commit_id}", progress=lambda m: progress(m, 0, 1))
    cid = store.new_commit("rollback", str(bdir), parent=commit_id)
    stats = defaultdict(int)
    data_dir = app.settings.get("data_dir") or store.meta_get("data_dir")
    for n, r in enumerate(reversed(rows)):
        if cancelled():
            break
        progress(f"Reverting {n + 1}/{len(rows)}", n, len(rows))
        key = r["item_key"]
        try:
            it = zot.get_item(key)
            d = it["data"]
            if r["action"] == "update":
                patch, skipped = {}, []
                for f, v in r["after"].items():
                    if _same(d.get(f, "" if f not in ("creators", "tags") else []), v):
                        patch[f] = r["before"].get(f, "")
                    else:
                        skipped.append(f)
                if patch:
                    zot.patch_item(key, it["version"], patch)
                status = "applied" if not skipped else ("partial" if patch else "conflict")
                msg = f"changed since commit, not reverted: {', '.join(skipped)}" if skipped else ""
                store.add_commit_item(cid, key, "revert", r["after"], {f: r["before"].get(f) for f in patch}, status, msg)
            elif r["action"] == "update_type":
                full_after = r["after"].get("__full__", {})
                changed = [f for f, v in full_after.items() if f not in ("dateModified", "key") and not _same(d.get(f), v)]
                if changed:
                    store.add_commit_item(cid, key, "revert", {}, {}, "conflict", f"changed since commit: {', '.join(changed[:5])}")
                    stats["conflict"] += 1
                    continue
                old = dict(r["before"]["__full__"], version=it["version"])
                zot.put_item(key, old)
                store.add_commit_item(cid, key, "revert", {"itemType": d.get("itemType")}, {"itemType": old.get("itemType")}, "applied")
            elif r["action"] == "create_parent":
                new_key = r["after"].get("created")
                zot.patch_item(key, it["version"], {"parentItem": False, "collections": r["before"].get("collections", [])})
                if new_key:
                    parent = zot.get_item(new_key)
                    zot.patch_item(new_key, parent["version"], {"deleted": 1})
                store.add_commit_item(cid, key, "revert", r["after"], {"parentItem": None, "trashed": new_key}, "applied")
            elif r["action"] == "attach":
                new_key = (r["after"] or {}).get("created")
                if new_key:
                    a = zot.get_item(new_key)
                    zot.patch_item(new_key, a["version"], {"deleted": 1})
                store.add_commit_item(cid, key, "revert", r["after"], {"trashed": new_key}, "applied")
            elif r["action"] == "rename":
                if d.get("filename") != r["after"].get("filename") and Path(d.get("path") or "").name != r["after"].get("filename"):
                    store.add_commit_item(cid, key, "revert", {}, {}, "conflict", "file was renamed again since")
                    stats["conflict"] += 1
                    continue
                path = resolve_path(zot, data_dir, it)
                c = Committer(app, progress, cancelled)
                res = c._rename(it, path, r["before"]["filename"])
                store.add_commit_item(cid, key, "revert", r["after"], res, "applied")
            stats["applied"] += 1
            store.update_commit_item_status(r["id"], "rolled_back")
        except AuthError:
            raise  # stop the commit; proposals stay 'accepted'
        except (ZoteroError, OSError) as e:
            stats["failed"] += 1
            store.add_commit_item(cid, key, "revert", {}, {}, "failed", str(e))
    try:
        store.upsert_items(zot.get_items(keys))
    except Exception:
        pass
    store.finish_commit(cid, "done" if not stats["failed"] and not stats["conflict"] else "done_with_problems", dict(stats))
    return {"commit_id": cid, "touched": keys, **stats}
