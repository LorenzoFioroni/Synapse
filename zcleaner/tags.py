import logging

log = logging.getLogger(__name__)

def reassign_tags(items: list[dict], curated_tags: list[str], threshold: float = 0.45, progress=None) -> dict:
    try:
        from sentence_transformers import SentenceTransformer, util
        import torch
    except ImportError:
        raise RuntimeError("Please install sentence-transformers (pip install sentence-transformers)")

    if not curated_tags:
        return {}

    abstracts = []
    keys = []
    
    for it in items:
        if it.get("item_type") == "attachment":
            continue
        data = it.get("data", {})
        if isinstance(data, str):
            import json
            data = json.loads(data)
        abstract = data.get("abstractNote", "").strip()
        if abstract:
            abstracts.append(abstract)
            keys.append(it["key"])

    if not abstracts:
        return {}

    if progress: progress("Loading semantic embedding model...", 0, 100)
    log.info(f"Reassigning tags based on {len(curated_tags)} curated tags with threshold {threshold}...")
    
    model = SentenceTransformer("all-MiniLM-L6-v2")
    
    tags_by_item = {}
    total = len(abstracts)
    
    if progress: progress("Computing tag embeddings...", 10, 100)
    tag_embs = model.encode(curated_tags, convert_to_tensor=True, show_progress_bar=False)
    
    batch_size = 32
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        if progress: progress(f"Evaluating papers {start+1} to {end} of {total}...", 10 + int(90 * start / total), 100)
        
        batch = abstracts[start:end]
        batch_keys = keys[start:end]
        
        doc_embs = model.encode(batch, convert_to_tensor=True, show_progress_bar=False)
        cos_scores = util.cos_sim(doc_embs, tag_embs)
        
        for i, key in enumerate(batch_keys):
            scores = cos_scores[i]
            item_tags = []
            for j, score in enumerate(scores):
                s = float(score)
                if s >= threshold:
                    item_tags.append((s, curated_tags[j]))
                    
            if item_tags:
                # Sort by score descending and limit to top 3 tags
                item_tags.sort(reverse=True)
                tags_by_item[key] = [t for s, t in item_tags[:3]]
            
    if progress: progress("Done", 100, 100)
    return tags_by_item
