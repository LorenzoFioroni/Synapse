import logging
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

log = logging.getLogger(__name__)

def discover_tags(items: list[dict], min_topic_size: int = 5, progress=None) -> dict:
    try:
        from sentence_transformers import SentenceTransformer
        from sklearn.feature_extraction.text import CountVectorizer
        from sklearn.cluster import AgglomerativeClustering
    except ImportError:
        raise RuntimeError("Please install sentence-transformers and scikit-learn")

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
        return {"topics": {}, "tags": {}}
        
    if progress:
        progress("Extracting keyphrases...", len(abstracts), len(abstracts))
    log.info(f"Extracting candidate keyphrases from {len(abstracts)} abstracts...")
    
    # Extract candidate bigrams and trigrams
    # min_df ensures we only get recurring phrases, filtering noise.
    vec = CountVectorizer(stop_words="english", ngram_range=(2, 3), min_df=max(2, min_topic_size - 2), max_df=0.4)
    X = vec.fit_transform(abstracts)
    phrases = vec.get_feature_names_out()
    counts = np.array(X.sum(axis=0)).flatten()
    
    if len(phrases) == 0:
        return {"topics": {}, "tags": {}, "embeddings": [], "keys": keys}
    
    if progress:
        progress(f"Embedding {len(phrases)} candidate phrases...", len(phrases), len(phrases))
        
    model = SentenceTransformer("all-MiniLM-L6-v2")
    
    # Embed the phrases
    phrase_embeddings = model.encode(phrases, show_progress_bar=False)
    
    if progress:
        progress("Clustering and merging synonyms...", len(phrases), len(phrases))
        
    # Merge synonyms (cosine distance < 0.15 means cosine similarity > 0.85)
    clusterer = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=0.15,
        metric="cosine",
        linkage="average"
    )
    clusters = clusterer.fit_predict(phrase_embeddings)
    
    discovered_topics = {}
    topic_id = 0
    for cid in set(clusters):
        cluster_indices = np.where(clusters == cid)[0]
        # Pick the most frequent phrase as the canonical tag
        best_idx = cluster_indices[np.argmax(counts[cluster_indices])]
        canonical_name = phrases[best_idx]
        total_count = int(np.sum(counts[cluster_indices]))
        synonyms = [phrases[i] for i in cluster_indices if i != best_idx]
        
        discovered_topics[topic_id] = {
            "name": canonical_name,
            "words": synonyms[:5],
            "count": total_count
        }
        topic_id += 1
        
    # We still need document embeddings for reassign_tags
    if progress:
        progress("Embedding abstracts for fast reassignment...", len(abstracts), len(abstracts))
    
    # Process document embeddings in batches
    batch_size = 64
    embeddings_list = []
    total = len(abstracts)
    for i in range(0, total, batch_size):
        batch = abstracts[i:i+batch_size]
        batch_emb = model.encode(batch, show_progress_bar=False)
        embeddings_list.extend(batch_emb)
        
    embeddings = np.array(embeddings_list)
    
    return {
        "topics": discovered_topics,
        "tags": {},  # tags will be applied via the curated Reassign step
        "embeddings": embeddings.tolist(),
        "keys": keys
    }

def reassign_tags(keys: list[str], embeddings: list[list[float]], curated_tags: list[str], threshold: float = 0.5) -> dict:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError("Please install sentence-transformers")

    if not curated_tags:
        return {}

    log.info(f"Reassigning tags based on {len(curated_tags)} curated tags with threshold {threshold}...")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    tag_embeddings = model.encode(curated_tags, show_progress_bar=False)
    
    embeddings_arr = np.array(embeddings)
    similarities = cosine_similarity(embeddings_arr, tag_embeddings)
    
    tags_by_item = {}
    for i, key in enumerate(keys):
        best_tag_idx = np.argmax(similarities[i])
        best_score = similarities[i][best_tag_idx]
        if best_score >= threshold:
            tags_by_item[key] = curated_tags[best_tag_idx]
            
    return tags_by_item
