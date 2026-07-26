import numpy as np 
from src.entities.linking import cosine_similarity 

def cluster_embedding(cluster: list[dict]) -> np.ndarray: 
    embeddings = np.stack([r['embedding'] for r in cluster])
    return embeddings.mean(axis=0)

def keyword_overlap_score(query: str, cluster: list[dict]) -> float: 
    query_words = set(query.lower().split())
    cluster_text = " ".join(f"{r['title']} {r['body']}" for r in cluster).lower() 
    cluster_words = set(cluster_text.split()) 
    if not query_words: 
        return 0.0 
    overlap = query_words & cluster_words 
    return len(overlap)/len(query_words) 

def search(
    query_embedding: np.ndarray, 
    query_text: str, 
    clusters: list[list[dict]], 
    top_k: int = 3, 
) -> list[tuple[list[dict], float]]:
    scored = [] 
    for cluster in clusters: 
        cluster_vec = cluster_embedding(cluster) 
        semantic_vec = cosine_similarity(query_embedding, cluster_vec) 
        keyword_score = keyword_overlap_score(query_text, cluster) 

        combined_score = (0.8 * semantic_vec) + (0.2 * keyword_score) 
        scored.append((cluster, combined_score)) 

    scored.sort(key=lambda x: x[1], reverse=True) 
    return scored[:top_k]


    