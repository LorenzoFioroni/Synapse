import logging

log = logging.getLogger(__name__)

def reassign_tags(items: list[dict], curated_tags: list[str], threshold: float = 0.85, progress=None) -> dict:
    try:
        from transformers import pipeline
    except ImportError:
        raise RuntimeError("Please install transformers (pip install transformers)")

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

    if progress: progress("Loading zero-shot local LLM...", 0, 100)
    log.info(f"Reassigning tags based on {len(curated_tags)} curated tags with threshold {threshold}...")
    
    # Use a small NLI model for robust zero-shot text classification
    clf = pipeline("zero-shot-classification", model="typeform/distilbert-base-uncased-mnli")
    
    tags_by_item = {}
    total = len(abstracts)
    
    for i, (key, abstract) in enumerate(zip(keys, abstracts)):
        if progress:
            progress(f"Evaluating paper {i+1} of {total}...", 10 + int(90 * i / total), 100)
            
        res = clf(abstract, candidate_labels=curated_tags, multi_label=True)
        
        item_tags = []
        for label, score in zip(res["labels"], res["scores"]):
            if score >= threshold:
                item_tags.append(label)
                
        # Limit to the top 3 most relevant tags to prevent spurious associations
        if item_tags:
            tags_by_item[key] = item_tags[:3]
            
    if progress: progress("Done", 100, 100)
    return tags_by_item
