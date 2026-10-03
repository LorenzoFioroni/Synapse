import logging
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

log = logging.getLogger(__name__)

def reassign_tags(items: list[dict], curated_tags: list[str], threshold: float = 0.5, progress=None) -> dict:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError("Please install sentence-transformers")

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

    if progress: progress("Loading model...", 0, 100)
    log.info(f"Reassigning tags based on {len(curated_tags)} curated tags with threshold {threshold}...")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    
    if progress: progress("Embedding tags...", 10, 100)
    tag_embeddings = model.encode(curated_tags, show_progress_bar=False)
    
    batch_size = 64
    embeddings_list = []
    total = len(abstracts)
    for i in range(0, total, batch_size):
        if progress:
            progress(f"Embedding abstract {i+1} to {min(i+batch_size, total)} of {total}", 10 + int(80 * i / total), 100)
        batch = abstracts[i:i+batch_size]
        batch_emb = model.encode(batch, show_progress_bar=False)
        embeddings_list.extend(batch_emb)
        
    embeddings_arr = np.array(embeddings_list)
    
    if progress: progress("Computing similarities...", 90, 100)
    similarities = cosine_similarity(embeddings_arr, tag_embeddings)
    
    tags_by_item = {}
    for i, key in enumerate(keys):
        item_tags = []
        for j, tag in enumerate(curated_tags):
            if similarities[i][j] >= threshold:
                item_tags.append(tag)
        if item_tags:
            tags_by_item[key] = item_tags
            
    if progress: progress("Done", 100, 100)
    return tags_by_item
