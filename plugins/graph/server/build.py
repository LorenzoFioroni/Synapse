"""The knowledge graph shown in the Graph view: papers, the works they cite, and notes.

Nodes (``kind``):
  paper   an item in the library            id "item:<item id>"
  work    a cited work not in the library   id "doi:…", "arxiv:…", "openalex:…" or "ref:…"
  note    a Markdown note                   id "note:<path>"

Edges (``kind``), source → target:
  cites   paper → paper | work   (from the fetched reference lists, backend/references/service.py)
  cite    note → paper           ([@key] in the note)
  link    note → note            ([[link]] or ![[embed]])
  about   note → paper           (the paper note of an item: ``paper: key`` in its front matter)

Everything is derived from data that already exists; nothing is stored.
"""

from __future__ import annotations


def build_graph(s) -> dict:
    cg = s.citations.graph()
    items = {p["id"]: p for p in s.papers.list()}
    key_to_item = {p["citation_key"]: pid for pid, p in items.items() if p.get("citation_key")}

    nodes: list[dict] = []
    for n in cg["nodes"]:
        if n["in_library"]:
            p = items.get(n["item_id"]) or {}
            nodes.append(
                {
                    "id": n["id"],
                    "kind": "paper",
                    "item_id": n["item_id"],
                    "title": n.get("title") or "",
                    "authors": n.get("authors") or "",
                    "year": n.get("year"),
                    "doi": n.get("doi"),
                    "arxiv_id": n.get("arxiv_id"),
                    "citation_key": p.get("citation_key") or "",
                    "item_type": p.get("item_type"),
                    "folder_ids": p.get("folder_ids", []),
                    "tags": p.get("tags", []),
                    "has_pdf": bool(p.get("attachments")),
                }
            )
        else:
            nodes.append(
                {
                    "id": n["id"],
                    "kind": "work",
                    "title": n.get("title") or "",
                    "authors": n.get("authors") or "",
                    "year": n.get("year"),
                    "doi": n.get("doi"),
                    "arxiv_id": n.get("arxiv_id"),
                }
            )
    edges = [{"source": e["source"], "target": e["target"], "kind": "cites"} for e in cg["edges"]]

    for note in s.notes.list():
        nid = f"note:{note['path']}"
        nodes.append(
            {
                "id": nid,
                "kind": "note",
                "path": note["path"],
                "title": note.get("title") or _stem(note["path"]),
                "note_type": note.get("type"),
                "paper": note.get("paper"),
                "tags": note.get("tags", []),
                "updated": note.get("updated"),
            }
        )
        if note.get("paper") in key_to_item:
            edges.append({"source": nid, "target": f"item:{key_to_item[note['paper']]}", "kind": "about"})

    about = {(e["source"], e["target"]) for e in edges if e["kind"] == "about"}
    for l in s.notes.link_graph():
        src = f"note:{l['source']}"
        if l["kind"] == "note":
            edges.append({"source": src, "target": f"note:{l['target']}", "kind": "link"})
        elif l["target"] in key_to_item:
            tgt = f"item:{key_to_item[l['target']]}"
            if (src, tgt) not in about:  # a paper note citing its own paper adds nothing
                edges.append({"source": src, "target": tgt, "kind": "cite"})

    kinds: dict[str, int] = {}
    for n in nodes:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    ekinds: dict[str, int] = {}
    for e in edges:
        ekinds[e["kind"]] = ekinds.get(e["kind"], 0) + 1
    fetched = sorted(s.citations.store.fetched_ids() & set(items))
    return {
        "nodes": nodes,
        "edges": edges,
        "fetched": fetched,
        "stats": {
            "papers": kinds.get("paper", 0),
            "works": kinds.get("work", 0),
            "notes": kinds.get("note", 0),
            "edges": ekinds,
            "items_with_references": cg["stats"]["items_with_references"],
        },
    }


def _stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name
