import tarfile

import pytest


@pytest.fixture
def lib(svc, client):
    item = svc.papers.create({"authors": "Smith, Anna", "title": "Quantum things", "year": 2021})
    other = svc.papers.create({"authors": "Smith, Anna", "title": "Quantum things b", "year": 2021})
    key = item["citation_key"]                       # smithQuantum2021
    assert other["citation_key"] == key + "a"        # a key that starts with the same text
    client.post("/api/notes/note", json={"item_id": item["id"]})
    text = (f"# Review\r\n\r\nAs shown [@{key}, p. 3] and [see @{key}; @{key}a].\r\n"
            f"Code `[@{key}]` and math $[@{key}]$ and plain {key} stay.\r\n"
            f"<!-- [@{key}] -->\r\nLink [[{key}]] and [[papers/{key}|alias]] and ![[{key}#Notes]].\r\n"
            f"```\r\n[@{key}]\r\n```\r\n")
    assert client.put("/api/notes/note", json={"path": "Review.md", "text": text, "create": True}).status_code == 200
    return svc, item, other, key, text


def test_plan_writes_nothing(lib, client):
    svc, item, other, key, text = lib
    before = {r: svc.vault.read(r)[0] for r, _ in svc.vault.iter_notes()}
    p = client.get("/api/notes/rename_plan", query_string={"old": key, "new": "smith2021"}).json
    assert {n["path"] for n in p["notes"]} == {"Review.md", f"papers/{key}.md"}
    assert p["moves"] == [{"from": f"papers/{key}.md", "to": "papers/smith2021.md"}]
    review = next(n for n in p["notes"] if n["path"] == "Review.md")
    assert all("smith2021" in c["after"] for c in review["changes"])
    assert {r: svc.vault.read(r)[0] for r, _ in svc.vault.iter_notes()} == before
    assert not (svc.config.backups_dir).exists() or not list(svc.config.backups_dir.glob("notes-*"))


def test_rename_rewrites_exactly_the_references(lib, client):
    svc, item, other, key, text = lib
    client.patch(f"/api/items/{item['id']}", json={"citation_key": "smith2021"})
    review = svc.vault.read("Review.md")[0]
    expected = (text.replace(f"[@{key}, p. 3]", "[@smith2021, p. 3]")
                    .replace(f"[see @{key}; @{key}a]", f"[see @smith2021; @{key}a]")
                    .replace(f"Link [[{key}]] and [[papers/{key}|alias]] and ![[{key}#Notes]]",
                             "Link [[smith2021]] and [[papers/smith2021|alias]] and ![[smith2021#Notes]]"))
    assert review == expected                               # code, math, comment, plain text, other key untouched; CRLF kept
    assert not svc.vault.exists(f"papers/{key}.md")
    paper = svc.vault.read("papers/smith2021.md")[0]
    assert "paper: smith2021" in paper and "[@smith2021]" in paper
    # the index follows
    assert {c["path"] for c in svc.notes.citing("smith2021")} == {"Review.md", "papers/smith2021.md"}
    assert svc.notes.citing(key) == []
    assert [b["path"] for b in svc.notes.backlinks("papers/smith2021.md")] == ["Review.md"] * 3
    # a backup of the notes as they were, made before writing
    backups = list(svc.config.backups_dir.glob("notes-*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as tar:
        saved = tar.extractfile("notes/Review.md").read().decode()
    assert saved == text


def test_regenerate_also_rewrites(lib, client):
    svc, item, other, key, text = lib
    svc.papers.update(item["id"], {"title": "Different words"})
    client.post("/api/items/citekeys", json={"ids": [item["id"]]})
    newkey = svc.papers.get(item["id"])["citation_key"]
    assert newkey != key
    assert f"[@{newkey}, p. 3]" in svc.vault.read("Review.md")[0]


def test_unrelated_and_cleared_keys(lib, client):
    svc, item, other, key, text = lib
    client.patch(f"/api/items/{other['id']}", json={"citation_key": "zzz"})
    review = svc.vault.read("Review.md")[0]
    assert "[see @" + key + "; @zzz]" in review and f"[@{key}, p. 3]" in review
    client.patch(f"/api/items/{item['id']}", json={"citation_key": ""})   # cleared: notes left alone
    assert svc.vault.read("Review.md")[0] == review


def test_trashed_item_keeps_citations(lib, client):
    svc, item, other, key, text = lib
    svc.papers.trash([item["id"]])
    links = client.get("/api/notes/links", query_string={"path": "Review.md"}).json
    assert {c["key"]: c["item"]["trashed"] for c in links["cited"]}[key] is True
    assert svc.vault.read("Review.md")[0] == text
    svc.papers.restore([item["id"]])
    links = client.get("/api/notes/links", query_string={"path": "Review.md"}).json
    assert {c["key"]: c["item"]["trashed"] for c in links["cited"]}[key] is False


def test_backups_are_throttled_and_pruned(lib):
    svc, *_ = lib
    rk = svc.note_service.rekeyer
    assert rk.backup(force=True) is not None
    assert rk.backup() is None                      # within a minute: covered by the previous one
    for _ in range(25):
        rk.backup(force=True)
    assert len(list(svc.config.backups_dir.glob("notes-*.tar.gz"))) == 20



def test_first_rename_is_backed_up_even_just_after_boot(tmp_path, monkeypatch):
    """time.monotonic() counts from boot, so right after it it is below the 60 s interval."""
    from backend.notes import rekey
    vault = type("V", (), {"iter_notes": lambda self: iter(()), "root": tmp_path})()
    r = rekey.Rekeyer(vault, None, tmp_path / "backups")
    monkeypatch.setattr(rekey.time, "monotonic", lambda: 5.0)
    assert r.backup() is not None          # the first one is always made
    assert r.backup() is None              # then one per burst of renames
