"""The knowledge graph (/api/graph): papers, cited works, notes and their links."""
from backend import references as R

from .test_references import S2_PAGE1, S2_PAGE2, Resp, router


def test_graph_combines_citations_and_notes(client, svc, monkeypatch):
    a = svc.papers.create({"title": "Citing paper", "doi": "10.1/X", "year": 2020, "citation_key": "citing2020"})
    b = svc.papers.create({"title": "Matrix product states", "doi": "10.1080/14789940801912366", "year": 2008,
                           "citation_key": "mps2008"})
    monkeypatch.setattr(R.requests, "get", router([
        ("semanticscholar", lambda url, p: Resp(S2_PAGE1 if not p.get("offset") else S2_PAGE2))]))
    client.post(f"/api/items/{a['id']}/references")
    ka = svc.papers.get(a["id"])["citation_key"]
    kb = svc.papers.get(b["id"])["citation_key"]

    def note(**kw):
        r = client.post("/api/notes/note", json=kw)
        assert r.status_code == 201, r.json
        return r.json

    def write(path, extra):
        cur = client.get("/api/notes/note", query_string={"path": path}).json
        r = client.put("/api/notes/note", json={"path": path, "text": cur["text"] + extra, "hash": cur["hash"]})
        assert r.status_code == 200, r.json

    pn = note(item_id=a["id"])                                    # paper note of a
    review = note(title="Review", folder="topics")
    other = note(title="Other")
    write(pn["path"], f"\nBuilds on [@{kb}] and [@{ka}]. See [[Review]].\n")
    write(review["path"], f"\nCompare [@{kb}; @missingKey]. Also [[Other]], [[Nowhere]] and [[#Heading]].\n")

    g = client.get("/api/graph").json
    nodes = {n["id"]: n for n in g["nodes"]}
    ia, ib = f"item:{a['id']}", f"item:{b['id']}"
    assert nodes[ia]["kind"] == "paper" and nodes[ia]["citation_key"] == ka
    assert nodes["arxiv:2101.00001"]["kind"] == "work"            # cited, not in the library
    assert nodes[f"note:{review['path']}"]["kind"] == "note"

    edges = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
    np, nr, no = f"note:{pn['path']}", f"note:{review['path']}", f"note:{other['path']}"
    assert (ia, ib, "cites") in edges and (ia, "arxiv:2101.00001", "cites") in edges
    assert (np, ia, "about") in edges
    assert (np, ia, "cite") not in edges                           # implied by "about"
    assert (np, ib, "cite") in edges and (nr, ib, "cite") in edges
    assert (np, nr, "link") in edges and (nr, no, "link") in edges
    assert not any("missingKey" in e[1] or "Nowhere" in e[1] for e in edges)   # unresolved: left out
    assert len([e for e in g["edges"] if e["source"] == nr and e["kind"] == "link"]) == 1
    assert g["stats"]["notes"] == 3 and g["stats"]["papers"] == 2 and g["stats"]["edges"]["about"] == 1
    assert g["fetched"] == [a["id"]]                               # items with a reference list


def test_link_graph_resolves_like_resolve(svc, client):
    for title, folder in (("Alpha", ""), ("Alpha", "deep/er"), ("Beta", "x")):
        client.post("/api/notes/note", json={"title": title, "folder": folder})
    path = "x/Beta.md"
    cur = client.get("/api/notes/note", query_string={"path": path}).json
    client.put("/api/notes/note", json={"path": path, "text": cur["text"] + "\n[[alpha]] [[er/Alpha]] [[Beta]]\n",
                                        "hash": cur["hash"]})
    got = {(l["target"]) for l in svc.notes.link_graph() if l["source"] == path}
    want = {svc.notes.resolve(t, from_path=path)[0] for t in ("alpha", "er/Alpha")}
    assert got == want == {"Alpha.md", "deep/er/Alpha.md"}         # the link to itself is dropped
