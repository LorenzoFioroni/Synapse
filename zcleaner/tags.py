import logging
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

log = logging.getLogger(__name__)

def discover_tags(items: list[dict], min_topic_size: int = 5, progress=None) -> dict:
    try:
        from bertopic import BERTopic
        from sentence_transformers import SentenceTransformer
        from sklearn.feature_extraction.text import CountVectorizer
    except ImportError:
        raise RuntimeError("Please install bertopic, sentence-transformers, and scikit-learn")

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
        
    log.info(f"Embedding {len(abstracts)} abstracts...")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    
    # Process embeddings in batches for progress reporting
    batch_size = 64
    embeddings_list = []
    total = len(abstracts)
    for i in range(0, total, batch_size):
        if progress:
            progress(f"Embedding abstract {i+1} to {min(i+batch_size, total)} of {total}", i, total)
        batch = abstracts[i:i+batch_size]
        batch_emb = model.encode(batch, show_progress_bar=False)
        embeddings_list.extend(batch_emb)
        
    embeddings = np.array(embeddings_list)
    
    if progress:
        progress("Clustering with BERTopic...", total, total)
    log.info("Clustering with BERTopic...")
    vectorizer_model = CountVectorizer(stop_words="english", ngram_range=(1, 3))
    topic_model = BERTopic(min_topic_size=min_topic_size, vectorizer_model=vectorizer_model)
    topics, probs = topic_model.fit_transform(abstracts, embeddings)
    
    topic_info = topic_model.get_topic_info()
    
    discovered_topics = {}
    for i, row in topic_info.iterrows():
        t_id = row["Topic"]
        if t_id == -1:
            continue
        # Get canonical tag name from top words
        words = topic_model.get_topic(t_id)
        if not words:
            continue
        tag_name = words[0][0] # use top word as canonical tag
        discovered_topics[int(t_id)] = {
            "name": tag_name,
            "words": [w[0] for w in words[:5]],
            "count": int(row["Count"])
        }
    
    # Assign tags
    tags_by_item = {}
    for i, t_id in enumerate(topics):
        if t_id != -1 and t_id in discovered_topics:
            tags_by_item[keys[i]] = discovered_topics[t_id]["name"]
            
    return {
        "topics": discovered_topics,
        "tags": tags_by_item,
        "embeddings": embeddings.tolist(),
        "keys": keys
    }

def reassign_tags(keys: list[str], embeddings: list[list[float]], curated_tags: list[str]) -> dict:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError("Please install sentence-transformers")

    if not curated_tags:
        return {}

    log.info(f"Reassigning tags based on {len(curated_tags)} curated tags...")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    tag_embeddings = model.encode(curated_tags, show_progress_bar=False)
    
    embeddings_arr = np.array(embeddings)
    similarities = cosine_similarity(embeddings_arr, tag_embeddings)
    
    tags_by_item = {}
    for i, key in enumerate(keys):
        best_tag_idx = np.argmax(similarities[i])
        best_score = similarities[i][best_tag_idx]
        if best_score >= 0.3: # threshold mentioned in the notes
            tags_by_item[key] = curated_tags[best_tag_idx]
            
    return tags_by_item
